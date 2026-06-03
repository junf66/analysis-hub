"""3 ソースのエッジを過剰最適化に強い基準で検証する。

backtest_* は全 cell の net 損益を出すが、(1) 多数のセルを試すと偶然 |t|>2 が出る
(偽陽性)、(2) 同日内相関で t が水増しされる、(3) in-sample だけ良い過剰最適化、
という罠がある。本スクリプトは analyzers.stats.evaluate_cells で:

  - t_clustered : 同一営業日クラスタで補正した頑健 t
  - p / FDR     : 全セルに Benjamini-Hochberg を適用し偽発見を抑制
  - walk-forward: 方向を train で決め test(OOS) の net EV を測定

「FDR 有意 かつ OOS 頑健」なセルだけが信頼できるエッジ候補。

探索スキャン (標準区分) に加え、過去に「エッジ」と名付けた PO 既知3エッジを
当時の特殊な仕掛け (9:10 利確 / 受渡日ギャップ条件) のまま再評価する監査
セクションも出す (po_named_observations)。

出力: reports/edge_validation.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator

from analyzers.stats import evaluate_cells
from scripts._buckets import disc_bucket as _disc_bucket
from scripts.analyze_holdings_edge import is_eligible_for_ev as _hold_eligible
from scripts.analyze_po_edge import GD_THRESHOLD_PCT, _is_eligible_for_ev as _po_eligible
from scripts.backtest_po import _STAGE_METRIC

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = REPO_ROOT / "reports" / "edge_validation.md"

KOUAKU_PATH = REPO_ROOT / "data" / "kouaku_records.json"
PO_PATH = REPO_ROOT / "data" / "po_records.json"
HOLDINGS_PATH = REPO_ROOT / "data" / "holdings_records.json"


def _obs(cell: Any, ret: Any, rec: dict[str, Any]) -> dict[str, Any] | None:
    if ret is None:
        return None
    return {"cell": cell, "ret": float(ret), "date": rec.get("event_date"), "code": rec.get("code")}


def kouaku_observations(records: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """好悪: (subpattern × DiscTime) cell、寄り→引け、limit-lock 除外。"""
    for r in records:
        a = r.get("attrs") or {}
        if a.get("limit_locked"):
            continue
        o = _obs((r.get("subpattern", "?"), _disc_bucket(r)), a.get("next_day_open_to_close_ret"), r)
        if o:
            yield o


def po_observations(records: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """PO: (stage × po_type × lending) cell、ステージ別 metric、eligible のみ。"""
    for r in records:
        if not _po_eligible(r):
            continue
        metric = _STAGE_METRIC.get(r.get("stage"))
        if not metric:
            continue
        cell = (r.get("stage", "?"), r.get("po_type") or "?", r.get("lending_type") or "?")
        o = _obs(cell, (r.get("attrs") or {}).get(metric), r)
        if o:
            yield o


def po_named_observations(records: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """既知3エッジを「当時の定義 (特殊な仕掛け含む)」のまま検証する監査用。

    探索スキャン (po_observations) は標準区分 (stage×po_type×lending) のみで、
    ①9:10利確 ②受渡日 GD のギャップ条件 を再現しない。ここでは当時の仕掛けを
    そのまま 3 セルとして評価し、過剰最適化ガード後も生き残るかをフェアに判定する。
    3 セル独立の FDR (= 事前登録 3 仮説の補正) になる。
    """
    for r in records:
        if not _po_eligible(r):
            continue
        stage, po_type = r.get("stage"), r.get("po_type")
        a = r.get("attrs") or {}
        if stage == "announce" and po_type == "普通":
            o = _obs("① 発表翌日 普通 (翌寄り→09:10 long)", a.get("next_day_910_ret"), r)
        elif stage == "deliver" and po_type == "普通":
            gap = a.get("gap_pct")
            if gap is None or float(gap) > GD_THRESHOLD_PCT:
                continue  # GD 条件 (gap<=-0.5%) を満たすもののみ
            o = _obs(f"② 受渡日GD 普通 (gap≤{GD_THRESHOLD_PCT}% 寄→引 long)",
                     a.get("next_day_open_to_close_ret"), r)
        elif stage == "decide" and po_type == "リート":
            o = _obs("③ 決定 リート (翌寄り→決定引 short)", a.get("ret_close"), r)
        else:
            continue
        if o:
            yield o


def holdings_observations(records: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """大量保有: (purpose × holder) cell、寄り→引け、low_ratio_suspect 除外。"""
    for r in records:
        if not _hold_eligible(r):
            continue
        cell = (r.get("purpose_category_jp") or "?", r.get("holder_category_jp") or "?")
        o = _obs(cell, (r.get("attrs") or {}).get("next_day_open_to_close_ret"), r)
        if o:
            yield o


_MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
_PO_ENR_PATH = REPO_ROOT / "data" / "edge_candidates" / "po_enriched.json"
_MILD_PATH = REPO_ROOT / "data" / "edge_candidates" / "mild_good.json"


def _primary_mag(r: dict[str, Any]) -> float | None:
    for fac in (r.get("bad_factors") or []) + (r.get("good_factors") or []):
        for k, v in (fac.get("metric") or {}).items():
            if isinstance(v, (int, float)) and "pct" in k.lower():
                return float(v)
    return None


def new_edges_observations(_ignored: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """発見済み新エッジを事前登録 named cell で検証する (公式データ拡張)。

    複数データ源 (kouaku 程度別 / PO 規模別 / mild_good 軽い減益×増配 / 受渡日ロング) を跨ぐため、
    渡された records は無視し各ファイルを直接読む。事前登録仮説独立の FDR + walk-forward OOS。
    """
    # ① kouaku 程度別 (大引け後)
    for r in json.loads(KOUAKU_PATH.read_text()).get("records", []):
        a = r.get("attrs") or {}
        if a.get("limit_locked") or _disc_bucket(r) != "大引け後":
            continue
        sp, mag, ret = r.get("subpattern"), _primary_mag(r), a.get("next_day_open_to_close_ret")
        if mag is None or ret is None:
            continue
        if sp == "kouhou_nx_genshu" and mag <= -10:
            o = _obs("kouhou_nx_genshu×大引け後×深減益(NP≤-10%) short", ret, r)
            if o:
                yield o
        elif sp == "zouhai_kahou_nx" and -30 <= mag <= -17:
            o = _obs("zouhai_kahou_nx×大引け後×中magnitude(-30〜-17%) short", ret, r)
            if o:
                yield o
    # ② PO 中型(Mid400) × 翌日GD × 引け (master 規模 + po_enriched 引けリターン)
    if _MASTER_PATH.exists() and _PO_ENR_PATH.exists():
        scale = {m["Code"]: m.get("scale_band")
                 for m in json.loads(_MASTER_PATH.read_text()).get("records", [])}
        enr = json.loads(_PO_ENR_PATH.read_text()).get("by_id", {})
        for r in json.loads(PO_PATH.read_text()).get("records", []):
            if r.get("stage") != "announce" or r.get("po_type") != "普通":
                continue
            a = r.get("attrs") or {}
            code5 = r["code"] + "0" if len(r["code"]) == 4 else r["code"]
            gap = a.get("gap_pct")
            oc = (enr.get(r["id"]) or {}).get("next_day_open_to_close_ret")
            if scale.get(code5) == "中型" and gap is not None and gap <= -0.5 and oc is not None:
                o = _obs("PO中型×翌日GD×引け long", oc, r)
                if o:
                    yield o
    # ③ 増配 × 軽い減益(3%未満) × +3日α (mild_good)
    if _MILD_PATH.exists():
        for r in json.loads(_MILD_PATH.read_text()).get("records", []):
            a = r.get("attrs") or {}
            y = a.get("np_yoy")
            if y is not None and -3 <= y < 0 and "zouhai" in a.get("goods", []):
                o = _obs("増配×軽い減益(3%未満)×+3日α short", a.get("alpha_d3_ret"), r)
                if o:
                    yield o
    # ④ 受渡日ロング (PO規模主軸): deliver×普通×GD+フラット(gap<0.5%)×PO規模≥300億 寄→引 long
    #   絶対調達額が大きいほど受渡し前後の機械的売り圧の反動を取れる (規模割合では効かない)。
    for r in json.loads(PO_PATH.read_text()).get("records", []):
        if r.get("stage") != "deliver" or r.get("po_type") != "普通":
            continue
        a = r.get("attrs") or {}
        gap = a.get("gap_pct")
        sc = r.get("po_scale")
        if gap is None or float(gap) >= 0.5:   # GU除外 (= GD+フラット)
            continue
        if not sc or float(sc) < 300:          # PO規模(絶対額) ≥300億
            continue
        o = _obs("受渡日×GD+フラット×PO規模≥300億 寄→引 long", a.get("next_day_open_to_close_ret"), r)
        if o:
            yield o


_SOURCES = {
    "kouaku": (KOUAKU_PATH, kouaku_observations),
    "po": (PO_PATH, po_observations),
    "po (既知3エッジ監査・当時定義)": (PO_PATH, po_named_observations),
    "新エッジ (事前登録・公式データ拡張)": (KOUAKU_PATH, new_edges_observations),
    "holdings": (HOLDINGS_PATH, holdings_observations),
}


def _fmt(v: float | None, suffix: str = "%") -> str:
    return f"{v:+.2f}{suffix}" if v is not None else "–"


def _section(name: str, results: list[dict[str, Any]], *, min_n: int) -> list[str]:
    lines = [f"## {name}", ""]
    if not results:
        lines += [f"(データなし or n>={min_n} のセルなし)", ""]
        return lines
    survivors = [r for r in results if r["fdr_significant"] and r.get("robust_oos")]
    lines.append(f"検証セル数 {len(results)} / FDR 有意 {sum(r['fdr_significant'] for r in results)} "
                 f"/ **FDR有意 かつ OOS頑健 {len(survivors)}**")
    lines.append("")
    lines.append("| cell | dir | cost | n | EV(net) | t | t_clust | p | FDR | OOS(test EV) | 信頼 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        cell = " × ".join(str(x) for x in r["cell"]) if isinstance(r["cell"], tuple) else str(r["cell"])
        trust = "✅" if (r["fdr_significant"] and r.get("robust_oos")) else ""
        lines.append(
            f"| {cell} | {r['direction']} | {r.get('cost', 0):.2f}% | {r['n']} | {_fmt(r['ev_net'])} | "
            f"{r['t']:+.2f} | {r['t_clustered']:+.2f} | {r['p']:.4f} | "
            f"{'✓' if r['fdr_significant'] else ''} | {_fmt(r.get('test_ev_net'))} | {trust} |"
        )
    lines.append("")
    return lines


def build_report(*, long_cost: float, short_cost: float, alpha: float,
                 split_frac: float, min_n: int) -> str:
    """3 ソースを evaluate_cells で検証し、FDR + OOS 付きの md レポート文字列を返す。"""
    lines = ["# エッジ検証 (過剰最適化ガード付き)", ""]
    lines.append(f"往復コスト 方向別 (long {long_cost:.2f}% / short {short_cost:.2f}%) "
                 f"/ FDR α={alpha} / walk-forward 分割={split_frac:.0%} / min_n={min_n}")
    lines.append("")
    lines.append("コスト前提: ショート=楽天 手数料0・逆日歩無視で寄りの滑りのみ、"
                 "ロング=日興手数料込み安全側。")
    lines.append("t_clust=日付クラスタ頑健 t、p は t_clust 由来。FDR=Benjamini-Hochberg 生存。")
    lines.append("OOS=方向を train で決め test 区間で測った net EV。**信頼✅=FDR有意かつOOS頑健**。")
    lines.append("")
    for name, (path, adapter) in _SOURCES.items():
        if not path.exists():
            lines += [f"## {name}", "", f"(skip: {path.name} 未生成)", ""]
            continue
        records = json.loads(path.read_text()).get("records", [])
        results = evaluate_cells(list(adapter(records)), long_cost=long_cost, short_cost=short_cost,
                                 alpha=alpha, split_frac=split_frac, min_n=min_n)
        lines += _section(name, results, min_n=min_n)
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPORT_PATH, help="出力 md ファイル")
    ap.add_argument("--long-cost", type=float, default=0.20,
                    help="ロング往復コスト %% (既定 0.20、日興手数料込み安全側)")
    ap.add_argument("--short-cost", type=float, default=0.15,
                    help="ショート往復コスト %% (既定 0.15、楽天 手数料0・逆日歩無視 滑りのみ)")
    ap.add_argument("--alpha", type=float, default=0.05, help="FDR の α (既定 0.05)")
    ap.add_argument("--split", type=float, default=0.7, help="walk-forward の train 割合 (既定 0.7)")
    ap.add_argument("--min-n", type=int, default=30, help="検証する最小セル n (既定 30)")
    args = ap.parse_args()

    report = build_report(long_cost=args.long_cost, short_cost=args.short_cost,
                          alpha=args.alpha, split_frac=args.split, min_n=args.min_n)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

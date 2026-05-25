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


_SOURCES = {
    "kouaku": (KOUAKU_PATH, kouaku_observations),
    "po": (PO_PATH, po_observations),
    "po (既知3エッジ監査・当時定義)": (PO_PATH, po_named_observations),
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
    lines.append("| cell | dir | n | EV(net) | t | t_clust | p | FDR | OOS(test EV) | 信頼 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        cell = " × ".join(str(x) for x in r["cell"]) if isinstance(r["cell"], tuple) else str(r["cell"])
        trust = "✅" if (r["fdr_significant"] and r.get("robust_oos")) else ""
        lines.append(
            f"| {cell} | {r['direction']} | {r['n']} | {_fmt(r['ev_net'])} | "
            f"{r['t']:+.2f} | {r['t_clustered']:+.2f} | {r['p']:.4f} | "
            f"{'✓' if r['fdr_significant'] else ''} | {_fmt(r.get('test_ev_net'))} | {trust} |"
        )
    lines.append("")
    return lines


def build_report(*, cost_pct: float, alpha: float, split_frac: float, min_n: int) -> str:
    """3 ソースを evaluate_cells で検証し、FDR + OOS 付きの md レポート文字列を返す。"""
    lines = ["# エッジ検証 (過剰最適化ガード付き)", ""]
    lines.append(f"往復コスト {cost_pct:.2f}% / FDR α={alpha} / walk-forward 分割={split_frac:.0%} / min_n={min_n}")
    lines.append("")
    lines.append("t_clust=日付クラスタ頑健 t、p は t_clust 由来。FDR=Benjamini-Hochberg 生存。")
    lines.append("OOS=方向を train で決め test 区間で測った net EV。**信頼✅=FDR有意かつOOS頑健**。")
    lines.append("")
    for name, (path, adapter) in _SOURCES.items():
        if not path.exists():
            lines += [f"## {name}", "", f"(skip: {path.name} 未生成)", ""]
            continue
        records = json.loads(path.read_text()).get("records", [])
        results = evaluate_cells(list(adapter(records)), cost_pct=cost_pct,
                                 alpha=alpha, split_frac=split_frac, min_n=min_n)
        lines += _section(name, results, min_n=min_n)
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPORT_PATH, help="出力 md ファイル")
    ap.add_argument("--cost", type=float, default=0.20, help="往復コスト %% (既定 0.20)")
    ap.add_argument("--alpha", type=float, default=0.05, help="FDR の α (既定 0.05)")
    ap.add_argument("--split", type=float, default=0.7, help="walk-forward の train 割合 (既定 0.7)")
    ap.add_argument("--min-n", type=int, default=30, help="検証する最小セル n (既定 30)")
    args = ap.parse_args()

    report = build_report(cost_pct=args.cost, alpha=args.alpha, split_frac=args.split, min_n=args.min_n)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

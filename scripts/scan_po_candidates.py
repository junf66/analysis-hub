"""PO Tracker の全次元を自動総当たりして期待値プラスの候補セルを抽出する。

思想: 人間が PO Tracker のフィルターを手で回して良い組合せを探す代わりに、
(取引シグナル × 分析軸 × バケット) の全セルを機械的に評価し、過剰最適化ガード
(方向別コスト + 日付クラスタ頑健t + walk-forward OOS + 全セル横断 BH-FDR) を
通した上で「期待値ありそうな候補」を一覧化する。analyzers.stats.evaluate_cells に乗せる。

シグナル(取引): 各ステージの tradeable リターン。方向(long/short)は生EV符号で自動判定。
分析軸: po_type / 規模band / 時価総額 / PO規模 / 希薄化 / 受渡(翌日)gap / 信用区分 / 業種。

出力: reports/po_candidate_scan.md
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any, Callable

from analyzers.stats import evaluate_cells

REPO_ROOT = Path(__file__).resolve().parent.parent
PO_PATH = REPO_ROOT / "data" / "po_records.json"
ENRICHED_PATH = REPO_ROOT / "data" / "edge_candidates" / "po_enriched.json"
REPORT_PATH = REPO_ROOT / "reports" / "po_candidate_scan.md"

LONG_COST = 0.20
SHORT_COST = 0.15
MIN_N = 30           # セル最小サンプル
TC_CANDIDATE = 1.5   # 候補として拾う t_clustered の下限

# 取引シグナル: (表示名, stage, リターンの在処, フィールド)。'attrs'/'enriched' で参照先を切替。
SIGNALS: list[dict[str, str]] = [
    {"name": "受渡日 寄→引", "stage": "deliver", "src": "attrs", "field": "next_day_open_to_close_ret"},
    {"name": "発表翌日 引け", "stage": "announce", "src": "enriched", "field": "next_day_open_to_close_ret"},
    {"name": "発表翌日 9:10", "stage": "announce", "src": "attrs", "field": "next_day_910_ret"},
    {"name": "発表翌日 9:30", "stage": "announce", "src": "attrs", "field": "next_day_930_ret"},
    {"name": "決定 寄→決定引け", "stage": "decide", "src": "attrs", "field": "ret_close"},
]


def _mc_bucket(mc: float) -> str:
    for lo, hi, lab in [(0, 500, "時価≤500億"), (500, 1000, "時価500-1000億"),
                        (1000, 3000, "時価1000-3000億"), (3000, 10000, "時価3000億-1兆"),
                        (10000, float("inf"), "時価≥1兆")]:
        if lo <= mc < hi:
            return lab
    return "時価?"


def _scale_bucket(sc: float) -> str:
    for lo, hi, lab in [(0, 50, "PO規模<50億"), (50, 100, "PO規模50-100億"),
                        (100, 300, "PO規模100-300億"), (300, 1000, "PO規模300-1000億"),
                        (1000, float("inf"), "PO規模≥1000億")]:
        if lo <= sc < hi:
            return lab
    return "PO規模?"


def _dil_bucket(d: float) -> str:
    for lo, hi, lab in [(-1, 3, "希薄化<3%"), (3, 5, "希薄化3-5%"), (5, 10, "希薄化5-10%"),
                        (10, 20, "希薄化10-20%"), (20, float("inf"), "希薄化≥20%")]:
        if lo <= d < hi:
            return lab
    return "希薄化?"


def _gap_regime(gap: float) -> str:
    if gap <= -0.5:
        return "gap:GD(≤-0.5%)"
    if gap < 0.5:
        return "gap:フラット"
    return "gap:GU(≥0.5%)"


# 分析軸: 名前 → record/enriched からバケットラベルを作る関数 (None で除外)
AXES: dict[str, Callable[[dict[str, Any], dict[str, Any]], str | None]] = {
    "po_type": lambda r, e: f"種別:{r.get('po_type')}" if r.get("po_type") else None,
    "規模band": lambda r, e: f"規模:{e.get('scale_band')}" if e.get("scale_band") else None,
    "時価総額": lambda r, e: _mc_bucket(float(r["market_cap"])) if r.get("market_cap") else None,
    "PO規模": lambda r, e: _scale_bucket(float(r["po_scale"])) if r.get("po_scale") else None,
    "希薄化": lambda r, e: _dil_bucket(float(r["dilution"])) if r.get("dilution") is not None else None,
    "gap": lambda r, e: _gap_regime(float((r.get("attrs") or {})["gap_pct"]))
    if (r.get("attrs") or {}).get("gap_pct") is not None else None,
    "信用区分": lambda r, e: f"信用:{r.get('lending_type')}" if r.get("lending_type") else None,
    "業種": lambda r, e: f"業種:{r.get('sector17')}" if r.get("sector17") else None,
}


def load_records() -> list[dict[str, Any]]:
    """po_records を返す。"""
    data = json.loads(PO_PATH.read_text())
    return data.get("records", []) if isinstance(data, dict) else data


def load_enriched() -> dict[str, dict[str, Any]]:
    """id → enriched attrs。無ければ空。"""
    if not ENRICHED_PATH.exists():
        return {}
    return json.loads(ENRICHED_PATH.read_text()).get("by_id", {})


def signal_ret(record: dict[str, Any], enriched_entry: dict[str, Any],
               sig: dict[str, str]) -> float | None:
    """シグナル定義に従いリターンを取り出す。"""
    if sig["src"] == "enriched":
        val = enriched_entry.get(sig["field"])
    else:
        val = (record.get("attrs") or {}).get(sig["field"])
    return float(val) if val is not None else None


def build_observations(records: list[dict[str, Any]],
                       enriched: dict[str, dict[str, Any]],
                       max_combo: int = 2, since: str | None = None) -> list[dict[str, Any]]:
    """(シグナル × 軸の組合せ) を cell とする観測リストを作る。

    max_combo=2 で単一軸に加え2軸の掛け合わせ(=トラッカーで複数フィルタを併用)も総当たり。
    since (ISO date) を渡すと event_date≥since のレコードのみに絞る (期間限定スキャン)。
    cell = (シグナル名, (条件ラベル, ...))。
    """
    obs: list[dict[str, Any]] = []
    for r in records:
        stage = r.get("stage")
        e = enriched.get(r.get("id", "")) or {}
        date = r.get("event_date")
        code = r.get("code")
        if since and (not date or date < since):
            continue
        for sig in SIGNALS:
            if stage != sig["stage"]:
                continue
            ret = signal_ret(r, e, sig)
            if ret is None:
                continue
            # この record で有効な (軸, ラベル) を集める
            active = [fn(r, e) for fn in AXES.values()]
            active = [lab for lab in active if lab is not None]
            # 全体ベースライン
            obs.append({"cell": (sig["name"], ("全体",)), "ret": ret, "date": date, "code": code})
            # 1軸〜max_combo軸の組合せ
            for k in range(1, max_combo + 1):
                for combo in itertools.combinations(active, k):
                    obs.append({"cell": (sig["name"], combo), "ret": ret,
                                "date": date, "code": code})
    return obs


def scan(records: list[dict[str, Any]],
         enriched: dict[str, dict[str, Any]], since: str | None = None) -> list[dict[str, Any]]:
    """全セルを evaluate_cells で評価し、候補(ev_net>0 かつ t_clustered≥下限)を返す。"""
    obs = build_observations(records, enriched, since=since)
    results = evaluate_cells(obs, long_cost=LONG_COST, short_cost=SHORT_COST, min_n=MIN_N)
    cands = [r for r in results if r["ev_net"] > 0 and r["t_clustered"] >= TC_CANDIDATE]
    cands.sort(key=lambda r: r["t_clustered"], reverse=True)
    return cands


def build_report(records: list[dict[str, Any]],
                 enriched: dict[str, dict[str, Any]]) -> str:
    """候補スキャン結果レポートを生成。"""
    L: list[str] = []
    L.append("# PO候補スキャン ── 全次元 自動総当たり (2026-06-03)")
    L.append("")
    L.append(f"取引シグナル {len(SIGNALS)}種 × 分析軸 {len(AXES)}種 のセルを機械評価。")
    L.append(f"方向(long/short)は生EV符号で自動判定。コスト long{LONG_COST}%/short{SHORT_COST}%、")
    L.append(f"セル最小n={MIN_N}、日付クラスタ頑健t、walk-forward OOS、全セル横断BH-FDR。")
    L.append(f"**候補条件**: net EV>0 かつ t_clustered≥{TC_CANDIDATE}（完璧でなくとも芽のあるものを拾う）。")
    L.append("")
    cands = scan(records, enriched)
    if not cands:
        L.append("_(候補なし)_")
        return "\n".join(L)
    top = cands[:40]
    L.append(f"## 候補一覧（{len(cands)}件中 上位{len(top)} / t_clust 降順）")
    L.append("")
    L.append("| シグナル | 条件（軸の掛け合わせ） | 方向 | n | net EV | t_clust | OOS test | FDR★ |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in top:
        sig, combo = r["cell"]
        cell_disp = " & ".join(combo)
        oos = r.get("test_ev_net")
        oos_disp = f"{oos:+.2f}%" if oos is not None else "—"
        fdr = "★" if r.get("fdr_significant") else ""
        L.append(f"| {sig} | {cell_disp} | {r['direction']} | {r['n']} | "
                 f"{r['ev_net']:+.2f}% | {r['t_clustered']:+.2f} | {oos_disp} | {fdr} |")
    L.append("")
    n_fdr = sum(1 for r in cands if r.get("fdr_significant"))
    n_oos = sum(1 for r in cands if r.get("robust_oos"))
    L.append("## 読み方")
    L.append("")
    L.append(f"- **FDR★ ({n_fdr}件)**: 全セル横断の多重検定補正を生存＝最も信頼できる確定級。")
    L.append(f"- **OOS test プラス ({n_oos}件)**: 後半データでも net 正＝過剰最適化の疑い低。")
    L.append("- FDR★が付かない候補も『芽』として残す（n待ち・要追検証）。単一軸の breakdown なので、")
    L.append("  複数軸を重ねると更に厚くなる可能性（ただし重ねるほど過剰最適化リスク↑）。")
    L.append("- 確定採用は edge_playbook.md の正本へ。本スキャンは候補出しの一次フィルタ。")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> None:
    """CLI: --since YYYY-MM-DD で期間限定スキャン (健全性チェック用)。"""
    import argparse
    ap = argparse.ArgumentParser(description="PO候補スキャン (全次元総当たり)")
    ap.add_argument("--since", default=None, help="event_date≥SINCE のみ (例: 2024-06-03)")
    args = ap.parse_args(argv)
    records = load_records()
    enriched = load_enriched()
    cands = scan(records, enriched, since=args.since)
    scope = f"直近(≥{args.since})" if args.since else "全期間"
    print(f"[{scope}] 候補 {len(cands)}件 / FDR★ {sum(1 for c in cands if c['fdr_significant'])}件")
    if args.since is None:
        REPORT_PATH.write_text(build_report(records, enriched))
        print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()

"""data/holdings_records.json から大量保有セル (purpose × holder) の net バックテスト生成。

戦略: 主要 metric (寄り→引け = next_day_open_to_close_ret) を用い、cell の生 EV 符号で
方向を決める (負なら寄り売り→引け買戻、正なら寄り買い→引け売り)。
往復コスト COST_PCT を引いて純損益。累積は単純加算 (複利なし、1 単位ベット想定)。

backtest_kouaku / backtest_po と同一の net 計算定義 (_net_pnl / _stat_block を流用)。

EV 評価から除外: low_ratio_suspect。

出力: reports/holdings_backtest.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.analyze_holdings_edge import is_eligible_for_ev
from scripts.backtest_po import _net_pnl, _stat_block

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = REPO_ROOT / "data" / "holdings_records.json"
REPORT_PATH = REPO_ROOT / "reports" / "holdings_backtest.md"

COST_PCT_DEFAULT = 0.20
MIN_CELL_N = 5
PRIMARY_METRIC = "next_day_open_to_close_ret"


def _cell_key(rec: dict[str, Any]) -> tuple[str, str]:
    return (rec.get("purpose_category_jp") or "?", rec.get("holder_category_jp") or "?")


def build_report(records: list[dict[str, Any]], cost_pct: float) -> str:
    """purpose × holder セルの net 損益 + 強度ランキング + 有意セル年度別を md で返す。"""
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if not is_eligible_for_ev(r):
            continue
        cells[_cell_key(r)].append(r)

    lines: list[str] = []
    lines.append("# 大量保有 バックテスト (寄り→引け 戦略)")
    lines.append("")
    lines.append(f"往復コスト: **{cost_pct:.2f}%** / 1 ベット 1 単位均等 (複利なし) / low_ratio_suspect 除外")
    lines.append("")
    lines.append(f"metric: {PRIMARY_METRIC}。方向は cell の生 EV 符号に従う (負→寄り売り、正→寄り買い)。")
    lines.append("")
    lines.append("| purpose | holder | direction | n | EV(net) | t | win% | cumul(net) |")
    lines.append("|---|---|---|---|---|---|---|---|")

    rows: list[tuple[tuple[str, str], str, dict[str, float], list[dict[str, Any]]]] = []
    for key, recs in sorted(cells.items()):
        rets = [(r.get("attrs") or {}).get(PRIMARY_METRIC) for r in recs]
        rets = [float(v) for v in rets if v is not None]
        if len(rets) < MIN_CELL_N:
            continue
        raw_ev = statistics.fmean(rets)
        direction = "short" if raw_ev < 0 else "long"
        nets = [_net_pnl(v, cost_pct, direction) for v in rets]
        s = _stat_block(nets)
        rows.append((key, direction, s, recs))
        lines.append(
            f"| {key[0]} | {key[1]} | {direction} | {s['n']} | "
            f"{s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% | {s['cumul']:+.2f}% |"
        )

    # 強度ランキング
    lines.append("")
    lines.append("## 強度ランキング (|t| 降順)")
    lines.append("")
    lines.append("| rank | purpose | holder | dir | n | t | EV(net) |")
    lines.append("|---|---|---|---|---|---|---|")
    rows.sort(key=lambda x: -abs(x[2]["t"]))
    for i, (key, dr, s, _) in enumerate(rows, 1):
        lines.append(
            f"| {i} | {key[0]} | {key[1]} | {dr} | {s['n']} | {s['t']:+.2f} | {s['ev']:+.2f}% |"
        )

    # 有意セル (|t|>=2) の年度別 cumul
    lines.append("")
    lines.append("## 有意セル (|t|≥2) の年度別 cumul (OOS 健全性)")
    for (key, dr, s, recs) in [r for r in rows if abs(r[2]["t"]) >= 2.0]:
        lines.append("")
        lines.append(f"### {key[0]} / {key[1]}  ({dr})")
        by_year: dict[str, list[float]] = defaultdict(list)
        for r in recs:
            v = (r.get("attrs") or {}).get(PRIMARY_METRIC)
            if v is None:
                continue
            by_year[r["event_date"][:4]].append(_net_pnl(float(v), cost_pct, dr))
        lines.append("")
        lines.append("| year | n | cumul(net) | EV(net) |")
        lines.append("|---|---|---|---|")
        for y in sorted(by_year):
            vals = by_year[y]
            lines.append(f"| {y} | {len(vals)} | {sum(vals):+.2f}% | {statistics.fmean(vals):+.2f}% |")

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=DATA_PATH, help="holdings_records.json のパス")
    ap.add_argument("--out", type=Path, default=REPORT_PATH, help="出力 md ファイル")
    ap.add_argument("--cost", type=float, default=COST_PCT_DEFAULT, help="往復コスト %% (既定 0.20)")
    args = ap.parse_args()

    data = json.loads(args.path.read_text())
    report = build_report(data["records"], args.cost)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

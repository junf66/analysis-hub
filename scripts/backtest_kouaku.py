"""kouaku_records から「subpattern × DiscTime」ごとの net バックテストを生成。

戦略: 翌寄り起点。
  - cell の翌寄り→翌引 EV が負: 翌寄りでショート → 翌引け買戻 (short_ret = -ret)
  - cell の翌寄り→翌引 EV が正: 翌寄りでロング  → 翌引け売り

往復コスト COST_PCT を引いて純損益。累積は単純加算 (複利なし、1 単位ベット想定)。

出力: reports/kouaku_backtest.md
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = REPO_ROOT / "data" / "kouaku_records.json"
REPORT_PATH = REPO_ROOT / "reports" / "kouaku_backtest.md"

COST_PCT_DEFAULT = 0.20
MIN_CELL_N = 5


from scripts._buckets import disc_bucket as _disc_bucket  # noqa: E402


def _net_pnl(open_to_close_ret: float, cost_pct: float, direction: str) -> float:
    raw = -open_to_close_ret if direction == "short" else open_to_close_ret
    return raw - cost_pct


def _stat_block(values: list[float]) -> dict[str, float]:
    n = len(values)
    if n == 0:
        return {"n": 0, "ev": 0, "stdev": 0, "se": 0, "t": 0, "win": 0, "cumul": 0}
    m = statistics.fmean(values)
    s = statistics.stdev(values) if n > 1 else 0.0
    se = s / math.sqrt(n) if s else 0.0
    t = m / se if se else 0.0
    wins = sum(1 for v in values if v > 0)
    return {"n": n, "ev": m, "stdev": s, "se": se, "t": t, "win": wins / n * 100, "cumul": sum(values)}


def build_report(records: list[dict[str, Any]], cost_pct: float) -> str:
    # cell 構成
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if (r.get("attrs") or {}).get("limit_locked"):
            continue
        cells[(r.get("subpattern", "other"), _disc_bucket(r))].append(r)

    lines: list[str] = []
    lines.append("# kouaku_mixed バックテスト (寄→引 戦略)")
    lines.append("")
    lines.append(f"往復コスト: **{cost_pct:.2f}%** / 1 ベット 1 単位均等 (複利なし) / limit-lock 除外")
    lines.append("")
    lines.append("方向は cell の生 EV (翌寄り→翌引) 符号に従う。負なら寄り売り、正なら寄り買い。")
    lines.append("")
    lines.append("| subpattern | DiscTime | direction | n | EV(net) | t | win% | cumul(net) |")
    lines.append("|---|---|---|---|---|---|---|---|")

    rows: list[tuple[str, str, str, dict[str, float], list[dict[str, Any]]]] = []
    for (sub, bk), recs in sorted(cells.items()):
        rets = [(r.get("attrs") or {}).get("next_day_open_to_close_ret") for r in recs]
        rets = [float(v) for v in rets if v is not None]
        if len(rets) < MIN_CELL_N:
            continue
        raw_ev = statistics.fmean(rets)
        direction = "short" if raw_ev < 0 else "long"
        nets = [_net_pnl(r, cost_pct, direction) for r in rets]
        s = _stat_block(nets)
        rows.append((sub, bk, direction, s, recs))
        lines.append(
            f"| {sub} | {bk} | {direction} | {s['n']} | "
            f"{s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% | {s['cumul']:+.2f}% |"
        )

    # |t| 降順で並べた要約
    lines.append("")
    lines.append("## 強度ランキング (|t| 降順)")
    lines.append("")
    lines.append("| rank | subpattern | DiscTime | dir | n | t | EV(net) |")
    lines.append("|---|---|---|---|---|---|---|")
    rows.sort(key=lambda x: -abs(x[3]["t"]))
    for i, (sub, bk, dr, s, _) in enumerate(rows, 1):
        lines.append(
            f"| {i} | {sub} | {bk} | {dr} | {s['n']} | {s['t']:+.2f} | {s['ev']:+.2f}% |"
        )

    # |t|>=2 のセルだけ年度別 cumul
    lines.append("")
    lines.append("## 有意セル (|t|≥2) の年度別 cumul (out-of-sample 健全性)")
    for (sub, bk, dr, s, recs) in [r for r in rows if abs(r[3]["t"]) >= 2.0]:
        lines.append("")
        lines.append(f"### {sub} × {bk}  ({dr})")
        by_year: dict[str, list[float]] = defaultdict(list)
        for r in recs:
            v = (r.get("attrs") or {}).get("next_day_open_to_close_ret")
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
    ap.add_argument("--path", type=Path, default=DATA_PATH)
    ap.add_argument("--out", type=Path, default=REPORT_PATH)
    ap.add_argument("--cost", type=float, default=COST_PCT_DEFAULT, help="往復コスト %% (既定 0.20)")
    args = ap.parse_args()

    data = json.loads(args.path.read_text())
    report = build_report(data["records"], args.cost)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

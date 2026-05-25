"""data/po_records.json から PO 各セル (stage × po_type × lending_type) の net バックテストを生成。

戦略: ステージ固有の主要 metric を 1 つ選び、cell の生 EV 符号に従って方向を決める。
  - announce ステージ: next_day_910_ret (po-tracker FIELDS.md 準拠の翌寄り→9:10 戦略)
  - decide   ステージ: ret_close      (next_open→決定日引け)
  - deliver  ステージ: next_day_open_to_close_ret (受渡日 寄り→引け)

往復コスト COST_PCT を引いて純損益。累積は単純加算 (複利なし、1 単位ベット想定)。

既知 3 エッジ (po-tracker セッション参照 EV):
  - 発表翌日 (普通 announce, long): EV +0.66%
  - 受渡日 GD (普通 deliver, gap<=-0.5, long): EV +0.80%
  - リート ショート (REIT decide, short): EV +1.12%

出力: reports/po_backtest.md
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
DATA_PATH = REPO_ROOT / "data" / "po_records.json"
REPORT_PATH = REPO_ROOT / "reports" / "po_backtest.md"

COST_PCT_DEFAULT = 0.20
MIN_CELL_N = 5

# stage → 戦略 metric
_STAGE_METRIC: dict[str, str] = {
    "announce": "next_day_910_ret",
    "decide": "ret_close",
    "deliver": "next_day_open_to_close_ret",
}

GD_THRESHOLD_PCT = -0.5


def _is_eligible_for_ev(rec: dict[str, Any]) -> bool:
    if rec.get("legacy_record"):
        return False
    if rec.get("concurrent_earnings"):
        return False
    if rec.get("split_within_po_window"):
        return False
    status = rec.get("status")
    if rec.get("stage") == "announce":
        return status in ("complete", "nextday")
    return status == "complete"


def _net_pnl(raw_ret: float, cost_pct: float, direction: str) -> float:
    base = -raw_ret if direction == "short" else raw_ret
    return base - cost_pct


def _stat_block(values: list[float]) -> dict[str, float]:
    n = len(values)
    if n == 0:
        return {"n": 0, "ev": 0.0, "stdev": 0.0, "se": 0.0, "t": 0.0, "win": 0.0, "cumul": 0.0}
    m = statistics.fmean(values)
    s = statistics.stdev(values) if n > 1 else 0.0
    se = s / math.sqrt(n) if s else 0.0
    t = m / se if se else 0.0
    wins = sum(1 for v in values if v > 0)
    return {"n": n, "ev": m, "stdev": s, "se": se, "t": t, "win": wins / n * 100, "cumul": sum(values)}


def build_report(records: list[dict[str, Any]], cost_pct: float) -> str:
    """stage × po_type × lending_type セルの net 損益 + 既知 3 エッジ単独評価を md で返す。"""
    # cell 構成 (stage × po_type × lending_type)
    cells: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if not _is_eligible_for_ev(r):
            continue
        key = (r.get("stage", "?"), r.get("po_type") or "?", r.get("lending_type") or "?")
        cells[key].append(r)

    lines: list[str] = []
    lines.append("# PO バックテスト")
    lines.append("")
    lines.append(f"往復コスト: **{cost_pct:.2f}%** / 1 ベット 1 単位均等 (複利なし)")
    lines.append("")
    lines.append("metric: announce=next_day_910_ret / decide=ret_close / deliver=next_day_open_to_close_ret")
    lines.append("方向は cell の生 EV (該当 metric) 符号に従う。")
    lines.append("")
    lines.append("| stage | po_type | lending | metric | n | EV(net) | t | win% | cumul(net) |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    rows: list[tuple[tuple[str, str, str], str, dict[str, float], list[dict[str, Any]]]] = []
    for key, recs in sorted(cells.items()):
        stage = key[0]
        field = _STAGE_METRIC.get(stage)
        if not field:
            continue
        rets = [(r.get("attrs") or {}).get(field) for r in recs]
        rets = [float(v) for v in rets if v is not None]
        if len(rets) < MIN_CELL_N:
            continue
        raw_ev = statistics.fmean(rets)
        direction = "short" if raw_ev < 0 else "long"
        nets = [_net_pnl(r, cost_pct, direction) for r in rets]
        s = _stat_block(nets)
        rows.append((key, direction, s, recs))
        lines.append(
            f"| {key[0]} | {key[1]} | {key[2]} | {field} ({direction}) | {s['n']} | "
            f"{s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% | {s['cumul']:+.2f}% |"
        )

    # 強度ランキング
    lines.append("")
    lines.append("## 強度ランキング (|t| 降順)")
    lines.append("")
    lines.append("| rank | stage | po_type | lending | dir | n | t | EV(net) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    rows.sort(key=lambda x: -abs(x[2]["t"]))
    for i, (key, dr, s, _) in enumerate(rows, 1):
        lines.append(
            f"| {i} | {key[0]} | {key[1]} | {key[2]} | {dr} | {s['n']} | {s['t']:+.2f} | {s['ev']:+.2f}% |"
        )

    # 既知 3 エッジの net 単独評価
    lines.append("")
    lines.append("## 既知 3 エッジ (net EV)")
    lines.append("")
    lines.append("| edge | n | EV(net) | t | win% | cumul(net) |")
    lines.append("|---|---|---|---|---|---|")

    # 1. announce + 普通 → next_day_910_ret long
    e1 = [
        float((r.get("attrs") or {}).get("next_day_910_ret"))
        for r in records
        if r.get("stage") == "announce"
        and r.get("po_type") == "普通"
        and _is_eligible_for_ev(r)
        and (r.get("attrs") or {}).get("next_day_910_ret") is not None
    ]
    s = _stat_block([_net_pnl(v, cost_pct, "long") for v in e1])
    lines.append(
        f"| 発表翌日 (普通 announce long, 9:10 売り) | {s['n']} | "
        f"{s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% | {s['cumul']:+.2f}% |"
    )

    # 2. deliver + 普通 + gap<=-0.5 → next_day_open_to_close_ret long
    e2 = [
        float((r.get("attrs") or {}).get("next_day_open_to_close_ret"))
        for r in records
        if r.get("stage") == "deliver"
        and r.get("po_type") == "普通"
        and _is_eligible_for_ev(r)
        and (r.get("attrs") or {}).get("next_day_open_to_close_ret") is not None
        and (r.get("attrs") or {}).get("gap_pct") is not None
        and float((r.get("attrs") or {}).get("gap_pct")) <= GD_THRESHOLD_PCT
    ]
    s = _stat_block([_net_pnl(v, cost_pct, "long") for v in e2])
    lines.append(
        f"| 受渡日 GD (普通 deliver, gap<={GD_THRESHOLD_PCT}%, 寄り→引け long) | {s['n']} | "
        f"{s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% | {s['cumul']:+.2f}% |"
    )

    # 3. decide + リート → ret_close short
    e3 = [
        float((r.get("attrs") or {}).get("ret_close"))
        for r in records
        if r.get("stage") == "decide"
        and r.get("po_type") == "リート"
        and _is_eligible_for_ev(r)
        and (r.get("attrs") or {}).get("ret_close") is not None
    ]
    s = _stat_block([_net_pnl(v, cost_pct, "short") for v in e3])
    lines.append(
        f"| リート ショート (REIT decide, next_open→決定日引け short) | {s['n']} | "
        f"{s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% | {s['cumul']:+.2f}% |"
    )

    # 有意セル年度別 (|t|>=2 のみ)
    lines.append("")
    lines.append("## 有意セル (|t|≥2) の年度別 cumul (OOS 健全性)")
    for (key, dr, s, recs) in [r for r in rows if abs(r[2]["t"]) >= 2.0]:
        stage = key[0]
        field = _STAGE_METRIC.get(stage)
        if not field:
            continue
        lines.append("")
        lines.append(f"### {key[0]} / {key[1]} / {key[2]}  ({dr})")
        by_year: dict[str, list[float]] = defaultdict(list)
        for r in recs:
            v = (r.get("attrs") or {}).get(field)
            if v is None:
                continue
            by_year[r["event_date"][:4]].append(_net_pnl(float(v), cost_pct, dr))
        lines.append("")
        lines.append("| year | n | cumul(net) | EV(net) |")
        lines.append("|---|---|---|---|")
        for y in sorted(by_year):
            vals = by_year[y]
            lines.append(
                f"| {y} | {len(vals)} | {sum(vals):+.2f}% | {statistics.fmean(vals):+.2f}% |"
            )

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=DATA_PATH, help="po_records.json のパス")
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

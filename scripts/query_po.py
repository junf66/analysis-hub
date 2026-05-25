"""po_records.json を ad-hoc フィルタして EV を即計算 (探索用 CLI / query_kouaku の PO 版)。

stage / po_type / lending_type / 期間 / GAP 等で絞り込み、n / EV / median / σ / SE /
t / win率 / 累積 / bootstrap CI / histogram / cumul を出す。

注意: PO の metric は stage 依存。
  announce → next_day_905..1000_ret / next_day_morning_ret / next_day_open_to_high_ret
  decide   → ret_open / ret_close
  deliver  → gap_pct / next_day_open_to_close_ret
--stage で絞ってから --metric を選ぶと分かりやすい。

例:
  python -m scripts.query_po --stage decide --po-type リート --metric ret_close --bootstrap
  python -m scripts.query_po --stage decide --metric ret_close --group-by lending_type
  python -m scripts.query_po --stage announce --metric next_day_910_ret --group-by po_type
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts._query_report import group_table, summarize
from scripts.analyze_po_edge import _is_eligible_for_ev

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDS_PATH = REPO_ROOT / "data" / "po_records.json"

_METRIC_CHOICES = [
    "gap_pct",
    "next_day_905_ret",
    "next_day_910_ret",
    "next_day_915_ret",
    "next_day_930_ret",
    "next_day_1000_ret",
    "next_day_morning_ret",
    "next_day_open_to_close_ret",
    "next_day_open_to_high_ret",
    "ret_open",
    "ret_close",
]

_GROUP_KEYS = {
    "stage": lambda r: r.get("stage", "?"),
    "po_type": lambda r: r.get("po_type") or "?",
    "lending_type": lambda r: r.get("lending_type") or "?",
    "year": lambda r: r.get("event_date", "?")[:4],
    "code": lambda r: r.get("code", "?"),
}

_DIMS = [
    ("stage", _GROUP_KEYS["stage"]),
    ("po_type", _GROUP_KEYS["po_type"]),
    ("lending_type", _GROUP_KEYS["lending_type"]),
]


def _csv_set(raw: str | None) -> set[str] | None:
    return {s.strip() for s in raw.split(",")} if raw else None


def _filter(records: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    stages = _csv_set(args.stage)
    ptypes = _csv_set(args.po_type)
    lendings = _csv_set(args.lending_type)
    codes = {c.strip().zfill(4) for c in args.code.split(",")} if args.code else None

    out: list[dict[str, Any]] = []
    for r in records:
        if not args.include_ineligible and not _is_eligible_for_ev(r):
            continue
        if stages and r.get("stage") not in stages:
            continue
        if ptypes and (r.get("po_type") or "?") not in ptypes:
            continue
        if lendings and (r.get("lending_type") or "?") not in lendings:
            continue
        if codes and r.get("code") not in codes:
            continue
        if args.year and r.get("event_date", "")[:4] != str(args.year):
            continue
        if args.since and r.get("event_date", "") < args.since:
            continue
        if args.until and r.get("event_date", "") > args.until:
            continue
        gap = (r.get("attrs") or {}).get("gap_pct")
        if args.gap_min is not None and (gap is None or gap < args.gap_min):
            continue
        if args.gap_max is not None and (gap is None or gap > args.gap_max):
            continue
        out.append(r)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=RECORDS_PATH, help="po_records.json のパス")
    ap.add_argument("--stage", help="announce,decide,deliver (カンマ区切り)")
    ap.add_argument("--po-type", help="普通,リート (カンマ区切り)")
    ap.add_argument("--lending-type", help="貸借,信用 等 (カンマ区切り)")
    ap.add_argument("--code", help="カンマ区切り 4 桁コード")
    ap.add_argument("--year", type=int, help="event_date の年")
    ap.add_argument("--since", help="ISO date YYYY-MM-DD (含む)")
    ap.add_argument("--until", help="ISO date YYYY-MM-DD (含む)")
    ap.add_argument("--gap-min", type=float, help="GAP%% 下限 (含む)")
    ap.add_argument("--gap-max", type=float, help="GAP%% 上限 (含む)")
    ap.add_argument("--include-ineligible", action="store_true",
                    help="legacy/決算同時/分割窓/status 不適格も含める (既定は除外)")
    ap.add_argument("--metric", choices=_METRIC_CHOICES, default="next_day_910_ret",
                    help="集計対象メトリクス (既定 next_day_910_ret, stage 依存)")
    ap.add_argument("--group-by", choices=list(_GROUP_KEYS), help="グルーピング集計")
    ap.add_argument("--bootstrap", action="store_true", help="平均の bootstrap 95%% CI")
    ap.add_argument("--bootstrap-iter", type=int, default=2000, help="bootstrap リサンプル回数")
    ap.add_argument("--histogram", action="store_true", help="ASCII ヒストグラム")
    ap.add_argument("--histogram-bins", type=int, default=20, help="histogram の bin 数")
    ap.add_argument("--plot-cumul", action="store_true", help="event_date 順の累積 PnL")
    ap.add_argument("--list-records", action="store_true", help="該当レコード一覧")
    ap.add_argument("--collapse-daily", action="store_true",
                    help="同一 code+date を1観測に集約 (非独立サンプルの n/t 水増しを補正)")
    args = ap.parse_args()

    payload = json.loads(args.path.read_text())
    filtered = _filter(payload.get("records", []), args)

    if args.group_by:
        group_table(filtered, args.metric, _GROUP_KEYS[args.group_by],
                    group_by=args.group_by, collapse=args.collapse_daily)
        return

    label = " ".join(
        f"{k}={v}" for k, v in vars(args).items()
        if v and k not in ("path", "metric", "group_by", "bootstrap", "bootstrap_iter",
                           "histogram", "histogram_bins", "plot_cumul", "list_records",
                           "include_ineligible", "collapse_daily")
    )
    summarize(
        filtered, args.metric, filter_label=label,
        bootstrap=args.bootstrap, bootstrap_iter=args.bootstrap_iter,
        histogram=args.histogram, histogram_bins=args.histogram_bins,
        plot_cumul=args.plot_cumul, dims=_DIMS, list_records=args.list_records,
        collapse=args.collapse_daily,
    )


if __name__ == "__main__":
    main()

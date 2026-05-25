"""holdings_records.json を ad-hoc フィルタして EV を即計算 (探索用 CLI / query_kouaku の大量保有版)。

保有目的 (purpose) / 保有者区分 (holder) / GAP ラベル / 保有割合 / 期間 等で絞り込み、
n / EV / median / σ / SE / t / win率 / 累積 / bootstrap CI / histogram / cumul を出す。

metric は寄り→引け (next_day_open_to_close_ret) が既定。寄り→各時刻、GAP、d5/d10 も可。

例:
  python -m scripts.query_holdings --holder 外資ファンド --bootstrap
  python -m scripts.query_holdings --purpose 重要提案 --metric next_day_open_to_close_ret
  python -m scripts.query_holdings --group-by purpose --metric next_day_open_to_close_ret
  python -m scripts.query_holdings --holder アクティビスト --ratio-min 10 --group-by gap_label
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts._query_report import group_table, summarize
from scripts.analyze_holdings_edge import is_eligible_for_ev

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDS_PATH = REPO_ROOT / "data" / "holdings_records.json"

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
    "next_day_open_to_low_ret",
    "next_day_high_to_close_ret",
    "d5_ret",
    "d10_ret",
]

_GROUP_KEYS = {
    "purpose": lambda r: r.get("purpose_category_jp") or "?",
    "holder": lambda r: r.get("holder_category_jp") or "?",
    "gap_label": lambda r: r.get("gap_label") or "?",
    "year": lambda r: r.get("event_date", "?")[:4],
    "code": lambda r: r.get("code", "?"),
}

_DIMS = [
    ("purpose", _GROUP_KEYS["purpose"]),
    ("holder", _GROUP_KEYS["holder"]),
    ("gap_label", _GROUP_KEYS["gap_label"]),
]


def _csv_set(raw: str | None) -> set[str] | None:
    return {s.strip() for s in raw.split(",")} if raw else None


def _filter(records: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    purposes = _csv_set(args.purpose)
    holders = _csv_set(args.holder)
    gap_labels = _csv_set(args.gap_label)
    codes = {c.strip().zfill(4) for c in args.code.split(",")} if args.code else None

    out: list[dict[str, Any]] = []
    for r in records:
        if not args.include_suspect and not is_eligible_for_ev(r):
            continue
        if purposes and (r.get("purpose_category_jp") or "?") not in purposes:
            continue
        if holders and (r.get("holder_category_jp") or "?") not in holders:
            continue
        if gap_labels and (r.get("gap_label") or "?") not in gap_labels:
            continue
        if codes and r.get("code") not in codes:
            continue
        if args.year and r.get("event_date", "")[:4] != str(args.year):
            continue
        if args.since and r.get("event_date", "") < args.since:
            continue
        if args.until and r.get("event_date", "") > args.until:
            continue
        ratio = r.get("holding_ratio")
        if args.ratio_min is not None and (ratio is None or ratio < args.ratio_min):
            continue
        if args.ratio_max is not None and (ratio is None or ratio > args.ratio_max):
            continue
        # 流動性フィルタ (約定可能性): 売買代金・時価総額の下限
        tov = r.get("turnover_oku")
        if args.min_turnover is not None and (tov is None or tov < args.min_turnover):
            continue
        mcap = r.get("market_cap_oku")
        if args.min_mktcap is not None and (mcap is None or mcap < args.min_mktcap):
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
    ap.add_argument("--path", type=Path, default=RECORDS_PATH, help="holdings_records.json のパス")
    ap.add_argument("--purpose", help="保有目的 (例 純投資,重要提案 / カンマ区切り)")
    ap.add_argument("--holder", help="保有者区分 (例 外資ファンド,アクティビスト / カンマ区切り)")
    ap.add_argument("--gap-label", help="GAP ラベル (例 GU,GD / カンマ区切り)")
    ap.add_argument("--code", help="カンマ区切り 4 桁コード")
    ap.add_argument("--year", type=int, help="event_date の年")
    ap.add_argument("--since", help="ISO date YYYY-MM-DD (含む)")
    ap.add_argument("--until", help="ISO date YYYY-MM-DD (含む)")
    ap.add_argument("--ratio-min", type=float, help="保有割合%% 下限 (含む)")
    ap.add_argument("--ratio-max", type=float, help="保有割合%% 上限 (含む)")
    ap.add_argument("--gap-min", type=float, help="GAP%% 下限 (含む)")
    ap.add_argument("--gap-max", type=float, help="GAP%% 上限 (含む)")
    ap.add_argument("--min-turnover", type=float, help="売買代金(億)の下限 — 約定可能性フィルタ")
    ap.add_argument("--min-mktcap", type=float, help="時価総額(億)の下限 — 約定可能性フィルタ")
    ap.add_argument("--include-suspect", action="store_true",
                    help="low_ratio_suspect も含める (既定は除外)")
    ap.add_argument("--metric", choices=_METRIC_CHOICES, default="next_day_open_to_close_ret",
                    help="集計対象メトリクス (既定 next_day_open_to_close_ret)")
    ap.add_argument("--group-by", choices=list(_GROUP_KEYS), help="グルーピング集計")
    ap.add_argument("--bootstrap", action="store_true", help="平均の bootstrap 95%% CI")
    ap.add_argument("--bootstrap-iter", type=int, default=2000, help="bootstrap リサンプル回数")
    ap.add_argument("--histogram", action="store_true", help="ASCII ヒストグラム")
    ap.add_argument("--histogram-bins", type=int, default=20, help="histogram の bin 数")
    ap.add_argument("--plot-cumul", action="store_true", help="event_date 順の累積 PnL")
    ap.add_argument("--list-records", action="store_true", help="該当レコード一覧")
    ap.add_argument("--collapse-daily", action="store_true",
                    help="同一 code+date を1観測に集約 (同日複数提出者の n/t 水増しを補正)")
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
                           "include_suspect", "collapse_daily")
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

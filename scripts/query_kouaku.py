"""kouaku_records を ad-hoc フィルタして EV を即計算。

探索セッション用の CLI。dataframe 不要、stdlib のみで動作。

例:
  python -m scripts.query_kouaku                                  # 全件
  python -m scripts.query_kouaku --subpattern kouhou_genshu       # サブパターン
  python -m scripts.query_kouaku --disc-time-bucket 場中           # DiscTime
  python -m scripts.query_kouaku --year 2025                       # 年
  python -m scripts.query_kouaku --code 7203,4502                  # 銘柄
  python -m scripts.query_kouaku --metric next_day_910_ret         # 別指標
  python -m scripts.query_kouaku --gap-min -5 --gap-max 0          # GAP 範囲
  python -m scripts.query_kouaku --subpattern kouhou_genshu --disc-time-bucket 場中 \\
      --json                                                       # JSON 出力

集計値: n / EV / median / σ / SE / t / win率 / 累積 / GAP 別分布。
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDS_PATH = REPO_ROOT / "data" / "kouaku_records.json"

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
    "next_day_full_ret",
]


def _disc_bucket(rec: dict[str, Any]) -> str:
    times = [f.get("disc_time") for f in rec.get("good_factors", []) + rec.get("bad_factors", []) if f.get("disc_time")]
    if not times:
        return "unknown"
    t = min(times)
    h = t[:2]
    if h < "09":
        return "寄前"
    if h < "11":
        return "寄り中"
    if h < "15":
        return "場中"
    if h == "15" and t < "15:30":
        return "引け間際"
    return "大引け後"


def _filter(records: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    codes: set[str] | None = None
    if args.code:
        codes = {c.strip().zfill(4) for c in args.code.split(",")}
    subs: set[str] | None = None
    if args.subpattern:
        subs = {s.strip() for s in args.subpattern.split(",")}
    buckets: set[str] | None = None
    if args.disc_time_bucket:
        buckets = {b.strip() for b in args.disc_time_bucket.split(",")}
    for r in records:
        a = r.get("attrs") or {}
        if args.exclude_locked and a.get("limit_locked"):
            continue
        if codes and r["code"] not in codes:
            continue
        if subs and r.get("subpattern") not in subs:
            continue
        if args.year and r["event_date"][:4] != str(args.year):
            continue
        if args.since and r["event_date"] < args.since:
            continue
        if args.until and r["event_date"] > args.until:
            continue
        if buckets and _disc_bucket(r) not in buckets:
            continue
        gap = a.get("gap_pct")
        if args.gap_min is not None and (gap is None or gap < args.gap_min):
            continue
        if args.gap_max is not None and (gap is None or gap > args.gap_max):
            continue
        out.append(r)
    return out


def _stats(values: list[float]) -> dict[str, float]:
    n = len(values)
    if n == 0:
        return {"n": 0}
    m = statistics.fmean(values)
    med = statistics.median(values)
    s = statistics.stdev(values) if n > 1 else 0.0
    se = s / math.sqrt(n) if s else 0.0
    t = m / se if se else 0.0
    wins = sum(1 for v in values if v > 0)
    return {
        "n": n, "ev": m, "median": med, "stdev": s, "se": se, "t": t,
        "win": wins / n * 100, "cumul": sum(values),
        "min": min(values), "max": max(values),
    }


def _print_human(filtered: list[dict[str, Any]], metric: str, args: argparse.Namespace) -> None:
    vals = [(r.get("attrs") or {}).get(metric) for r in filtered]
    vals = [float(v) for v in vals if v is not None]
    st = _stats(vals)

    # フィルタ条件サマリ
    print("=" * 60)
    print(f"filter: {' '.join(f'{k}={v}' for k,v in vars(args).items() if v and k not in ('json','metric','path','list_records'))}")
    print(f"metric: {metric}")
    print("-" * 60)
    if st["n"] == 0:
        print(f"n=0 (該当なし)")
        return
    print(f"  n         = {st['n']}")
    print(f"  EV        = {st['ev']:+.3f}%")
    print(f"  median    = {st['median']:+.3f}%")
    print(f"  σ (stdev) = {st['stdev']:.3f}%")
    print(f"  SE        = {st['se']:.3f}%")
    print(f"  t-stat    = {st['t']:+.2f}")
    print(f"  win率     = {st['win']:.1f}%")
    print(f"  cumul     = {st['cumul']:+.2f}% (単純加算)")
    print(f"  range     = {st['min']:+.2f}% .. {st['max']:+.2f}%")

    # subpattern 別件数
    subs = Counter(r["subpattern"] for r in filtered)
    if len(subs) > 1:
        print(f"\nsubpattern 分布:")
        for k, n in subs.most_common():
            print(f"    {k}: {n}")

    # DiscTime 別件数
    bks = Counter(_disc_bucket(r) for r in filtered)
    if len(bks) > 1:
        print(f"\nDiscTime 分布:")
        for k, n in bks.most_common():
            print(f"    {k}: {n}")

    if args.list_records:
        print(f"\n=== records (sorted by event_date) ===")
        for r in sorted(filtered, key=lambda x: x["event_date"]):
            a = r.get("attrs") or {}
            v = a.get(metric)
            v_str = f"{v:+.2f}%" if v is not None else "--"
            print(f"  {r['event_date']} {r['code']:>5}  [{r['subpattern']}]  {metric}={v_str}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=RECORDS_PATH)
    ap.add_argument("--subpattern", help="カンマ区切り (e.g. kouhou_genshu,zouhai_genshu)")
    ap.add_argument("--disc-time-bucket", help="カンマ区切り (e.g. 場中,大引け後)")
    ap.add_argument("--year", type=int)
    ap.add_argument("--since", help="ISO date YYYY-MM-DD (含む)")
    ap.add_argument("--until", help="ISO date YYYY-MM-DD (含む)")
    ap.add_argument("--code", help="カンマ区切り 4 桁コード")
    ap.add_argument("--gap-min", type=float, help="GAP%% 下限 (含む)")
    ap.add_argument("--gap-max", type=float, help="GAP%% 上限 (含む)")
    ap.add_argument("--exclude-locked", action="store_true", default=True, help="limit-lock 除外 (既定 ON)")
    ap.add_argument("--include-locked", action="store_true", help="limit-lock を含める (--exclude-locked を上書き)")
    ap.add_argument("--metric", choices=_METRIC_CHOICES, default="next_day_open_to_close_ret")
    ap.add_argument("--json", action="store_true", help="JSON で stdout 出力")
    ap.add_argument("--list-records", action="store_true", help="該当レコードを一覧表示")
    args = ap.parse_args()
    if args.include_locked:
        args.exclude_locked = False

    payload = json.loads(args.path.read_text())
    records = payload.get("records", [])
    filtered = _filter(records, args)

    if args.json:
        vals = [(r.get("attrs") or {}).get(args.metric) for r in filtered]
        vals = [float(v) for v in vals if v is not None]
        result = {
            "metric": args.metric,
            "stats": _stats(vals),
            "n_records": len(filtered),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    _print_human(filtered, args.metric, args)


if __name__ == "__main__":
    main()

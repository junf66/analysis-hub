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


from scripts._buckets import disc_bucket as _disc_bucket  # noqa: E402


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


def _bootstrap_ci(values: list[float], *, n_iter: int = 2000, alpha: float = 0.05) -> tuple[float, float]:
    """平均の bootstrap CI (両側 1-alpha)。stdlib のみで実装。"""
    import random
    n = len(values)
    if n < 2:
        return (0.0, 0.0)
    rng = random.Random(42)
    means: list[float] = []
    for _ in range(n_iter):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.fmean(sample))
    means.sort()
    lo = means[int(n_iter * alpha / 2)]
    hi = means[int(n_iter * (1 - alpha / 2))]
    return (lo, hi)


def _ascii_histogram(values: list[float], *, bins: int = 20, width: int = 40) -> list[str]:
    """ASCII ヒストグラム。"""
    if not values:
        return ["(empty)"]
    lo, hi = min(values), max(values)
    if lo == hi:
        return [f"{lo:+.2f}% | {len(values)} all-same"]
    step = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - lo) / step), bins - 1)
        counts[idx] += 1
    peak = max(counts) or 1
    out: list[str] = []
    for i, c in enumerate(counts):
        left = lo + step * i
        right = lo + step * (i + 1)
        bar = "█" * int(c * width / peak)
        out.append(f"  {left:+6.2f}..{right:+6.2f}%  {c:>3d}  {bar}")
    return out


def _ascii_cumul(values: list[float], *, width: int = 50, height: int = 12) -> list[str]:
    """累積 PnL ASCII プロット (順序は与えられたまま、time-ordered 想定)。"""
    if len(values) < 2:
        return ["(too few samples for cumul plot)"]
    cumul: list[float] = []
    s = 0.0
    for v in values:
        s += v
        cumul.append(s)
    lo, hi = min(cumul), max(cumul)
    if lo == hi:
        return [f"flat at {lo:+.2f}%"]
    n = len(cumul)
    # サンプリング (n が width より大きい場合は間引く)
    if n > width:
        step = n / width
        sampled = [cumul[int(i * step)] for i in range(width)]
    else:
        sampled = cumul + [cumul[-1]] * (width - n)
    grid = [[" "] * width for _ in range(height)]
    for x, v in enumerate(sampled):
        y_pos = int((1 - (v - lo) / (hi - lo)) * (height - 1))
        y_pos = max(0, min(height - 1, y_pos))
        grid[y_pos][x] = "•"
    # y軸ラベルを左に
    out = [f"  cumul (n={n}, range {lo:+.2f}% .. {hi:+.2f}%):"]
    for i, row in enumerate(grid):
        y_val = hi - (hi - lo) * i / (height - 1)
        out.append(f"  {y_val:>+7.2f}% |{''.join(row)}")
    out.append(f"            +{'-' * width}")
    return out


_GROUP_KEYS = {
    "subpattern": lambda r: r.get("subpattern", "?"),
    "disc_time_bucket": _disc_bucket,
    "year": lambda r: r["event_date"][:4],
    "code": lambda r: r["code"],
}


def _print_group(filtered: list[dict[str, Any]], metric: str, group_by: str) -> None:
    """指定キーでグルーピングし、各 cell の n/EV/t/win を 1 行で並べる。"""
    if group_by not in _GROUP_KEYS:
        print(f"unknown --group-by: {group_by} (choices: {list(_GROUP_KEYS)})")
        return
    grouper = _GROUP_KEYS[group_by]
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in filtered:
        groups.setdefault(grouper(r), []).append(r)

    print(f"=" * 70)
    print(f"group_by={group_by}  metric={metric}  total filtered={len(filtered)}")
    print(f"-" * 70)
    header = f"  {'key':20s} {'n':>4s}  {'EV':>8s}  {'σ':>6s}  {'t':>6s}  {'win':>6s}  {'cumul':>8s}"
    print(header)
    print(f"  {'-' * 20} {'-'*4}  {'-'*8}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*8}")
    rows = []
    for k, recs in groups.items():
        vals = [(r.get("attrs") or {}).get(metric) for r in recs]
        vals = [float(v) for v in vals if v is not None]
        st = _stats(vals)
        rows.append((k, st, len(recs)))
    # sort: |t| 降順 (n>=3 のみ、それ以外は末尾)
    rows.sort(key=lambda x: (-(abs(x[1].get("t", 0)) if x[1].get("n", 0) >= 3 else -1)))
    for k, st, n_rec in rows:
        n = st.get("n", 0)
        if n < 1:
            print(f"  {str(k):20s} {n_rec:>4d}  (no metric)")
            continue
        ev_s = f"{st['ev']:+.3f}%" if n >= 1 else "  -"
        sig_s = f"{st['stdev']:.2f}%" if n >= 2 else "  -"
        t_s = f"{st['t']:+.2f}" if n >= 2 else "  -"
        win_s = f"{st['win']:.0f}%"
        cum_s = f"{st['cumul']:+.2f}%"
        marker = " ★" if n >= 5 and abs(st["t"]) >= 2 else ""
        print(f"  {str(k):20s} {n:>4d}  {ev_s:>8s}  {sig_s:>6s}  {t_s:>6s}  {win_s:>6s}  {cum_s:>8s}{marker}")
    print("\n  (★ = n>=5 かつ |t|>=2)")


def _print_human(filtered: list[dict[str, Any]], metric: str, args: argparse.Namespace) -> None:
    vals = [(r.get("attrs") or {}).get(metric) for r in filtered]
    vals = [float(v) for v in vals if v is not None]
    st = _stats(vals)

    # フィルタ条件サマリ
    print("=" * 60)
    active_filters = " ".join(
        f"{k}={v}" for k, v in vars(args).items()
        if v and k not in ("json", "metric", "path", "list_records", "group_by",
                          "histogram", "bootstrap", "plot_cumul", "include_locked")
    )
    print(f"filter: {active_filters or '(none)'}")
    print(f"metric: {metric}")
    print("-" * 60)
    if st["n"] == 0:
        print("n=0 (該当なし)")
        return
    print(f"  n         = {st['n']}")
    print(f"  EV        = {st['ev']:+.3f}%")
    print(f"  median    = {st['median']:+.3f}%")
    print(f"  σ (stdev) = {st['stdev']:.3f}%")
    print(f"  SE        = {st['se']:.3f}%")
    print(f"  t-stat    = {st['t']:+.2f}")
    print(f"  win率     = {st['win']:.1f}%")
    print(f"  cumul     = {st['cumul']:+.2f}% (単純加算、time-ordered なし)")
    print(f"  range     = {st['min']:+.2f}% .. {st['max']:+.2f}%")

    if args.bootstrap and st["n"] >= 2:
        lo, hi = _bootstrap_ci(vals, n_iter=args.bootstrap_iter)
        print(f"  CI 95%    = [{lo:+.3f}%, {hi:+.3f}%] (bootstrap n_iter={args.bootstrap_iter})")

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

    if args.histogram:
        print(f"\nhistogram (bins={args.histogram_bins}):")
        for line in _ascii_histogram(vals, bins=args.histogram_bins):
            print(line)

    if args.plot_cumul:
        # event_date 順
        ordered = sorted(filtered, key=lambda r: r["event_date"])
        oc_vals = [(r.get("attrs") or {}).get(metric) for r in ordered]
        oc_vals = [float(v) for v in oc_vals if v is not None]
        print()
        for line in _ascii_cumul(oc_vals):
            print(line)

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
    ap.add_argument("--group-by", choices=list(_GROUP_KEYS), help="グルーピング集計 (subpattern/disc_time_bucket/year/code)")
    ap.add_argument("--histogram", action="store_true", help="ASCII ヒストグラムを表示")
    ap.add_argument("--histogram-bins", type=int, default=20)
    ap.add_argument("--bootstrap", action="store_true", help="平均の bootstrap 95% CI")
    ap.add_argument("--bootstrap-iter", type=int, default=2000)
    ap.add_argument("--plot-cumul", action="store_true", help="event_date 順の累積 PnL ASCII プロット")
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
        if args.bootstrap and len(vals) >= 2:
            lo, hi = _bootstrap_ci(vals, n_iter=args.bootstrap_iter)
            result["bootstrap_ci"] = {"lo": lo, "hi": hi, "alpha": 0.05}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.group_by:
        _print_group(filtered, args.metric, args.group_by)
        return

    _print_human(filtered, args.metric, args)


if __name__ == "__main__":
    main()

"""query_* CLI 共通の集計・表示ヘルパ (source 非依存)。

query_po / query_holdings が共有する human サマリ・group-by 出力をまとめる。
metric 値の取り出しと group キーは呼び出し側が callable で渡すことで、
ソース固有のフィルタ/軸だけ各 query_* に置けばよい構成にする。

統計・プロットの実体は query_kouaku の検証済ヘルパを流用 (重複実装を避ける)。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable, Sequence

from scripts.query_kouaku import _ascii_cumul, _ascii_histogram, _bootstrap_ci, _stats


def metric_values(records: Sequence[dict[str, Any]], metric: str, *, collapse: bool = False) -> list[float]:
    """records の attrs から metric 値 (非 None) を float リストで取り出す。

    collapse=True のとき、同一 (event_date, code) を 1 観測に平均集約する。
    同一銘柄・同一日の複数レコード (例: 同日複数提出者の大量保有報告) は翌日リターンが
    同値で独立でないため、n/t の水増しを避けたい場合に使う。日付順に並べて返す
    (累積プロットの順序を保つため)。
    """
    if not collapse:
        out: list[float] = []
        for r in records:
            v = (r.get("attrs") or {}).get(metric)
            if v is not None:
                out.append(float(v))
        return out
    groups: dict[tuple[Any, Any], list[float]] = defaultdict(list)
    for r in records:
        v = (r.get("attrs") or {}).get(metric)
        if v is not None:
            groups[(r.get("event_date"), r.get("code"))].append(float(v))
    return [sum(vs) / len(vs) for _, vs in sorted(groups.items())]


def summarize(
    records: Sequence[dict[str, Any]],
    metric: str,
    *,
    filter_label: str = "",
    bootstrap: bool = False,
    bootstrap_iter: int = 2000,
    histogram: bool = False,
    histogram_bins: int = 20,
    plot_cumul: bool = False,
    dims: Sequence[tuple[str, Callable[[dict[str, Any]], Any]]] = (),
    list_records: bool = False,
    collapse: bool = False,
) -> None:
    """フィルタ済 records の単一集計を stdout に出す (query_kouaku._print_human 相当)。"""
    vals = metric_values(records, metric, collapse=collapse)
    st = _stats(vals)
    print("=" * 60)
    print(f"filter: {filter_label or '(none)'}")
    print(f"metric: {metric}" + ("  [collapse-daily: 同一 code+date を1観測に集約]" if collapse else ""))
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

    if bootstrap and st["n"] >= 2:
        lo, hi = _bootstrap_ci(vals, n_iter=bootstrap_iter)
        print(f"  CI 95%    = [{lo:+.3f}%, {hi:+.3f}%] (bootstrap n_iter={bootstrap_iter})")

    for name, getter in dims:
        c = Counter(getter(r) for r in records)
        if len(c) > 1:
            print(f"\n{name} 分布:")
            for k, n in c.most_common():
                print(f"    {k}: {n}")

    if histogram:
        print(f"\nhistogram (bins={histogram_bins}):")
        for line in _ascii_histogram(vals, bins=histogram_bins):
            print(line)

    if plot_cumul:
        ordered = sorted(records, key=lambda r: r.get("event_date", ""))
        print()
        for line in _ascii_cumul(metric_values(ordered, metric, collapse=collapse)):
            print(line)

    if list_records:
        print("\n=== records (sorted by event_date) ===")
        for r in sorted(records, key=lambda x: x.get("event_date", "")):
            v = (r.get("attrs") or {}).get(metric)
            vs = f"{v:+.2f}%" if v is not None else "--"
            print(f"  {r.get('event_date','?')} {r.get('code','?'):>5}  {metric}={vs}")


def group_table(
    records: Sequence[dict[str, Any]],
    metric: str,
    grouper: Callable[[dict[str, Any]], Any],
    *,
    group_by: str,
    collapse: bool = False,
) -> None:
    """grouper(rec)->key で grouping し各 cell の n/EV/σ/t/win/cumul を 1 行で出す。"""
    groups: dict[Any, list[dict[str, Any]]] = {}
    for r in records:
        groups.setdefault(grouper(r), []).append(r)

    print("=" * 74)
    note = "  [collapse-daily]" if collapse else ""
    print(f"group_by={group_by}  metric={metric}  total filtered={len(records)}{note}")
    print("-" * 74)
    print(f"  {'key':26s} {'n':>4s}  {'EV':>8s}  {'σ':>6s}  {'t':>6s}  {'win':>5s}  {'cumul':>8s}")
    print(f"  {'-'*26} {'-'*4}  {'-'*8}  {'-'*6}  {'-'*6}  {'-'*5}  {'-'*8}")

    rows: list[tuple[Any, dict[str, float], int]] = []
    for k, recs in groups.items():
        rows.append((k, _stats(metric_values(recs, metric, collapse=collapse)), len(recs)))
    # |t| 降順 (n>=3 のみ、それ以外は末尾)
    rows.sort(key=lambda x: -(abs(x[1].get("t", 0.0)) if x[1].get("n", 0) >= 3 else -1.0))
    for k, st, n_rec in rows:
        n = st.get("n", 0)
        if n < 1:
            print(f"  {str(k):26s} {n_rec:>4d}  (no metric)")
            continue
        marker = " ★" if n >= 5 and abs(st["t"]) >= 2 else ""
        print(
            f"  {str(k):26s} {n:>4d}  {st['ev']:+7.3f}%  {st['stdev']:5.2f}%  "
            f"{st['t']:+6.2f}  {st['win']:4.0f}%  {st['cumul']:+7.2f}%{marker}"
        )
    print("\n  (★ = n>=5 かつ |t|>=2)")

"""⑩R の S高“型”別検証: S高引け(C=上限) vs ザラ場タッチ剥がれ(C<上限) vs 値幅拡大。

⑩R母体は UL=1(=日中S高タッチ)だが、内訳で効きが大きく違う。S高日のOHLC＋値幅制限テーブルで
a=S高引け / b=タッチ剥がれ / c=値幅拡大 に分類し、翌日寄→引 SHORT の EV/勝率/t を比較する。

所見(2026-06): a S高引け +2.95%/勝60%/t5.0/n318 ≫ b タッチ剥がれ +0.59%/勝59%/t0.4/n56(非有意)。
＝⑩Rの実体は「S高で“引けた”」。タッチして剥がれた型(5367型)はエッジ薄い→対象外にすべき。
値幅拡大(c)はn3で判定不能(超強力ゆえ翌日大GU化し中GU条件から外れがち)。

価格cache: cache/sh_abc_bars.json (⑩Rコードの日足OHLC・2017-)。出力: reports/r10_sh_close.md
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
from pathlib import Path

from scripts._atomic import atomic_write_json, atomic_write_text
from scripts.edge_candidates.analyze_archive_regime import clustered_t
from scripts.edge_candidates.verify_edges_standalone import _load, edge_rows

REPO = Path(__file__).resolve().parent.parent.parent
CACHE = REPO / "cache" / "sh_abc_bars.json"
REPORT = REPO / "reports" / "r10_sh_close.md"
SHORT_COST = 0.15
# 値幅制限テーブル (基準値<hi → 制限値幅 w)
_LIMIT = [(100, 30), (200, 50), (500, 80), (700, 100), (1000, 150), (1500, 300), (2000, 400),
          (3000, 500), (5000, 700), (7000, 1000), (10000, 1500), (15000, 3000), (20000, 4000),
          (30000, 5000), (50000, 7000), (70000, 10000), (100000, 15000), (150000, 30000),
          (200000, 40000), (300000, 50000), (500000, 70000), (700000, 100000), (1000000, 150000)]


def _width(p: float) -> float:
    for hi, w in _LIMIT:
        if p < hi:
            return w
    return 300000


def _c5(c: str) -> str:
    return c + "0" if len(c) == 4 else c


def fetch(codes: list[str]) -> dict[str, dict]:
    """⑩Rコードの日足OHLC {date:[O,H,L,C]} を取得 (cache・2017-。契約範囲外の2016以前は不可)。"""
    from scripts import _jquants
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    for i, c in enumerate([c for c in codes if c not in cache], 1):
        try:
            b = _jquants.get_list("/equities/bars/daily", code=_c5(c), **{"from": "2017-01-01", "to": "2026-06-16"})
            cache[c] = {x["Date"]: [x.get("O"), x.get("H"), x.get("L"), x.get("C")] for x in b if x.get("C")}
        except _jquants.JQuantsError:
            cache[c] = {}
        if i % 50 == 0:
            atomic_write_json(CACHE, cache)
    atomic_write_json(CACHE, cache)
    return cache


def build(D: dict, cache: dict) -> str:
    """S高型別の ⑩R(中GU)翌日寄→引 SHORT を集計した md。"""
    rows, _, _ = edge_rows("⑩R", D)
    seen, ev = set(), []
    for r in rows:
        if (r[1], r[2]) not in seen:
            seen.add((r[1], r[2]))
            ev.append((r[0], r[1], r[2]))   # io, S高日 d0, code
    cal = sorted(D["tpx"])

    def prevd(d):
        i = bisect.bisect_left(cal, d)
        return cal[i - 1] if i > 0 else None

    groups: dict[str, list] = {"a": [], "b": [], "c": []}
    for io, d0, code in ev:
        b = cache.get(code, {})
        pv = prevd(d0)
        if d0 not in b or pv not in b:
            continue
        _, h, _, c = b[d0]
        pc = b[pv][3]
        if not (h and c and pc):
            continue
        lim = pc + _width(pc)
        if h > lim * 1.002:
            groups["c"].append((io, d0))
        elif c >= lim * 0.998:
            groups["a"].append((io, d0))
        else:
            groups["b"].append((io, d0))

    def st(L):
        if len(L) < 5:
            return f"n{len(L)}（判定不能）"
        nets = [-x[0] - SHORT_COST for x in L]
        win = sum(1 for v in nets if v > 0) / len(nets) * 100
        t = clustered_t(nets, [x[1] for x in L])
        return f"EV{statistics.fmean(nets):+.2f}% / 勝{win:.0f}% / t_clust{t:+.1f} / n{len(L)}"

    allL = groups["a"] + groups["b"] + groups["c"]
    L = ["# ⑩R S高型別: 翌日寄→引 SHORT (cost0.15)", "",
         "S高日のOHLC+値幅制限テーブルで分類。a=S高引け(C=上限) / b=タッチ剥がれ(C<上限) / c=値幅拡大(H>通常上限)。", "",
         "| 型 | 成績 |", "|---|---|",
         f"| **a S高引け** | {st(groups['a'])} |",
         f"| b タッチ剥がれ | {st(groups['b'])} |",
         f"| c 値幅拡大 | {st(groups['c'])} |",
         f"| 全体 | {st(allL)} |", "",
         "結論: ⑩Rの実体は**S高引け(a)**。タッチ剥がれ(b)はEV1/5・t≈0でエッジ薄い→対象外。"
         "＝⑩Rの条件は『ザラ場タッチ(UL=1)』でなく『S高で引けた(C=上限)』に絞るべき。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true", help="キャッシュのみで集計")
    ap.add_argument("--out", type=Path, default=REPORT, help="出力 md (既定 reports/r10_sh_close.md)")
    args = ap.parse_args()
    D = _load()
    rows, _, _ = edge_rows("⑩R", D)
    codes = sorted({r[2] for r in rows})
    cache = json.loads(CACHE.read_text()) if (args.no_fetch and CACHE.exists()) else fetch(codes)
    report = build(D, cache)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, report)
    print(report)
    print(f"[r10_sh_close] → {args.out}")


if __name__ == "__main__":
    main()

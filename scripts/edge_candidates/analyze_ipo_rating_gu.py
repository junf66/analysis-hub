"""IPO 評価ランク(96ut)×初値GU で初値→当日引けデイトレを検証。

仮説(ユーザー): IPO上場日に「評価A/Bかつ初値GUがX%以内」なら初値買い→当日引け売りで期待値。
データ: data/edge_candidates/ipo_96ut_ratings.json (96ut.com 初値結果・評価/初値/初値騰落率を手動転記)。
価格: /equities/bars/daily を code 指定で取得し、生始値が初値に一致する日(=上場日)の 始値→終値。
分割銘柄は AdjO≠生初値で母体から落ちるため**生(raw)始値・終値**で照合・計算する(C/O比は分割不変)。

結論(2024-2026 A系コード n155): 初値買い→引けは全体 net-0.79%/勝率41%=エッジなし(初値天井)。
A/B×GU≤10%は+1.72%だがn11/t0.54/勝45%で非有意。良評価=大GUに偏り「良評価×GU小」が稀で立証不能。

出力: reports/ipo_rating_gu.md / 価格cache: cache/ipo_bars_raw.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

from scripts._atomic import atomic_write_json, atomic_write_text

REPO = Path(__file__).resolve().parent.parent.parent
DATA = REPO / "data" / "edge_candidates" / "ipo_96ut_ratings.json"
CACHE = REPO / "cache" / "ipo_bars_raw.json"
REPORT = REPO / "reports" / "ipo_rating_gu.md"
COST = 0.3   # IPO初日デイトレの保守的コスト(滑り含む)


def fetch_bars(codes: list[str]) -> dict[str, list]:
    """code→[[date,O,C]] 生始値/終値 (cache 併用・resume)。"""
    from scripts import _jquants
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [c for c in codes if c not in cache]
    for i, c in enumerate(todo, 1):
        cc = c + "0" if len(c) == 4 else c
        try:
            b = _jquants.get_list("/equities/bars/daily", code=cc, **{"from": "2024-01-01", "to": "2026-06-16"})
            cache[c] = [[x["Date"], x.get("O"), x.get("C")] for x in b if x.get("O") and x.get("C")]
        except _jquants.JQuantsError:
            cache[c] = []
        if i % 40 == 0:
            atomic_write_json(CACHE, cache)
    atomic_write_json(CACHE, cache)
    return cache


def daytrade_rows(recs: list[dict], cache: dict[str, list]) -> tuple[list[tuple], list[str]]:
    """各IPOの (rank, gu, 初値→引け%, code) と未照合code。"""
    rows, un = [], []
    for r in recs:
        code, rank, h, gu = r["code"], r["rank"], r["hatsune"], r["gu_pct"]
        best = next(((d, o, c) for d, o, c in cache.get(code, []) if o and abs(o - h) / h < 0.015), None)
        if not best:
            un.append(code)
            continue
        _, o, c = best
        rows.append((rank, gu, (c / o - 1) * 100, code))
    return rows, un


def _stat(sub: list[tuple]) -> str:
    if not sub:
        return "n0"
    raw = [x[2] for x in sub]
    net = [x - COST for x in raw]
    win = sum(1 for x in net if x > 0) / len(net) * 100
    se = statistics.pstdev(net) / math.sqrt(len(net)) if len(net) > 1 else 0
    t = statistics.fmean(net) / se if se else 0
    return f"n{len(net)} / raw{statistics.fmean(raw):+.2f}% / net{statistics.fmean(net):+.2f}% / 勝{win:.0f}% / t{t:+.2f}"


def build(recs: list[dict], cache: dict[str, list]) -> str:
    """評価×GU の初値→引けデイトレ表を md で返す。"""
    rows, un = daytrade_rows(recs, cache)
    gb = [("GD≤0", lambda g: g <= 0), ("0-5", lambda g: 0 < g <= 5), ("5-10", lambda g: 5 < g <= 10),
          ("10-20", lambda g: 10 < g <= 20), (">20", lambda g: g > 20)]
    L = ["# IPO 評価×初値GU: 初値→当日引けデイトレ検証", "",
         f"96ut評価/初値GUは手動転記、初値→引けは生始値→終値(分割不変)。cost{COST}%。",
         f"照合 {len(rows)}/{len(recs)} (未照合{len(un)}: 転記/分割/コード差)。", "",
         "| 評価 | GU帯 | 成績 |", "|---|---|---|"]
    for rk in ["A", "B", "C", "D"]:
        sub = [x for x in rows if x[0] == rk]
        L.append(f"| {rk} | 全 | {_stat(sub)} |")
        for lab, f in gb:
            L.append(f"| {rk} | {lab} | {_stat([x for x in sub if f(x[1])])} |")
    L += ["", "## 仮説検証", "", "| 条件 | 成績 |", "|---|---|"]
    tests = [("A/B×GU≤10", lambda x: x[0] in "AB" and x[1] <= 10),
             ("A/B×GU≤5", lambda x: x[0] in "AB" and x[1] <= 5),
             ("C×GU≤5", lambda x: x[0] == "C" and x[1] <= 5),
             ("C×GU≤10", lambda x: x[0] == "C" and x[1] <= 10),
             ("全GD≤0", lambda x: x[1] <= 0), ("全体", lambda x: True)]
    for lab, f in tests:
        L.append(f"| {lab} | {_stat([x for x in rows if f(x)])} |")
    L += ["", "結論: 初値買い→引けは全体エッジなし(初値天井)。良評価A/B×GU小は方向性+だが"
          "n過少・非有意・勝率≤50%(良評価=大GUに偏在し稀)。不採用。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true", help="キャッシュのみで集計")
    ap.add_argument("--out", type=Path, default=REPORT, help="出力 md (既定 reports/ipo_rating_gu.md)")
    args = ap.parse_args()
    recs = json.loads(DATA.read_text())["records"]
    cache = (json.loads(CACHE.read_text()) if (args.no_fetch and CACHE.exists())
             else fetch_bars([r["code"] for r in recs]))
    report = build(recs, cache)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, report)
    print(report)
    print(f"[ipo_rating_gu] → {args.out}")


if __name__ == "__main__":
    main()

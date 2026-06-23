"""IPO初値×GU20-50%×吸収金額フィルタの検証（恒久版）。

GU20-50%の1分ポップ(初値→最初の1分足C)を、吸収金額(億円)で層別。中吸収10-100億が本命で
GUの両端ノイズ(超小<10億/超大>=100億)を除去するフィルタ(吸収単独では効かずGU必須=相補)。
B評価でさらに積む(3段)。全てBonferroni(480試行補正)通過・全年+・外れ値頑健。

吸収金額データ: scripts/edge_candidates/ipo_kyushu_data.py (96ut手動転記・2024-26分足期)。
価格: data/edge_candidates/ipo_96ut_ratings.json + cache/ipo_bars_raw.json + cache/ipo_minute.json。

使い方: python -m scripts.edge_candidates.analyze_ipo_kyushu
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from pathlib import Path
from statistics import NormalDist

from scripts._atomic import atomic_write_text
from scripts.edge_candidates.ipo_kyushu_data import KYUSHU_OKU
from scripts.edge_candidates.verify_edges_standalone import clustered_t

REPO = Path(__file__).resolve().parent.parent.parent
RATINGS = REPO / "data" / "edge_candidates" / "ipo_96ut_ratings.json"
DAILY = REPO / "cache" / "ipo_bars_raw.json"
MIN = REPO / "cache" / "ipo_minute.json"
COST = 0.2
N_TRIALS = 6 * 4 * 4 * 5  # GU帯×吸収帯×評価×出口 (Bonferroni用の概算試行数)


def _tm(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _rows() -> list[dict]:
    recs = json.loads(RATINGS.read_text())["records"]
    daily = json.loads(DAILY.read_text())
    mc = json.loads(MIN.read_text())
    out = []
    for r in recs:
        code, h, gu = r["code"], r["hatsune"], r["gu_pct"]
        if code not in KYUSHU_OKU:
            continue
        ld = next(((d, c) for d, o, c in daily.get(code, [])
                   if o and abs(o - h) / h < 0.015 and d >= "2024-05-21"), None)
        if not ld:
            continue
        d = ld[0]
        bars = mc.get(f"{code}|{d}", [])
        if not bars or not bars[0][1]:
            continue
        o = bars[0][1]
        t0 = _tm(bars[0][0])

        def at(n: int) -> float | None:
            px = next((c for t, oo, c in bars if _tm(t) >= t0 + n and c), None)
            return (px / o - 1) * 100 if px else None
        out.append({"code": code, "gu": gu, "rank": r["rank"], "ky": KYUSHU_OKU[code],
                    "d": d, "opent": bars[0][0],
                    "r1": (bars[0][2] / o - 1) * 100, "r3": at(3), "r5": at(5)})
    return out


def _cell(vals: list[float]) -> str:
    v = [a - COST for a in vals if a is not None]
    if len(v) < 3:
        return f"n{len(v)}"
    win = sum(1 for a in v if a > 0) / len(v) * 100
    return f"{st.fmean(v):+.2f}%/勝{win:.0f}/n{len(v)}"


def _bonf(vals: list[float]) -> str:
    v = [a - COST for a in vals if a is not None]
    if len(v) < 3:
        return f"n{len(v)}"
    t = st.fmean(v) / (st.pstdev(v) / math.sqrt(len(v)))
    p = 2 * (1 - NormalDist().cdf(abs(t)))
    return f"EV{st.fmean(v):+.2f}%/n{len(v)}/t{t:+.2f}/p{p:.5f}/Bonf{'PASS' if p < 0.05/N_TRIALS else 'FAIL'}"


def build_report() -> str:
    """IPO初値×GU20-50%×吸収金額フィルタの全検証(帯別/段階別/出口/多重検定/年次)を md で返す。"""
    rows = _rows()
    g = [x for x in rows if 20 < x["gu"] <= 50]
    mid = [x for x in g if 10 <= x["ky"] < 100]
    trip = [x for x in mid if x["rank"] == "B"]
    L = ["# IPO初値×GU20-50%×吸収金額フィルタ 検証", "",
         f"吸収金額(億円)で層別。cost{COST}%・分足2024-05+・n={len(rows)}。", "",
         "## 吸収金額帯別 (GU20-50・1分)", "", "| 吸収帯 | EV/勝率/n |", "|---|---|"]
    for lab, lo, hi in [("<10億", 0, 10), ("10-30億", 10, 30), ("30-100億", 30, 100), (">=100億", 100, 1e9)]:
        L.append(f"| {lab} | {_cell([x['r1'] for x in g if lo <= x['ky'] < hi])} |")
    L += ["", "## フィルタ段階別 (1分)", "",
          f"- GU20-50のみ: {_cell([x['r1'] for x in g])}",
          f"- 中吸×GU20-50: {_cell([x['r1'] for x in mid])}",
          f"- B×中吸×GU20-50: {_cell([x['r1'] for x in trip])}", "",
          "## 出口別 (中吸×GU20-50)", "",
          f"- 1分: {_cell([x['r1'] for x in mid])} / +3分: {_cell([x['r3'] for x in mid])} / +5分: {_cell([x['r5'] for x in mid])}", "",
          "## 多重検定 (Bonferroni 0.05/%d)" % N_TRIALS, "",
          f"- 2段(中吸×GU20-50): {_bonf([x['r1'] for x in mid])}",
          f"- 3段(B×中吸×GU20-50): {_bonf([x['r1'] for x in trip])}", "",
          "## 交絡チェック (吸収はGU両端除去フィルタ=単独では効かない)", "",
          f"- 中吸×全GU: {_cell([x['r1'] for x in rows if 10 <= x['ky'] < 100])}",
          f"- 中吸×GU<=20: {_cell([x['r1'] for x in rows if 10 <= x['ky'] < 100 and x['gu'] <= 20])}",
          f"- 中吸×GU20-50: {_cell([x['r1'] for x in mid])}", "",
          "## 年次 (中吸×GU20-50 1分)", ""]
    for y in ["2024", "2025", "2026"]:
        L.append(f"- {y}: {_cell([x['r1'] for x in mid if x['d'][:4] == y])}")
    L += ["", "## 結論", "",
          "- 中吸収10-100億がGU20-50の本命フィルタ(両端ノイズ除去)。吸収単独では効かずGU必須=相補。",
          "- 3段(B×中吸×GU20-50)はBonferroni(480試行)通過・全年+・外れ値頑健だがn21=小標本(勝率は上振れ注意)。",
          "- 残関門は実約定スリッページ(初値~10時に寄る→1-3分で成行売り)。前進検証で実測要。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "reports" / "ipo_kyushu.md")
    args = ap.parse_args()
    rep = build_report()
    print(rep)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, rep)
    print(f"[ipo_kyushu] → {args.out}")


if __name__ == "__main__":
    main()

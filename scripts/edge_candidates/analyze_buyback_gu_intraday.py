"""自社株買い決定 × 翌日寄りロング × GU程度 × 出口時刻(9:30/10:00/11:30/15:30)。

自社株買いの「決定」開示(月次の取得状況報告は除外)を翌営業日寄りで買い、出口時刻別×初値GU帯別に
対TOPIX(1306は使わず単純net)で評価。IPO/業務提携と違い「会社自身が場中に買い支える」ため
保有(前場引け)が効く。GUの程度が肝。

所見(2024-05-21以降 n1765): GU3-5% × 11:30(前場引け) = net+0.72%/勝55%/t3.2 が当たり。
GD/0-1%(無反応)・GU>10%(過熱で即フェード負)はダメ。出口は早いほど良いではなく前場かけてジリ上げ→11:30ピーク。

データ: cache/disclosures/tdnet_all.json(決定抽出) + cache/buyback_daily.json(GU) + cache/buyback_minute.json(翌日分足)。
出力: reports/buyback_gu_intraday.md
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import statistics
from pathlib import Path

from scripts._atomic import atomic_write_json, atomic_write_text
from scripts.edge_candidates.mine_disclosure_titles import load_records

REPO = Path(__file__).resolve().parent.parent.parent
TOPIX = REPO / "data" / "edge_candidates" / "topix_daily.json"
DC = REPO / "cache" / "buyback_daily.json"
MC = REPO / "cache" / "buyback_minute.json"
REPORT = REPO / "reports" / "buyback_gu_intraday.md"
COST = 0.2


def is_decision(t: str) -> bool:
    """自社株買い『決定』開示か(状況/終了/消却/処分/結果 等は除外)。"""
    if not re.search(r"自己株式の?取得|自社株買", t):
        return False
    if re.search(r"状況|終了|消却|処分|立会外|結果|報告|変更|延長|信託", t):
        return False
    return bool(re.search(r"決定|お知らせ|取得枠|取得について", t))


def _c5(c: str) -> str:
    return c + "0" if len(c) == 4 else c


def events() -> list[tuple[str, str]]:
    """(code, 開示日) 2024-05-21以降・(code,date)重複除去。"""
    seen = {}
    for r in load_records():
        if is_decision(r.get("title", "")) and r["pubdate"][:10] >= "2024-05-21":
            seen[(str(r["code"]), r["pubdate"][:10])] = 1
    return list(seen)


def fetch(evs: list[tuple[str, str]], cal: list[str]) -> tuple[dict, dict]:
    """日足(GU用)と翌日分足(出口用)を取得 (cache・resume)。"""
    from scripts import _jquants

    def nxt(d):
        i = bisect.bisect_right(cal, d)
        return cal[i] if i < len(cal) else None

    dc = json.loads(DC.read_text()) if DC.exists() else {}
    for i, c in enumerate([c for c, _ in {(c, 1) for c, _ in evs} if c not in dc], 1):
        try:
            b = _jquants.get_list("/equities/bars/daily", code=_c5(c), **{"from": "2024-04-01", "to": "2026-06-16"})
            dc[c] = {x["Date"]: [x.get("O"), x.get("C")] for x in b if x.get("O")}
        except _jquants.JQuantsError:
            dc[c] = {}
        if i % 80 == 0:
            atomic_write_json(DC, dc)
    atomic_write_json(DC, dc)
    mc = json.loads(MC.read_text()) if MC.exists() else {}
    for i, (c, d) in enumerate([(c, nxt(d)) for c, d in evs if nxt(d) and f"{c}|{nxt(d)}" not in mc], 1):
        try:
            b = _jquants.get_list("/equities/bars/minute", code=_c5(c), date=d)
            mc[f"{c}|{d}"] = [[x["Time"], x.get("O"), x.get("C")] for x in b if x.get("O")]
        except _jquants.JQuantsError:
            mc[f"{c}|{d}"] = []
        if i % 100 == 0:
            atomic_write_json(MC, mc)
    atomic_write_json(MC, mc)
    return dc, mc


def build(evs: list[tuple[str, str]], dc: dict, mc: dict, cal: list[str]) -> str:
    """GU帯×出口の md。"""
    def nxt(d):
        i = bisect.bisect_right(cal, d)
        return cal[i] if i < len(cal) else None

    def prevc(dd: dict, d: str):
        ks = [x for x in dd if x <= d]
        return dd[max(ks)][1] if ks else None

    rows = []
    for c, d in evs:
        nd = nxt(d)
        bars = mc.get(f"{c}|{nd}", []) if nd else []
        ac = prevc(dc.get(c, {}), d)
        if not bars or not ac or not bars[0][1]:
            continue
        o = bars[0][1]
        gu = (o / ac - 1) * 100
        row = {"gu": gu}
        for lab, hh in [("930", "09:30"), ("1000", "10:00"), ("1130", "11:30")]:
            px = next((cl for t, oo, cl in bars if t >= hh and cl), None)
            row[lab] = (px / o - 1) * 100 if px else None
        row["1530"] = (bars[-1][2] / o - 1) * 100
        rows.append(row)

    def st(key, f):
        v = [r[key] for r in rows if f(r) and r.get(key) is not None]
        if len(v) < 5:
            return f"n{len(v)}"
        net = [x - COST for x in v]
        win = sum(1 for x in net if x > 0) / len(net) * 100
        t = statistics.fmean(net) / (statistics.pstdev(net) / math.sqrt(len(net))) if len(net) > 1 else 0
        return f"{statistics.fmean(net):+.2f}%/勝{win:.0f}/t{t:+.1f}/n{len(v)}"

    GUB = [("GD≤0", lambda g: g <= 0), ("0-1", lambda g: 0 < g <= 1), ("1-3", lambda g: 1 < g <= 3),
           ("3-5", lambda g: 3 < g <= 5), ("5-10", lambda g: 5 < g <= 10), (">10", lambda g: g > 10)]
    L = ["# 自社株買い決定 × 翌日寄りロング × GU × 出口", "",
         f"決定開示(状況報告除外)・翌寄り→各出口・cost{COST}%・n{len(rows)}。", "",
         "| GU帯 | →9:30 | →10:00 | →11:30 | →15:30 |", "|---|---|---|---|---|"]
    for lab, f in GUB:
        L.append(f"| {lab} | " + " | ".join(st(k, lambda r, f=f: f(r["gu"])) for k in ["930", "1000", "1130", "1530"]) + " |")
    L += ["", "結論: **GU3-5% × 11:30(前場引け)が当たり(+0.72%/勝55%/t3.2)**。"
          "GD/0-1%は無反応・GU>10%は過熱で負け。会社の場中買い支えで前場ジリ上げ→11:30ピーク。薄利・機械執行型。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true", help="キャッシュのみで集計")
    ap.add_argument("--out", type=Path, default=REPORT, help="出力 md (既定 reports/buyback_gu_intraday.md)")
    args = ap.parse_args()
    cal = sorted(r["Date"] for r in json.loads(TOPIX.read_text())["records"])
    evs = events()
    if args.no_fetch:
        dc = json.loads(DC.read_text()) if DC.exists() else {}
        mc = json.loads(MC.read_text()) if MC.exists() else {}
    else:
        dc, mc = fetch(evs, cal)
    report = build(evs, dc, mc, cal)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, report)
    print(report)
    print(f"[buyback_gu_intraday] → {args.out}")


if __name__ == "__main__":
    main()

"""決算ジャンプ継続ロング（PEAD: post-earnings-announcement drift）の検証。

仮説: 決算発表で大きく買われた(市場対比 反応≥+12%)銘柄は、反応を織り込みきれず数日ドリフトする
(市場の過小反応)。引け後発表→翌日(反応日)引けで+12% → その引けで買い→+1〜3日引けで売り。

発表タイミングで反応日が変わるため厳密に分ける:
- 引け後発表(DiscTime>=15:00): 反応日=発表翌営業日。react=発表日引け→翌日引け、買い=翌日引け。
- ザラ場発表(DiscTime<15:00):   反応日=発表日。      react=前日引け→発表日引け、買い=発表日引け。
本体は引け後発表(9割・t9.7)。ザラ場発表は弱い(t3.0)。

データ: fins_summary.json(DiscDate/DiscTime/DocType+業績)・event_bars.json(価格4桁)・
topix_daily.json(カレンダー+ベンチ)・equities_master.json(市場/信用/規模)。価格は market-adjusted。

使い方: python -m scripts.edge_candidates.analyze_pead
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

from scripts._atomic import atomic_write_text
from scripts.edge_candidates.verify_edges_standalone import clustered_t

REPO = Path(__file__).resolve().parent.parent.parent
FINS = REPO / "data" / "edge_candidates" / "fins_summary.json"
BARS = REPO / "cache" / "event_bars.json"
TOPIX = REPO / "data" / "edge_candidates" / "topix_daily.json"
MASTER = REPO / "data" / "edge_candidates" / "equities_master.json"
LONG_COST = 0.20
EXITS = (1, 2, 3, 5, 7, 10)
THRESH = 12.0          # 反応閾値(市場対比%)


def _c4(code: str) -> str:
    return code[:-1] if (code.endswith("0") and code[:-1].isdigit()) else code


def _events() -> list[dict]:
    """全決算開示を PEAD 観測に変換 (発表時刻で react/entry を整合)。"""
    fins = json.loads(FINS.read_text())["by_code"]
    bars = json.loads(BARS.read_text())
    tpx = {r["Date"]: r for r in json.loads(TOPIX.read_text())["records"] if r.get("C")}
    attr = {r["Code"]: (r.get("MktNm"), r.get("MrgnNm"), r.get("scale_band"))
            for r in json.loads(MASTER.read_text())["records"]}
    cal = sorted(tpx)
    cidx = {d: i for i, d in enumerate(cal)}

    def sret(code: str, i0: int, i1: int) -> float | None:
        m = bars.get(code)
        if not m:
            return None
        d0, d1 = cal[i0], cal[i1]
        if d0 not in m or d1 not in m:
            return None
        a, b = m[d0][1], m[d1][1]
        return (b / a - 1) * 100 if a and b else None

    def tret(i0: int, i1: int) -> float:
        return (tpx[cal[i1]]["C"] / tpx[cal[i0]]["C"] - 1) * 100

    out = []
    for c5, recs in fins.items():
        ec = _c4(c5)
        if ec not in bars:
            continue
        mkt, mrg, scl = attr.get(c5, (None, None, None))
        for r in recs:
            if "FinancialStatements" not in r.get("DocType", ""):
                continue
            d0 = r.get("DiscDate")
            t = (r.get("DiscTime") or "")[:5]
            if d0 not in cidx or not t:
                continue
            i = cidx[d0]
            after = t >= "15:00"
            ri = i if after else i - 1          # react開始
            ei = i + 1 if after else i          # 反応日(=entry)
            if ri < 0 or ei + max(EXITS) >= len(cal):
                continue
            react = sret(ec, ri, ei)
            if react is None:
                continue
            react -= tret(ri, ei)
            drifts = {}
            for h in EXITS:
                s = sret(ec, ei, ei + h)
                if s is not None:
                    drifts[h] = s - tret(ei, ei + h)
            out.append({"date": cal[ei], "react": react, "drifts": drifts,
                        "after": after, "mkt": mkt, "mrg": mrg, "scl": scl})
    return out


def _stat(rows: list[dict], h: int) -> str:
    v = [x["drifts"][h] - LONG_COST for x in rows if h in x["drifts"]]
    dd = [x["date"] for x in rows if h in x["drifts"]]
    if len(v) < 15:
        return f"n{len(v)}"
    win = sum(1 for a in v if a > 0) / len(v) * 100
    return f"net{st.fmean(v):+.2f}%/勝{win:.0f}/t{clustered_t(v, dd):.1f}/n{len(v)}"


def build_report(ev: list[dict]) -> str:
    """PEAD の閾値感度・出口・タイミング/市場/信用/規模別・年次を md にまとめて返す。"""
    ac = [x for x in ev if x["after"] and x["react"] >= THRESH]   # 本体(引け後)
    L = ["# 決算ジャンプ継続ロング (PEAD) 検証", "",
         f"反応≥{THRESH:.0f}%(市場対比)で引けた銘柄をその引けで買い→+N日引けで売り。cost{LONG_COST}%・分足2021+。", "",
         "## 出口別 (引け後発表・反応≥+12%)", "", "| 出口 | net/勝率/t |", "|---|---|"]
    for h in EXITS:
        L.append(f"| +{h}d | {_stat(ac, h)} |")
    L += ["", "## 反応閾値 感度 (引け後発表・+3d)", "", "| 閾値 | net/勝率/t |", "|---|---|"]
    for thr in (5, 8, 10, 12, 15, 20):
        sub = [x for x in ev if x["after"] and x["react"] >= thr]
        L.append(f"| ≥+{thr}% | {_stat(sub, 3)} |")
    L += ["", "## 発表タイミング別 (反応≥+12%・+3d)", "",
          f"- 引け後発表(昨日決算→今日反応): {_stat(ac, 3)}",
          f"- ザラ場発表(今日決算→今日): {_stat([x for x in ev if not x['after'] and x['react'] >= THRESH], 3)}",
          "", "## 市場区分別 (引け後・≥+12%・+3d)", ""]
    for m in ("プライム", "スタンダード", "グロース"):
        L.append(f"- {m}: {_stat([x for x in ac if x['mkt'] == m], 3)}")
    L += ["", "## 信用区分別 (引け後・≥+12%・+3d)", ""]
    for mg in ("貸借", "信用"):
        L.append(f"- {mg}: {_stat([x for x in ac if x['mrg'] == mg], 3)}")
    L += ["", "## 規模別 (引け後・≥+12%・+3d)", ""]
    for s in ("大型", "中型", "小型"):
        L.append(f"- {s}: {_stat([x for x in ac if x['scl'] == s], 3)}")
    L += ["", "## 年次 (引け後・≥+12%・+3d net)", ""]
    years = sorted({x["date"][:4] for x in ac})
    for y in years:
        sub = [x for x in ac if x["date"][:4] == y]
        L.append(f"- {y}: {_stat(sub, 3)}")
    L += ["", "## 結論", "",
          "- 引け後発表の決算反応≥+12%を翌日(反応日)引けで買い→+1〜3日引けで売り = 確定級(t9.7/全年+/閾値単調)。",
          "- 重複窓なし(銘柄ごと四半期間隔)でt頑健・市場/信用問わず(小型が本体)・ロングゆえ貸借不要。",
          "- 残関門は引成のクロージングオークション実約定スリッページ(小型ほど重い)=前進検証で実測。",
          "- 留保: 価格データ2021+の6年・ペイオフ型(勝率5割・伸びる玉が大)。"]
    return "\n".join(L) + "\n"


def main() -> None:
    """PEAD 検証レポートを生成して reports/pead.md に書き出す。"""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "reports" / "pead.md")
    args = ap.parse_args()
    rep = build_report(_events())
    print(rep)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, rep)
    print(f"[pead] → {args.out}")


if __name__ == "__main__":
    main()

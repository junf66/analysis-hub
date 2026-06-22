"""PO発表翌日「朝場ロング」を分足で検証（①A再現＋規模/gap横断）。

①Aスキャル(大型PO×翌寄り→9:05-9:15売り)を実定義(market_cap≥5000億円)で分足から再計算し、
さらに規模(円閾値/ScaleCat)×gap(GD/フラット/GU)×出口時刻を横断して「どこで効くか」を切り分ける。

データ: cache/po_announce_minute.json (code5|date -> [[Time,O,C]])。announce普通の翌営業日(分足期
2024-05-21以降)を J-Quants /equities/bars/minute からfetch。再fetchは scripts/_jquants で
get_list("/equities/bars/minute", code=, date=YYYYMMDD)。long往復0.20% net。

所見(2026-06): ①A再現=≥5000億×GD×9:05 +0.82%/勝92%/t3.5/n19・9:30で消失(完全スキャル)。
GD不要(gap不問でも9:05+0.62%/t3.0)。スイートスポット≥5000億(≥1兆は弱・超大型ScaleCatのみだと死)。
規模が決定的・gapは副次。中型(ScaleCat)は朝も引けも+(=①B朝版)、小型はジリ下げ。

使い方: python -m scripts.edge_candidates.analyze_po_morning_long
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics as st
from pathlib import Path

from scripts._atomic import atomic_write_text
from scripts.edge_candidates.verify_edges_standalone import clustered_t

REPO = Path(__file__).resolve().parent.parent.parent
PO = REPO / "data" / "po_records.json"
MASTER = REPO / "data" / "edge_candidates" / "equities_master.json"
TOPIX = REPO / "data" / "edge_candidates" / "topix_daily.json"
CACHE = REPO / "cache" / "po_announce_minute.json"
COST = 0.20
EXITS = ["09:05", "09:10", "09:15", "09:30", "11:30", "15:30"]
MIN_DATE = "2024-05-21"   # 分足の存在開始


def _c5(c: str) -> str:
    return c + "0" if len(c) == 4 else c


def _rows() -> list[dict]:
    po = json.loads(PO.read_text())["records"]
    mst = {m["Code"]: m.get("scale_band") for m in json.loads(MASTER.read_text())["records"]}
    cal = sorted(r["Date"] for r in json.loads(TOPIX.read_text())["records"] if r.get("O"))
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    def nxt(d: str) -> str | None:
        i = bisect.bisect_right(cal, d)
        return cal[i] if i < len(cal) else None

    def px(bars: list, t: str) -> tuple:
        o = bars[0][1]
        cands = [c for tt, oo, c in bars if tt and tt <= t and c]
        return (cands[-1], o) if cands and o else (None, None)

    out = []
    for r in po:
        if r.get("stage") != "announce" or r.get("po_type") != "普通":
            continue
        nd = nxt(r.get("event_date", ""))
        if not nd or nd < MIN_DATE:
            continue
        code = _c5(r.get("code", ""))
        bars = cache.get(f"{code}|{nd}")
        if not bars or not bars[0][1]:
            continue
        row = {"date": nd, "mc": r.get("market_cap"), "size": mst.get(code),
               "gap": (r.get("attrs") or {}).get("gap_pct")}
        for t in EXITS:
            p, o = px(bars, t)
            row[t] = ((p / o - 1) * 100 - COST) if p else None
        out.append(row)
    return out


def _line(label: str, sub: list[dict]) -> list[str]:
    if len(sub) < 3:
        return [f"### {label} — n={len(sub)}（小・略）", ""]
    L = [f"### {label} — n={len(sub)}", "", "| 出口 | EV | 勝率 | t_clust |", "|---|--:|--:|--:|"]
    for t in EXITS:
        v = [(x[t], x["date"]) for x in sub if x.get(t) is not None]
        if not v:
            continue
        nets = [a for a, _ in v]
        dates = [d for _, d in v]
        L.append(f"| {t} | {st.fmean(nets):+.2f}% | "
                 f"{sum(1 for a in nets if a > 0) / len(nets) * 100:.0f}% | {clustered_t(nets, dates):+.2f} |")
    L.append("")
    return L


def build_report() -> str:
    """PO朝場ロングの規模×gap×出口グリッドを md で返す。"""
    rows = _rows()
    L = ["# PO発表翌日「朝場ロング」分足検証（①A再現＋規模/gap横断）", "",
         f"announce普通・分足期({MIN_DATE}+)・long往復{COST}% net・n={len(rows)}。"
         "出口は翌寄り買い→各時刻売り。", ""]
    if not rows:
        return "\n".join(L + ["_(cache/po_announce_minute.json 未生成。fetch要)_"])
    L += _line("≥5000億 × GD（①A実定義）", [r for r in rows if r["mc"] and r["mc"] >= 5000
                                          and r["gap"] is not None and r["gap"] <= -0.5])
    L += _line("≥5000億 × gap不問（GD緩和）", [r for r in rows if r["mc"] and r["mc"] >= 5000])
    L += _line("≥1兆 × gap不問", [r for r in rows if r["mc"] and r["mc"] >= 10000])
    for sz in ["大型", "中型", "小型"]:
        L += _line(f"ScaleCat={sz} × gap不問", [r for r in rows if r["size"] == sz])
    L += ["## 結論", "",
          "- **①A再現**: ≥5000億×GD×9:05 で勝率9割・t3超、9:30で消失＝完全スキャル(9:05-9:15)。",
          "- **GD不要**: ≥5000億 gap不問でも9:05有意＝大型POは朝場ポップ(GDは加点)。スイートスポット≥5000億(≥1兆は弱)。",
          "- **規模が決定的**: 中型(ScaleCat)は朝も引けも+(①B朝版)、小型はジリ下げ。gapより規模。",
          "- n19-23・分足2年・超スキャル執行依存ゆえFDR確定には未達＝🟡裁量/候補据置。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "reports" / "po_morning_long.md")
    args = ap.parse_args()
    report = build_report()
    print(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, report)
    print(f"[po_morning_long] → {args.out}")


if __name__ == "__main__":
    main()

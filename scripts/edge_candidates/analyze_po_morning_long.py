"""PO発表翌日「朝場ロング」(①A) を分足で検証 — 実行faithful版。

①Aスキャル(大型PO×翌寄り買い→朝場で利確)を実定義(market_cap≥5000億円)で分足から再計算し、
規模×gap深さ×出口で「どこが実際に取れるか」を切り分ける。

⚠️ **出口は固定時計(9:05/9:15等)でなく「寄り+N分」(実際の寄りからの相対)で測る**。
理由: 深GDは特別売り気配で寄りが遅延(9:06-9:24)する。固定9:15だと早寄り銘柄に長い反発窓を
与え遅寄りを落とす=見かけのプラスが出る(2026-06に固定時計版で誤判定→本版で訂正)。実約定は
「寄って成行買い→N分後成行売り」なので寄り+N分が忠実。

データ: cache/po_announce_minute.json (code5|date -> [[Time,O,C]])。announce普通の翌営業日(分足期
2024-05-21+)を J-Quants /equities/bars/minute からfetch。long往復0.20% net。

所見(2026-06・実行faithful): 取れるのは2帯=**浅GD(-0.5~-2%・9:00-03早寄り) +0.66%/勝73%/t2.2**
と**深GD(-5~-10%・9:06-14遅寄り)寄り+10分 +0.64~0.72%/勝85-91%/t2.2**。
**激深(≤-10%)は寄りが9:21+に超遅延し戻らず落ち続ける=-3.9%/勝0/t-6で災害(除外)**。
中GD(-2~-5%)は弱・フラット(勝33-40%)/GUはノイズ。≥5000億が芯で≥1兆は弱・小型はジリ下げ。
n小(各帯5-17)・分足2年・超スキャル執行依存ゆえ🟡裁量/候補。

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
HOLDS = [5, 10, 15]   # 寄り+N分(実行faithful)
MIN_DATE = "2024-05-21"
# gap深さ帯 (lo<=gap<hi)
GAP_BANDS = [("フラ -0.5~0.5", -0.5, 0.5), ("浅GD -2~-0.5", -2, -0.5),
             ("中GD -5~-2", -5, -2), ("深GD -10~-5", -10, -5), ("激深 <=-10", -1e9, -10)]


def _c5(c: str) -> str:
    return c + "0" if len(c) == 4 else c


def _tmin(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _px_at(bars: list, target_min: int) -> float | None:
    """target_min(分)以前で最も新しい Close。"""
    cands = [c for tt, oo, c in bars if tt and _tmin(tt) <= target_min and c]
    return cands[-1] if cands else None


def _rows(min_mc: float) -> list[dict]:
    po = json.loads(PO.read_text())["records"]
    mst = {m["Code"]: m.get("scale_band") for m in json.loads(MASTER.read_text())["records"]}
    cal = sorted(r["Date"] for r in json.loads(TOPIX.read_text())["records"] if r.get("O"))
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    def nxt(d: str) -> str | None:
        i = bisect.bisect_right(cal, d)
        return cal[i] if i < len(cal) else None

    out = []
    for r in po:
        if r.get("stage") != "announce" or r.get("po_type") != "普通":
            continue
        mc = r.get("market_cap")
        if not mc or mc < min_mc:
            continue
        nd = nxt(r.get("event_date", ""))
        if not nd or nd < MIN_DATE:
            continue
        code = _c5(r.get("code", ""))
        bars = cache.get(f"{code}|{nd}")
        if not bars or not bars[0][1]:
            continue
        gap = (r.get("attrs") or {}).get("gap_pct")
        if gap is None:
            continue
        out.append({"gap": gap, "date": nd, "o": bars[0][1], "size": mst.get(code),
                    "otm": _tmin(bars[0][0]), "opent": bars[0][0], "bars": bars})
    return out


def _cell(rows: list[dict], hold: int) -> str:
    v = []
    for r in rows:
        p = _px_at(r["bars"], r["otm"] + hold)
        if p:
            v.append(((p / r["o"] - 1) * 100 - COST, r["date"]))
    if not v:
        return "—"
    nets = [a for a, _ in v]
    return (f"{st.fmean(nets):+.2f}%/勝{sum(1 for a in nets if a > 0) / len(nets) * 100:.0f}/"
            f"t{clustered_t(nets, [d for _, d in v]):+.1f}/n{len(v)}")


def build_report() -> str:
    """①A朝場ロングをgap深さ×寄り+N分(実行faithful)で切り分けた md を返す。"""
    L = ["# PO発表翌日「朝場ロング」(①A) 分足検証 — 実行faithful版", "",
         f"announce普通・分足期({MIN_DATE}+)・long往復{COST}% net。"
         "**出口は寄り+N分(実際の寄りからの相対・固定時計でない)**。", ""]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if not cache:
        return "\n".join(L + ["_(cache/po_announce_minute.json 未生成。fetch要)_"])
    for mc, lab in [(5000, "≥5000億"), (1500, "≥1500億"), (1000, "≥1000億")]:
        rows = _rows(mc)
        L.append(f"## {lab}（n={len(rows)}）")
        L.append("")
        L.append("| gap帯 | n | " + " | ".join(f"寄り+{h}分" for h in HOLDS) + " |")
        L.append("|---|--:|" + "---|" * len(HOLDS))
        for nm, lo, hi in GAP_BANDS:
            sub = [r for r in rows if lo <= r["gap"] < hi]
            cells = " | ".join(_cell(sub, h) for h in HOLDS)
            L.append(f"| {nm} | {len(sub)} | {cells} |")
        L.append("")
    L += ["## 結論（実行faithful）", "",
          "- **出口は寄り+5〜10分**（固定9:15は早寄りに長い窓を与える幻＝2026-06訂正）。",
          "- **取れる2帯**: 浅GD(-0.5~-2%・9:00-03早寄り)+0.66%/勝73%/t2.2 ＋ 深GD(-5~-10%・遅寄り)寄り+10分+0.6~0.7%/勝85-91%/t2.2。",
          "- **激深(≤-10%)は災害**: 寄りが9:21+に超遅延し戻らず落ち続ける(-3.9%/勝0/t-6)＝S安と同じ『深すぎ=本物の異常』。ハード除外。",
          "- 中GD(-2~-5%)は弱・フラット(勝33-40%)/GUはノイズ。≥5000億が芯・≥1兆弱・小型はジリ下げ。",
          "- 執行トリガー=gap数値より『特別売り気配で寄りが遅れたか＋深さ』。n小(5-17)・分足2年で🟡裁量/候補。"]
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

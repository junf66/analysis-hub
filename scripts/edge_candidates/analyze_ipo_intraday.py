"""IPO 初値買い × 出口時刻(分足) × GU程度 × 評価 の検証 (候補発見)。

ユーザー仮説「上場日 初値買い→寄り直後/+N分で利確」を分足で検証。出口は**初値が付いた時刻基準**
(9:00固定でない。IPOは買い気配で寄りが09:30-10:15に遅延)。GU(初値騰落率)程度で層別。

データ: data/edge_candidates/ipo_96ut_ratings.json (評価/初値/GU 手動転記) + 日足(上場日特定) +
分足 /equities/bars/minute (2024-05-21以降のみ)。価格cache: cache/ipo_bars_raw.json, cache/ipo_minute.json。

主要所見(2024-05-21以降 n121): 初値買いは保有するほど負け(初値天井)。**唯一の有意ロング=
GU20-50%×寄り直後(初値→最初の1分足C): 全評価+0.96%/勝72%/t3.9・B評価+1.03%/勝77%/t3.6**。
GU≤10は弱・GU>50(過熱)は即フェード負。エッジは寄り後1-2分に凝縮(+10分で勝率52%に低下)＝超スキャル・
執行(初値約定→1分内売り)と滑りが急所。確定でなく監視候補。出力: reports/ipo_intraday.md
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
DAILY = REPO / "cache" / "ipo_bars_raw.json"
MIN = REPO / "cache" / "ipo_minute.json"
REPORT = REPO / "reports" / "ipo_intraday.md"
COST = 0.2
GU_BANDS = [("GD≤0", lambda g: g <= 0), ("0-5", lambda g: 0 < g <= 5), ("5-10", lambda g: 5 < g <= 10),
            ("10-20", lambda g: 10 < g <= 20), ("20-50", lambda g: 20 < g <= 50), (">50", lambda g: g > 50)]


def _tomin(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def fetch(daily: dict, recs: list[dict]) -> dict[str, list]:
    """各IPOの上場日(初値一致バー)を特定し分足を取得 (2024-05-21以降・cache resume)。"""
    from scripts import _jquants
    mc = json.loads(MIN.read_text()) if MIN.exists() else {}
    jobs = []
    for r in recs:
        code, h = r["code"], r["hatsune"]
        d = next((d for d, o, c in daily.get(code, []) if o and abs(o - h) / h < 0.015 and d >= "2024-05-21"), None)
        if d:
            jobs.append((code, d))
    for i, (code, d) in enumerate([(c, d) for c, d in jobs if f"{c}|{d}" not in mc], 1):
        cc = code + "0" if len(code) == 4 else code
        try:
            b = _jquants.get_list("/equities/bars/minute", code=cc, date=d)
            mc[f"{code}|{d}"] = [[x["Time"], x.get("O"), x.get("C")] for x in b if x.get("O")]
        except _jquants.JQuantsError:
            mc[f"{code}|{d}"] = []
        if i % 40 == 0:
            atomic_write_json(MIN, mc)
    atomic_write_json(MIN, mc)
    return mc


def build(recs: list[dict], daily: dict, mc: dict) -> str:
    """初値買い→各出口を rank×GU で集計した md。"""
    data = []
    for r in recs:
        code, rank, h, gu = r["code"], r["rank"], r["hatsune"], r["gu_pct"]
        ld = next(((d, c) for d, o, c in daily.get(code, []) if o and abs(o - h) / h < 0.015), None)
        if not ld:
            continue
        d, close = ld
        bars = mc.get(f"{code}|{d}", [])
        if not bars or not bars[0][1]:
            continue
        t0, entry = _tomin(bars[0][0]), bars[0][1]
        row = {"rank": rank, "gu": gu, "寄後": (bars[0][2] / entry - 1) * 100, "引": (close / entry - 1) * 100}
        for n in (10, 30, 60):
            px = next((c for t, o, c in bars if _tomin(t) >= t0 + n and c), None)
            row[n] = (px / entry - 1) * 100 if px else None
        data.append(row)

    def st(key, filt) -> str:
        v = [r[key] for r in data if filt(r) and r.get(key) is not None]
        if len(v) < 3:
            return f"n{len(v)}"
        net = [x - COST for x in v]
        win = sum(1 for x in net if x > 0) / len(net) * 100
        se = statistics.pstdev(net) / math.sqrt(len(net))
        t = statistics.fmean(net) / se if se else 0
        return f"{statistics.fmean(net):+.2f}%/勝{win:.0f}/t{t:+.1f}/n{len(net)}"

    cols = ["寄後", 10, 30, 60, "引"]
    L = ["# IPO 初値買い × 出口(分足・寄り起点) × GU × 評価", "",
         f"出口は**初値が付いた時刻基準**(9:00固定でない)。cost{COST}%。分足は2024-05-21以降 n{len(data)}。", ""]
    for title, rf in [("全評価", lambda r: True), ("B評価", lambda r: r["rank"] == "B"),
                      ("A/B評価", lambda r: r["rank"] in ("A", "B")), ("C評価", lambda r: r["rank"] == "C")]:
        L += [f"## {title}: GU×出口 (net/勝率/t/n)", "",
              "| GU帯 | 寄後(~1分) | +10分 | +30分 | +60分 | 引け |", "|---|---|---|---|---|---|"]
        for lab, f in GU_BANDS:
            ff = (lambda r, f=f: rf(r) and f(r["gu"]))
            L.append(f"| {lab} | " + " | ".join(st(c, ff) for c in cols) + " |")
        L.append("")
    L += ["## 所見", "- 初値買いは保有するほど負け(初値天井)。**唯一の有意ロング=GU20-50%×寄り直後**: "
          "全評価+0.96%/勝72%/t3.9・B評価+1.03%/勝77%/t3.6。GU≤10弱・GU>50過熱は即フェード負。",
          "- エッジは寄り後1-2分に凝縮(+10分で勝率52%に低下)＝超スキャル。執行(初値約定→1分内売り)・滑りが急所＝監視候補。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true", help="キャッシュのみで集計")
    ap.add_argument("--out", type=Path, default=REPORT, help="出力 md (既定 reports/ipo_intraday.md)")
    args = ap.parse_args()
    recs = json.loads(DATA.read_text())["records"]
    daily = json.loads(DAILY.read_text())
    mc = json.loads(MIN.read_text()) if (args.no_fetch and MIN.exists()) else fetch(daily, recs)
    report = build(recs, daily, mc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, report)
    print(report)
    print(f"[ipo_intraday] → {args.out}")


if __name__ == "__main__":
    main()

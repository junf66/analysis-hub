"""S安リバウンド候補のトレード台帳を出力（Codex独立監査用・巨大cache不要）。

⑩Rの鏡像候補。S安引け→翌日 寄り long→朝(10:00-10:30)手仕舞い。引けは死・朝は候補。
本スクリプトは gap≤-8% の各イベントを1行=1件のCSVに落とし、入口(初値時刻/初値)・出口(10:00/10:30/引け)・
属性(市場/規模/信用区分)・net を全て載せる。監査側はこのCSVだけで GD帯×出口×属性 を独立再集計できる。

入力(gitignore cache): cache/limit_dl_events.json, cache/sdown_minute.json + data の topix/master。
出力(committed): data/edge_candidates/sdown_trades.csv
net_* = (price_t/初値-1)*100 - 0.20(long往復)。raw(intraday対TOPIXは/indices/bars/minute 403で不能)。
"""
from __future__ import annotations

import bisect
import csv
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DL = REPO / "cache" / "limit_dl_events.json"
MCACHE = REPO / "cache" / "sdown_minute.json"
TOPIX = REPO / "data" / "edge_candidates" / "topix_daily.json"
MASTER = REPO / "data" / "edge_candidates" / "equities_master.json"
OUT = REPO / "data" / "edge_candidates" / "sdown_trades.csv"
LONG_COST = 0.20


def _c5(code: str) -> str:
    code = str(code)
    return code if len(code) == 5 else code + "0"


def main() -> None:
    ev = json.loads(DL.read_text())
    tpx = {r["Date"]: r for r in json.loads(TOPIX.read_text())["records"]}
    cal = sorted(tpx)
    mc = json.loads(MCACHE.read_text())
    m = {str(r["Code"]): r for r in json.loads(MASTER.read_text())["records"]}

    def nextday(d):
        i = bisect.bisect_right(cal, d)
        return cal[i] if i < len(cal) else None

    rows = []
    for e in ev:
        if e.get("gap") is None or e["gap"] > -8:
            continue
        ed = nextday(e["date"])
        if not ed or ed < "2024-05-21":
            continue
        b = mc.get(f"{e['code']}|{ed}")
        if not b:
            continue
        o = b[0][1]; t0 = b[0][0]
        if not o:
            continue
        tmap = {t: c for t, _, c in b}

        def at(t):
            if t in tmap:
                return tmap[t]
            past = [c for tt, c in tmap.items() if tt <= t]
            return past[-1] if past else None
        mm = m.get(_c5(e["code"]), {})
        rec = {"code": e["code"], "sdown_date": e["date"], "entry_date": ed,
               "gap_pct": round(e["gap"], 2), "open_time": t0, "open": o,
               "mkt": mm.get("MktNm"), "scale_band": mm.get("scale_band"), "mrgn": mm.get("MrgnNm"),
               "openable_by_10": int(t0 <= "10:00")}
        for t, lab in [("09:30", "0930"), ("10:00", "1000"), ("10:30", "1030")]:
            p = at(t)
            rec[f"net_{lab}"] = round((p / o - 1) * 100 - LONG_COST, 3) if p else ""
        rec["net_close"] = round(e["io"] - LONG_COST, 3) if e.get("io") is not None else ""
        rows.append(rec)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"[dump_sdown_trades] {len(rows)} trades → {OUT}")
    # 検算: GD≤-12% × 午前寄り → net_1030
    import statistics
    sub = [r["net_1030"] for r in rows if r["gap_pct"] <= -12 and r["openable_by_10"] and r["net_1030"] != ""]
    if sub:
        print(f"  検算 GD≤-12%×午前寄り→10:30: EV{statistics.fmean(sub):+.2f}% "
              f"勝{sum(1 for x in sub if x>0)/len(sub)*100:.0f}% n{len(sub)}")


if __name__ == "__main__":
    main()

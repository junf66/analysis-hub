"""S安リバウンド(大GD)の分足深堀: GD程度 × 朝の出口 を細かく層別（候補🟡の精緻化）。

⑩Rの鏡像。S安引け→翌日寄り long→朝に手仕舞い。引けは死だが朝(10:00)は候補(別途確認済)。
本スクリプトは GD(翌朝gap)を細かく刻み×出口{9:30,10:00,10:30,引け}で EV/勝率/t/n と
『約定可能率(寄りが早く付いたか=10:00までに価格があるか)』を出す。分足期2024-05〜のみ。

価格cache: cache/sdown_minute.json (code|date -> [[Time,O,C]])。出力: 標準出力。
"""
from __future__ import annotations

import bisect
import json
import statistics
from pathlib import Path

from scripts import _jquants
from scripts._atomic import atomic_write_json
from scripts.edge_candidates.analyze_archive_regime import clustered_t

REPO = Path(__file__).resolve().parent.parent.parent
DL = REPO / "cache" / "limit_dl_events.json"
TOPIX = REPO / "data" / "edge_candidates" / "topix_daily.json"
MCACHE = REPO / "cache" / "sdown_minute.json"
EXITS = ["09:30", "10:00", "10:30"]
LONG_COST = 0.20
GAP_MAX = -8.0          # gap≤-8% の S安投げ母体
MIN_N = 12


def _minute(code: str, date: str, cache: dict) -> list:
    k = f"{code}|{date}"
    if k in cache:
        return cache[k]
    try:
        b = _jquants.get_list("/equities/bars/minute", code=code, date=date)
        rows = [[x["Time"], x.get("O"), x.get("C")] for x in b if x.get("O") and x.get("C")]
    except Exception:  # noqa: BLE001
        rows = []
    cache[k] = rows
    return rows


def main() -> None:
    ev = json.loads(DL.read_text())
    tpx = {r["Date"]: r for r in json.loads(TOPIX.read_text())["records"]}
    cal = sorted(tpx)
    cache = json.loads(MCACHE.read_text()) if MCACHE.exists() else {}

    def nextday(d):
        i = bisect.bisect_right(cal, d)
        return cal[i] if i < len(cal) else None

    rows = []   # {gap, nets:{exit:net}, io_net, opened_by_10}
    todo = [(e, nextday(e["date"])) for e in ev
            if e.get("gap") is not None and e["gap"] <= GAP_MAX]
    todo = [(e, ed) for e, ed in todo if ed and ed >= "2024-05-21"]
    for i, (e, ed) in enumerate(todo, 1):
        b = _minute(e["code"], ed, cache)
        if i % 60 == 0:
            atomic_write_json(MCACHE, cache)
        if not b:
            continue
        o = b[0][1]
        tmap = {t: c for t, c, *_ in [(r[0], r[2]) for r in b]}
        # price at exit (その時刻のC、無ければ直近過去)
        def price_at(t):
            if t in tmap:
                return tmap[t]
            past = [c for tt, c in tmap.items() if tt <= t]
            return past[-1] if past else None
        rec = {"gap": e["gap"], "month": ed[:7], "io_net": e["io"] - LONG_COST,
               "opened_by_10": "10:00" in tmap or any(tt <= "10:00" for tt in tmap)}
        for t in EXITS:
            p = price_at(t)
            rec[t] = ((p / o - 1) * 100 - LONG_COST) if (p and o) else None
        rows.append(rec)
    atomic_write_json(MCACHE, cache)

    bands = [(-10, -8, "-8〜-10%"), (-12, -10, "-10〜-12%"), (-15, -12, "-12〜-15%"),
             (-18, -15, "-15〜-18%"), (-99, -18, "≤-18%")]
    print(f"母体(gap≤{GAP_MAX}%・分足期) {len(rows)}件\n")
    print(f"{'GD帯':<12}{'n':>4} {'寄率%':>6} | " + " | ".join(f"{t}出口(EV/勝/t)" for t in EXITS) + " | 引け")
    for lo, hi, lab in bands:
        sub = [r for r in rows if lo <= r["gap"] < hi]
        if len(sub) < MIN_N:
            print(f"{lab:<12}{len(sub):>4} (n<{MIN_N})")
            continue
        openrate = sum(1 for r in sub if r["opened_by_10"]) / len(sub) * 100
        cells = []
        for t in EXITS:
            v = [(r[t], r["month"]) for r in sub if r[t] is not None]
            if len(v) >= MIN_N:
                x = [a for a, _ in v]
                cells.append(f"{statistics.fmean(x):+.2f}/{sum(1 for a in x if a>0)/len(x)*100:.0f}/{clustered_t(x,[m for _,m in v]):+.1f}(n{len(x)})")
            else:
                cells.append(f"n{len(v)}")
        io = [(r["io_net"], r["month"]) for r in sub]
        iox = [a for a, _ in io]
        iostr = f"{statistics.fmean(iox):+.2f}/{sum(1 for a in iox if a>0)/len(iox)*100:.0f}"
        print(f"{lab:<12}{len(sub):>4} {openrate:>6.0f} | " + " | ".join(cells) + f" | {iostr}")
    print("\n注: EV/勝率/t は long net0.20 raw(対TOPIX未)。寄率=10:00までに寄った%(執行可能性の目安)。")


if __name__ == "__main__":
    main()

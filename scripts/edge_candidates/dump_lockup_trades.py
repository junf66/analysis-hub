"""ロックアップ解除ショートの『トレード台帳』を出力（Codex独立監査用・巨大bars不要）。

ipo_bars_raw.json は 2.9MB 1行JSON で第三者が完全取得しづらい。本スクリプトは 90日マークの
各トレードを1行=1IPOの CSV に落とし、入力(エントリ/出口の日付・寄り・引け・TOPIX)と net を全て載せる。
監査側はこの小さなCSVだけで n/EV/勝率/t を独立再集計でき、数行を raw bars と突合すれば足りる。

出力: data/edge_candidates/lockup_trades.csv
列: code,lockup_days,listing,entry_date,entry_open,exit7_date,exit7_close,
    tpx_entry_open,tpx_exit7_close,net_day,net_3,net_5,net_7
net_* = −((出口引け/エントリ寄り−1) − (TOPIX同区間)) ×100 − 0.15(short cost)
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.edge_candidates.analyze_lockup_short import (
    RATINGS, BARS, TERMS, _addcal, _load_cal, _nth, _onafter, build_listing)

REPO = Path(__file__).resolve().parent.parent.parent
OUT = REPO / "data" / "edge_candidates" / "lockup_trades.csv"
SHORT_COST = 0.15


def _net(bk: dict, tpx: dict, E: str, X: str) -> float | None:
    if E not in bk or X not in bk or E not in tpx or X not in tpx:
        return None
    eo = bk[E][0]; to = tpx[E][0]; ec = bk[X][1]; tc = tpx[X][1]
    if not eo or not to:
        return None
    return -((ec / eo - 1) * 100 - (tc / to - 1) * 100) - SHORT_COST


def main() -> None:
    terms = json.loads(TERMS.read_text())
    ratings = json.loads(RATINGS.read_text())["records"]
    bars = json.loads(BARS.read_text())
    tpx, cal = _load_cal()
    listing = build_listing(ratings, bars)
    rows = []
    for code, (ld, bk) in listing.items():
        t = terms.get(code)
        if not t or t.get("status") != "ok":
            continue
        E = _onafter(cal, _addcal(ld, 90))   # 90日解除後の最初の取引可能日(寄り)
        if not E or E not in bk:
            continue
        X = {n: _nth(cal, E, n - 1) for n in (1, 3, 5, 7)}
        rec = {
            "code": code, "lockup_days": "|".join(map(str, t["lockup_days"])),
            "listing": ld, "entry_date": E, "entry_open": bk[E][0],
            "exit7_date": X[7], "exit7_close": bk.get(X[7], [None, None])[1] if X[7] else None,
            "tpx_entry_open": tpx.get(E, [None])[0],
            "tpx_exit7_close": tpx.get(X[7], [None, None])[1] if X[7] else None,
        }
        for n in (1, 3, 5, 7):
            rec[f"net_{n}"] = round(_net(bk, tpx, E, X[n]), 4) if X[n] and _net(bk, tpx, E, X[n]) is not None else ""
        rows.append(rec)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"[dump_lockup_trades] {len(rows)} trades → {OUT}")
    # 検算: 90日ロック保有 +7日の集計をその場で表示(台帳と一致確認用)
    import statistics
    g90 = [r["net_7"] for r in rows if "90" in r["lockup_days"].split("|") and r["net_7"] != ""]
    if g90:
        print(f"  検算 90日ロック保有 +7日: EV{statistics.fmean(g90):+.2f}% "
              f"勝{sum(1 for x in g90 if x>0)/len(g90)*100:.0f}% n{len(g90)}")


if __name__ == "__main__":
    main()

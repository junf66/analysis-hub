"""ストップ安(LL)銘柄の翌営業日リターンを取得 (uoa「ストップ安買い」検証用)。

`analyze_limit_events.py` の UL(S高)取得と**対称**な LL(S安)版。LL で引けた銘柄の
翌営業日 寄→引(io) と overnight gap を全市場ストリーミングで集計する。⑩R(小型S高翌朝
ショート)の鏡像＝小型S安翌日のリバウンドロングを検証する母体になる。

データ: /equities/bars/daily を date 指定で全市場取得。価格は AdjO/AdjC 統一。
出力(中間/gitignore): cache/limit_dl_events.json = [{date(=S安引け日), code, io, gap}]
resume 可 (チェックポイント: 処理済み末日と前日LL状態を保持)。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
DL_PATH = REPO_ROOT / "cache" / "limit_dl_events.json"        # [{date, code, io, gap}]
STATE_PATH = REPO_ROOT / "cache" / "limit_dl_state.json"      # {last_date, prev_ll:{code:close}}
FRM = "2016-06-13"


def _c4(code: str) -> str:
    return str(code)[:4]


def fetch_stream() -> list[dict[str, Any]]:
    """全営業日をストリーミングし、S安銘柄の翌日 io/gap を集計 (resume 可)。"""
    from scripts import _jquants
    topix = {r["Date"]: r["C"] for r in json.loads(TOPIX_PATH.read_text())["records"] if r.get("C")}
    cal = [d for d in sorted(topix) if d >= FRM]
    events: list[dict[str, Any]] = json.loads(DL_PATH.read_text()) if DL_PATH.exists() else []
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    last_date: str = state.get("last_date", "")
    prev_ll: dict[str, float] = state.get("prev_ll", {})   # 前営業日に LL で引けた code → AdjC
    prev_date: str = last_date
    for i, d in enumerate(cal):
        if last_date and d <= last_date:
            continue
        try:
            bars = _jquants.get_list("/equities/bars/daily", date=d)
        except _jquants.JQuantsError:
            prev_ll = {}
            prev_date = d
            continue
        cur: dict[str, tuple] = {}
        for b in bars:
            ll = b.get("LL") == "1"
            o, c = b.get("AdjO") or b.get("O"), b.get("AdjC") or b.get("C")
            if o and c:
                cur[_c4(b["Code"])] = (o, c, ll)
        # 前営業日 LL 銘柄の本日リターン (寄→引 io / overnight gap)
        for code, ll_close in prev_ll.items():
            if code in cur:
                o, c, _ = cur[code]
                events.append({"date": prev_date, "code": code,
                               "io": (c / o - 1.0) * 100.0, "gap": (o / ll_close - 1.0) * 100.0})
        prev_ll = {code: v[1] for code, v in cur.items() if v[2]}
        prev_date = d
        if i % 50 == 0:
            atomic_write_json(DL_PATH, events)
            atomic_write_json(STATE_PATH, {"last_date": d, "prev_ll": prev_ll})
            print(f"  {d} ({i}/{len(cal)}) LL_events={len(events)}")
    atomic_write_json(DL_PATH, events)
    atomic_write_json(STATE_PATH, {"last_date": cal[-1] if cal else "", "prev_ll": prev_ll})
    return events


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args()
    events = fetch_stream()
    print(f"[limit_dl] S安イベント {len(events)} → {DL_PATH}")


if __name__ == "__main__":
    main()

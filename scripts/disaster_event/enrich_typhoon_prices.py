"""台風イベント周辺の対象16銘柄リターンを J-Quants 日足から付与する。

対象16銘柄 (建材・電気工事・防災テーマ):
  5929 三和HD / 5930 文化シヤッター / 5938 LIXIL / 5947 リンナイ /
  1972 三晃金属 / 4612 日本ペHD / 1942 関電工 / 1959 九電工 /
  1949 住友電設 / 1968 太平電業 / 1961 三機工業 / 1979 大気社 /
  1911 住友林業 / 1928 積水ハウス / 1928? / 3050 DCMHD / 7516 コーナン商事

各イベント (event_date=最接近日) ごとに、各銘柄について最初の取引日
t0 (event_date 以降の最初の営業日) を起点に、以下のウィンドウの
始値→終値ロングリターン% を計算する (AdjO/AdjC = 分割調整後):
  pre_long : open[t0-3] → close[t0-1]   (接近前の仕込み)
  hit      : open[t0]   → close[t0]     (直撃日 当日)
  post1    : open[t0+1] → close[t0+1]   (通過翌日)
  post3    : open[t0+1] → close[t0+3]   (通過後 数日)
  post5    : open[t0+1] → close[t0+5]
方向(ロング/ショート)とコストは analyze 側で付与する。

出力: data/disaster_event/typhoon_price_data.json
"""
from __future__ import annotations

import argparse
import bisect
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EVENTS_PATH = REPO_ROOT / "data" / "disaster_event" / "typhoon_records.json"
OUT_PATH = REPO_ROOT / "data" / "disaster_event" / "typhoon_price_data.json"

STOCKS: dict[str, str] = {
    "5929": "三和HD", "5930": "文化シヤッター", "5938": "LIXIL", "5947": "リンナイ",
    "1972": "三晃金属", "4612": "日本ペHD", "1942": "関電工", "1959": "九電工",
    "1949": "住友電設", "1968": "太平電業", "1961": "三機工業", "1979": "大気社",
    "1911": "住友林業", "1928": "積水ハウス", "3050": "DCMHD", "7516": "コーナン商事",
}

# 各ウィンドウ: (entry_offset, exit_offset)  営業日オフセット (t0 基準)
WINDOWS: dict[str, tuple[int, int]] = {
    "pre_long": (-3, -1),
    "hit": (0, 0),
    "post1": (1, 1),
    "post3": (1, 3),
    "post5": (1, 5),
}


def fetch_bars(code: str, start: str, end: str) -> list[dict[str, Any]]:
    """4桁コードの日足を [start, end] で取得し Date 昇順で返す (分割調整値含む)。"""
    code5 = code + "0" if len(code) == 4 else code
    bars = _jquants.get_list("/equities/bars/daily", code=code5,
                             **{"from": start, "to": end})
    bars = [b for b in bars if b.get("Date") and b.get("AdjO") and b.get("AdjC")]
    bars.sort(key=lambda b: b["Date"])
    return bars


def _window_ret(bars: list[dict[str, Any]], dates: list[str], event_date: str,
                entry_off: int, exit_off: int) -> float | None:
    """event_date 以降の最初の営業日を t0 とし、[t0+entry, t0+exit] の始→終リターン%。"""
    t0 = bisect.bisect_left(dates, event_date)
    if t0 >= len(dates):
        return None
    ei = t0 + entry_off
    xi = t0 + exit_off
    if ei < 0 or xi >= len(dates) or ei > xi:
        return None
    o = bars[ei].get("AdjO")
    c = bars[xi].get("AdjC")
    if not o or not c:
        return None
    return (c / o - 1.0) * 100.0


def enrich(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """各イベント×銘柄のウィンドウリターンを計算した観測リストを返す。"""
    if not events:
        return []
    lo = min(e["event_date"] for e in events)
    hi = max(e["event_date"] for e in events)
    start = (date.fromisoformat(lo) - timedelta(days=20)).isoformat()
    end = (date.fromisoformat(hi) + timedelta(days=20)).isoformat()

    obs: list[dict[str, Any]] = []
    for code, name in STOCKS.items():
        bars = fetch_bars(code, start, end)
        dates = [b["Date"] for b in bars]
        print(f"  [{code} {name}] bars={len(bars)} {dates[0] if dates else '-'}..{dates[-1] if dates else '-'}")
        for e in events:
            rets = {w: _window_ret(bars, dates, e["event_date"], a, b)
                    for w, (a, b) in WINDOWS.items()}
            obs.append({
                "intl": e["intl"], "name_typhoon": e["name"],
                "event_date": e["event_date"], "code": code, "name_stock": name,
                "case_study": e.get("case_study"),
                "rets": rets,
            })
    return obs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", type=Path, default=EVENTS_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    events = json.loads(args.events.read_text())["records"]
    print(f"[enrich] events={len(events)} stocks={len(STOCKS)}")
    obs = enrich(events)
    n_cov = sum(1 for o in obs if o["rets"].get("hit") is not None)
    print(f"[enrich] observations={len(obs)} (hit-day coverage={n_cov})")
    atomic_write_json(args.out, {
        "records": obs, "count": len(obs),
        "stocks": STOCKS, "windows": {k: list(v) for k, v in WINDOWS.items()},
    }, indent=1)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

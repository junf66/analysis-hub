"""日本に接近・上陸した「大型」台風を抽出しイベント化する。

best_track.json (全経路) から、日本近傍ボックス内で TS 以上だった台風を
「日本接近」とみなし、さらに大型基準でフィルタしてトレード・イベントに変換する。

大型基準 (引き継ぎ仕様: 上陸/接近 かつ ≤960hPa または ≥35m/s):
  近傍最低気圧 <= 960 hPa  OR  近傍最大風速 >= 68 knot (= 35 m/s)
  → 2016-2025 で約 49 件 (年3〜8件) に収束。

イベント日 (event_date):
  近傍区間で中心気圧が最も低い観測点の JST 日付 = 「直撃/最接近日」。
  この日を t0 起点としてトレード戦略を組む。

出力: data/disaster_event/typhoon_records.json
  各 record = {intl, name, year, event_date, near_min_pressure, near_max_wind,
              overall_min_pressure, peak_grade, landfall_like, case_study, attrs}
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IN_PATH = REPO_ROOT / "data" / "disaster_event" / "best_track.json"
OUT_PATH = REPO_ROOT / "data" / "disaster_event" / "typhoon_records.json"

JST = timezone(timedelta(hours=9))
TS_PLUS_GRADES = {"3", "4", "5", "9"}

# 日本近傍ボックス (本州・四国・九州・南西諸島・接近域を広めに含む)
LAT_RANGE = (24.0, 46.0)
LON_RANGE = (123.0, 147.0)

# 大型基準
MAX_NEAR_PRESSURE = 960   # hPa 以下
MIN_NEAR_WIND_KT = 68     # knot 以上 (35 m/s ≒ 68 kt)

# 上陸近似ボックス (日本列島本土をざっくり包含)
LANDFALL_LAT = (30.0, 46.0)
LANDFALL_LON = (129.0, 146.0)

# 個別事例分析の対象 (引き継ぎ指定): 国際番号 -> ラベル
CASE_STUDIES = {
    "1915": "2019年15号 ファクサイ(千葉停電)",
    "1919": "2019年19号 ハギビス(東日本豪雨)",
    "2410": "2024年10号 サンサン",
    "2418": "2024年18号 チャーミー",
}


def _in_box(p: dict[str, Any], lat_range, lon_range) -> bool:
    return (lat_range[0] <= p["lat"] <= lat_range[1]
            and lon_range[0] <= p["lon"] <= lon_range[1])


def _jst_date(dt_utc_iso: str) -> str:
    dt = datetime.fromisoformat(dt_utc_iso).astimezone(JST)
    return dt.date().isoformat()


def identify(storms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """全ストームから日本接近・大型台風イベントを抽出する。"""
    events: list[dict[str, Any]] = []
    for s in storms:
        near = [p for p in s["points"]
                if p["grade"] in TS_PLUS_GRADES and _in_box(p, LAT_RANGE, LON_RANGE)]
        if not near:
            continue
        near_pres = [p["pressure"] for p in near if p["pressure"] is not None]
        near_wind = [p["wind"] for p in near if p["wind"] is not None]
        near_min_p = min(near_pres) if near_pres else None
        near_max_w = max(near_wind) if near_wind else None

        big = (near_min_p is not None and near_min_p <= MAX_NEAR_PRESSURE) or \
              (near_max_w is not None and near_max_w >= MIN_NEAR_WIND_KT)
        if not big:
            continue

        # 最接近(=近傍最低気圧)点。気圧欠損なら最も内陸寄り/最大風速点で代替
        if near_pres:
            peak = min((p for p in near if p["pressure"] is not None),
                       key=lambda p: p["pressure"])
        else:
            peak = max(near, key=lambda p: (p["wind"] or 0))
        event_date = _jst_date(peak["dt_utc"])

        overall_pres = [p["pressure"] for p in s["points"] if p["pressure"] is not None]
        peak_grade = max((p["grade"] for p in near), default="")
        landfall_like = any(_in_box(p, LANDFALL_LAT, LANDFALL_LON) for p in near)

        events.append({
            "intl": s["intl"],
            "name": s["name"],
            "year": s["year"],
            "event_date": event_date,
            "near_min_pressure": near_min_p,
            "near_max_wind_kt": near_max_w,
            "near_max_wind_ms": round(near_max_w * 0.514, 1) if near_max_w else None,
            "overall_min_pressure": min(overall_pres) if overall_pres else None,
            "peak_grade": peak_grade,
            "landfall_like": landfall_like,
            "n_near_points": len(near),
            "case_study": CASE_STUDIES.get(s["intl"]),
            "attrs": {},
        })
    events.sort(key=lambda e: e["event_date"])
    return events


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-path", type=Path, default=IN_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    storms = json.loads(args.in_path.read_text())["records"]
    events = identify(storms)
    by_year: dict[str, int] = {}
    for e in events:
        by_year[e["year"]] = by_year.get(e["year"], 0) + 1
    print(f"[identify] 日本接近・大型台風: {len(events)} 件")
    print(f"[identify] 年別: {dict(sorted(by_year.items()))}")
    found = [e["case_study"] for e in events if e["case_study"]]
    print(f"[identify] 個別事例ヒット: {found}")
    atomic_write_json(args.out, {"records": events, "count": len(events)}, indent=1)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

"""気象庁 RSMC東京ベストトラック (bst_all.zip) を取得しパースする。

JMA が公開する 1951年以降の全台風経路 (6時間毎) を取得し、対象期間
(既定 2016-2025) の台風だけを共通 dict に展開して保存する。

ベストトラック書式 (固定長、UTC):
  ヘッダ行   : `66666` で始まる。 [6:10]=国際番号(YYnn), [30:50]=英名
  データ行   : [9:12]=="002"。 [0:8]=YYMMDDHH, [13]=階級,
               [15:18]=緯度*0.1, [19:23]=経度*0.1, [24:28]=中心気圧hPa,
               [33:36]=最大風速(knot, 1977年以降)
  階級 (grade): 2=TD, 3=TS, 4=STS, 5=台風, 6=温帯低気圧, 7=域外, 9=TS以上(旧)

出力: data/disaster_event/best_track.json
  {"records": [{intl, name, year, points:[{dt_utc, grade, lat, lon, pressure, wind}]}]}

network policy で `www.jma.go.jp` の allowlist が必要。
"""
from __future__ import annotations

import argparse
import io
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = REPO_ROOT / "data" / "disaster_event" / "best_track.json"
BST_URL = ("https://www.jma.go.jp/jma/jma-eng/jma-center/"
           "rsmc-hp-pub-eg/Besttracks/bst_all.zip")

JST = timezone(timedelta(hours=9))
TS_PLUS_GRADES = {"3", "4", "5", "9"}  # TS 以上 (熱帯低気圧として有意)


def _yy_to_year(yy: int) -> int:
    """ベストトラックの 2 桁年 → 西暦 (51-99=1900年代, 00-50=2000年代)。"""
    return 1900 + yy if yy >= 51 else 2000 + yy


def download_best_track(url: str = BST_URL) -> str:
    """bst_all.zip を取得し中の bst_all.txt をデコードして返す。"""
    req = urllib.request.Request(url, headers={"User-Agent": "analysis-hub/disaster_event"})
    with urllib.request.urlopen(req, timeout=120) as r:
        blob = r.read()
    zf = zipfile.ZipFile(io.BytesIO(blob))
    name = zf.namelist()[0]
    return zf.read(name).decode("utf-8", errors="replace")


def parse_best_track(text: str) -> list[dict[str, Any]]:
    """ベストトラック全文 → ストーム dict のリスト (全期間)。"""
    storms: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in text.splitlines():
        if line.startswith("66666"):
            cur = {"intl": line[6:10].strip(), "name": line[30:50].strip(), "points": []}
            storms.append(cur)
        elif cur is not None and len(line) >= 28 and line[9:12] == "002":
            yy = int(line[0:2])
            year = _yy_to_year(yy)
            try:
                dt = datetime(year, int(line[2:4]), int(line[4:6]), int(line[6:8]),
                              tzinfo=timezone.utc)
            except ValueError:
                continue
            pres = line[24:28].strip()
            wind = line[33:36].strip() if len(line) >= 36 else ""
            cur["points"].append({
                "dt_utc": dt.isoformat(),
                "grade": line[13].strip(),
                "lat": int(line[15:18]) / 10.0,
                "lon": int(line[19:23]) / 10.0,
                "pressure": int(pres) if pres else None,
                "wind": int(wind) if wind and wind != "000" else None,
            })
    for s in storms:
        s["year"] = s["points"][0]["dt_utc"][:4] if s["points"] else None
    return storms


def select_period(storms: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    """発生年 (最初の観測点の年) が [start, end] のストームに絞る。"""
    out = []
    for s in storms:
        if not s["points"]:
            continue
        y = int(s["year"])
        if start <= y <= end:
            out.append(s)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=2016)
    ap.add_argument("--end", type=int, default=2025)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    print(f"[fetch_typhoon] downloading {BST_URL}")
    text = download_best_track()
    alls = parse_best_track(text)
    sel = select_period(alls, args.start, args.end)
    print(f"[fetch_typhoon] parsed {len(alls)} storms / {args.start}-{args.end}: {len(sel)}")
    atomic_write_json(args.out, {"records": sel, "count": len(sel),
                                 "period": [args.start, args.end]}, indent=1)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

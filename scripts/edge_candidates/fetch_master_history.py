"""銘柄属性マスタの年次履歴を取得 (point-in-time 参照用)。

単一スナップショット (equities_master.json, as_of 2026-06) を過去イベントに当てると
(1) 規模区分は年次入替なので遡及で誤分類、(2) 上場廃止銘柄がマスタに居らず脱落=生存
バイアス、の2つの穴が出る (⑩中型S高/①B中型PO/⑧医薬品×信用 に影響)。

本スクリプトは /equities/master を **年次** (各年 06-01) で取得し、イベント日時点の
区分を引けるようにする。TOPIX 規模区分の定期見直しは年1回(10月末実効)なので、
年次粒度で実効区分をほぼ再現できる (Nov-Dec イベントは1見直し分ずれる残差あり)。

出力(中間/gitignore): cache/master_history.json = {snapshot_date: {Code: {scale_band,S17Nm,MrgnNm,ScaleCat}}}
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

from scripts._atomic import atomic_write_json
from scripts.edge_candidates.fetch_equities_master import scale_band

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_PATH = REPO_ROOT / "cache" / "master_history.json"
_KEEP = ("ScaleCat", "S17Nm", "MrgnNm", "MktNm")


def fetch_history(years: list[int]) -> dict[str, dict]:
    """各年 06-01 時点のマスタを {snapshot_date: {Code: attrs}} で取得 (resume 可)。"""
    from scripts import _jquants
    hist: dict[str, dict] = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    for y in years:
        date = f"{y}-06-01"
        if date in hist and hist[date]:
            continue
        try:
            rows = _jquants.get_list("/equities/master", date=date)
        except Exception as e:  # noqa: BLE001  当該年取得不可は skip して継続
            print(f"  {date}: 取得不可 {type(e).__name__}")
            continue
        hist[date] = {
            str(r["Code"]): {**{k: r.get(k) for k in _KEEP}, "scale_band": scale_band(r.get("ScaleCat"))}
            for r in rows if r.get("Code")
        }
        print(f"  {date}: {len(hist[date])}銘柄")
        atomic_write_json(CACHE_PATH, hist)
    atomic_write_json(CACHE_PATH, hist)
    return hist


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-year", type=int, default=2016)
    ap.add_argument("--to-year", type=int, default=datetime.date.today().year)
    args = ap.parse_args()
    hist = fetch_history(list(range(args.from_year, args.to_year + 1)))
    print(f"[master_history] snapshots={len(hist)} → {CACHE_PATH}")


if __name__ == "__main__":
    main()

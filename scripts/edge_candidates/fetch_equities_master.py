"""全上場銘柄の属性マスタ (/equities/master) を取得して保存する。

業種(S17/S33)・規模区分(ScaleCat: TOPIX Core30/Large70/Mid400/Small1/Small2)・
信用貸借区分(Mrgn)・市場区分(Mkt) を 1 コールで全銘柄ぶん取得。
PO の規模フィルタ・#4 分割の業種/時価総額代理・kouaku の信用区分付与など、
公式データ基盤(横断属性結合)の土台になる。

留意: 取得日時点のスナップショット。規模区分は年次入替、信用区分も変動し得るため、
過去イベントへの厳密適用が要る場合は date 指定で時点取得する (本スクリプトは最新)。
出力: data/edge_candidates/equities_master.json
"""
from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
# 大型 = TOPIX Core30 + Large70 (時価総額上位)。中型=Mid400、小型=Small1/2/-。
LARGE_SCALES = {"TOPIX Core30", "TOPIX Large70"}
MID_SCALES = {"TOPIX Mid400"}


def scale_band(scale_cat: str | None) -> str:
    """ScaleCat を 大型/中型/小型 の3区分に丸める。"""
    if scale_cat in LARGE_SCALES:
        return "大型"
    if scale_cat in MID_SCALES:
        return "中型"
    return "小型"


def fetch_master(date: str) -> list[dict]:
    """指定日時点の全上場銘柄マスタを返す。"""
    rows = _jquants.get_list("/equities/master", date=date)
    for r in rows:
        r["scale_band"] = scale_band(r.get("ScaleCat"))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=datetime.date.today().isoformat(), help="取得時点 (YYYY-MM-DD)")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    rows = fetch_master(args.date)
    atomic_write_json(args.out, {"records": rows, "count": len(rows), "as_of": args.date}, indent=0)
    print(f"wrote {args.out} ({len(rows)}銘柄 / as_of {args.date})")


if __name__ == "__main__":
    main()

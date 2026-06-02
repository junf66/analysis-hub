"""PO announce レコードに「翌日引け(15:30)リターン」と銘柄属性(規模/信用区分)を付与する。

申し送り#1: next_day_open_to_close_ret(翌寄り→翌引け) が未enrich → 日足から付与。
申し送り#4: /equities/master の scale_band(大型/中型/小型)・信用区分(Mrgn) を結合。
既存の分足由来(9:05〜11:30)は po_records に既にあるので、ここでは日足の引けと属性のみ追加。

出力: data/edge_candidates/po_enriched.json (id→追加attrs のサイドカー)。
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PO_PATH = REPO_ROOT / "data" / "po_records.json"
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "po_enriched.json"


def open_to_close_from_bars(bars: list[dict[str, Any]], event_date: str) -> float | None:
    """event_date 翌営業日の 寄→引 リターン% (調整後優先)。"""
    rows = sorted([b for b in bars if b.get("Date") and b.get("C") is not None], key=lambda b: b["Date"])
    after = [b for b in rows if b["Date"] > event_date]
    if not after:
        return None
    b = after[0]
    o = b.get("AdjO") or b.get("O")
    c = b.get("AdjC") or b.get("C")
    if not o or not c:
        return None
    return (c / o - 1.0) * 100.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--po", type=Path, default=PO_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    po = json.loads(args.po.read_text())
    recs = po.get("records", po if isinstance(po, list) else [])
    master = {r["Code"]: r for r in json.loads(MASTER_PATH.read_text())["records"]}
    ann = [r for r in recs if r.get("stage") == "announce"]
    by_code: dict[str, list[dict]] = defaultdict(list)
    for r in ann:
        by_code[r["code"]].append(r)
    print(f"[po_enrich] announce {len(ann)}件 / {len(by_code)}銘柄")
    out: dict[str, dict[str, Any]] = {}
    for i, (code, rs) in enumerate(by_code.items(), 1):
        code5 = code + "0" if len(code) == 4 else code
        m = master.get(code5, {})
        dates = sorted(r["event_date"] for r in rs)
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=code5,
                                     **{"from": (date.fromisoformat(dates[0]) - timedelta(days=3)).isoformat(),
                                        "to": (date.fromisoformat(dates[-1]) + timedelta(days=8)).isoformat()})
        except _jquants.JQuantsError:
            bars = []
        for r in rs:
            oc = open_to_close_from_bars(bars, r["event_date"]) if bars else None
            out[r["id"]] = {"next_day_open_to_close_ret": oc,
                            "scale_band": m.get("scale_band"), "mrgn": m.get("MrgnNm"),
                            "s17": m.get("S17Nm"), "scale_cat": m.get("ScaleCat")}
        if i % 200 == 0:
            print(f"  ...{i}/{len(by_code)}")
    atomic_write_json(args.out, {"by_id": out, "count": len(out)}, indent=0)
    got = sum(1 for v in out.values() if v["next_day_open_to_close_ret"] is not None)
    print(f"wrote {args.out} (引け付与 {got}/{len(out)})")


if __name__ == "__main__":
    main()

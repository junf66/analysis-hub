"""既存 split_multiday.json に +2/+3日リターン (d2_ret/d3_ret) を追加する。

#4 株式分割の短期保有版を検証するため、d2_ret/d3_ret を後付けで埋める。
既に d3_ret がある event はスキップ (resume 可)。1 event = 1 API call。

実行: 約986件 × ~1秒 ≈ ~16分。チェーン (short-sale) と API 並走。
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPLIT_PATH = REPO_ROOT / "data" / "edge_candidates" / "split_multiday.json"
ADD_DAYS = [2, 3]


def add_short_days(records: list[dict[str, Any]], days: list[int] = ADD_DAYS,
                   *, out_path: Path = SPLIT_PATH, checkpoint_every: int = 100) -> dict[str, int]:
    """各 event に d{n}_ret (n in days) を付与。既存値はスキップ。"""
    todo = 0
    done = 0
    for r in records:
        a = r.setdefault("attrs", {})
        if all(f"d{n}_ret" in a for n in days):
            done += 1
            continue
        todo += 1
    print(f"[d3] 対象 {todo}/{len(records)} 件 (既済 {done})")
    processed = 0
    for i, r in enumerate(records, 1):
        a = r.setdefault("attrs", {})
        if all(f"d{n}_ret" in a for n in days):
            continue
        if a.get("price_error"):
            continue  # 既に取得失敗した event はスキップ
        code = r["code"]
        code5 = code + "0" if len(code) == 4 else code
        ev = date.fromisoformat(r["event_date"])
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=code5,
                                      **{"from": (ev - timedelta(days=5)).isoformat(),
                                         "to": (ev + timedelta(days=15)).isoformat()})
        except _jquants.JQuantsError as e:
            a["price_error"] = str(e)
            continue
        bars = sorted([b for b in bars if b.get("Date") and b.get("C") is not None],
                      key=lambda b: b["Date"])
        after = [b for b in bars if b["Date"] > ev.isoformat()]
        if not after:
            a["price_error"] = "no next-day bar"
            continue
        entry = after[0]
        o = entry.get("AdjO") or entry.get("O")
        if not o:
            a["price_error"] = "no entry open"
            continue
        for n in days:
            if n < len(after):
                target = after[n]
                c = target.get("AdjC") or target.get("C")
                if c:
                    a[f"d{n}_ret"] = (c / o - 1) * 100.0
        processed += 1
        if processed % checkpoint_every == 0:
            atomic_write_json(out_path, {"records": records, "count": len(records),
                                         "partial": True}, indent=1)
            print(f"  ...{processed}/{todo} (event_date {r['event_date']})")
    atomic_write_json(out_path, {"records": records, "count": len(records)}, indent=1)
    print(f"[d3] 完了 {processed}件 追加")
    return {"processed": processed, "skipped": done}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", type=Path, default=SPLIT_PATH)
    args = ap.parse_args()
    recs = json.loads(args.split.read_text())["records"]
    add_short_days(recs, out_path=args.split)


if __name__ == "__main__":
    main()

"""多日保有候補(#4 株式分割)用の +N日リターン付与。

#4: 株式分割発表翌営業日寄り買い → +5日 or +10日後の引けで売却。
TDnet索引の good_split (60件) を対象に日足を取得し d5_ret/d10_ret を計算。
J-Quants の /equities/bars/daily(過去5年範囲) を使用。
出力: data/edge_candidates/split_multiday.json
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
INDEX_PATH = REPO_ROOT / "data" / "edge_candidates" / "tdnet_index.json"
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "split_multiday.json"
DAYS = [1, 5, 10]


def select_split_events(index_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """index から good_split タグを含む (code,event_date) ユニークレコードを返す。"""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for r in index_records:
        if "good_split" not in (r.get("tags") or []):
            continue
        key = (r.get("code"), r.get("event_date"))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        out.append({"code": key[0], "event_date": key[1], "event_type": "stock_split",
                    "source": "tdnet_index", "title": r.get("title"), "attrs": {}})
    return out


def compute_multiday(rec: dict[str, Any]) -> None:
    """rec に entry_open / dN_ret (N in DAYS) を付与 (失敗時は price_error)。"""
    code = rec["code"]
    code5 = code + "0" if len(code) == 4 else code
    ev = date.fromisoformat(rec["event_date"])
    try:
        bars = _jquants.get_list("/equities/bars/daily", code=code5,
                                  **{"from": (ev - timedelta(days=5)).isoformat(),
                                     "to": (ev + timedelta(days=25)).isoformat()})
    except _jquants.JQuantsError as e:
        rec["attrs"]["price_error"] = str(e)
        return
    bars = sorted([b for b in bars if b.get("Date") and b.get("C") is not None],
                  key=lambda b: b["Date"])
    after = [b for b in bars if b["Date"] > ev.isoformat()]
    if not after:
        rec["attrs"]["price_error"] = "no next-day bar"
        return
    entry = after[0]
    o = entry.get("AdjO") or entry.get("O")
    rec["attrs"]["entry_date"] = entry["Date"]
    rec["attrs"]["entry_open"] = o
    if not o:
        rec["attrs"]["price_error"] = "no entry open"
        return
    for n in DAYS:
        if n - 1 < len(after):  # entry が after[0]=+1日目相当として +n日目 = after[n-1+...]
            target = after[min(n, len(after) - 1)]  # n日目 (0-indexed: n)
            c = target.get("AdjC") or target.get("C")
            if c:
                rec["attrs"][f"d{n}_ret"] = (c / o - 1) * 100.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", type=Path, default=INDEX_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    index = json.loads(args.index.read_text())["records"]
    events = select_split_events(index)
    print(f"[split_multiday] 対象 {len(events)}件")
    for i, rec in enumerate(events, 1):
        compute_multiday(rec)
        if i % 20 == 0:
            print(f"  ...{i}/{len(events)}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, {"records": events, "count": len(events)}, indent=1)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

"""split_multiday の event_date>=2024-05-21 (513件) について J-Quants 分足を
取得し、+1日の 9:30 / 11:30 価格を attrs に追加する。

J-Quants 分足 add-on は 2024-05-21 以降のみ。それ以前は分足なしのため
intraday 戦略の検証不能。

ロジック:
  - 各 event の entry_date (=event_date+1 営業日) について /equities/bars/minute
  - 09:30 バーの Open を px_930
  - 11:30 バーの Close を px_1130 (前場引け)
  - 既存の d{N}_ret から close_{+N} を逆算し、新リターンを計算:
      t930_d{N}_ret = (close_{+N} / px_930 - 1) * 100
      t1130_d{N}_ret = (close_{+N} / px_1130 - 1) * 100
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPLIT_PATH = REPO_ROOT / "data" / "edge_candidates" / "split_multiday.json"
MINUTE_AVAILABLE_FROM = "2024-05-21"
DAYS = [3, 5, 10]


def _find_bar(bars: list[dict[str, Any]], time: str) -> dict[str, Any] | None:
    """Time フィールドが time と一致する最初のバーを返す。なければ None。"""
    for b in bars:
        if str(b.get("Time", "")) == time:
            return b
    return None


def enrich_intraday(records: list[dict[str, Any]], *, out_path: Path = SPLIT_PATH,
                    checkpoint_every: int = 50) -> dict[str, int]:
    """対象 event の attrs に px_930 / px_1130 / t930_d{N}_ret / t1130_d{N}_ret を追加。"""
    todo = [r for r in records
            if r.get("event_date", "") >= MINUTE_AVAILABLE_FROM
            and not (r.get("attrs") or {}).get("price_error")
            and (r.get("attrs") or {}).get("entry_date")
            and not (r.get("attrs") or {}).get("intraday_error")
            and (r.get("attrs") or {}).get("px_930") is None]
    print(f"[intraday] 対象 {len(todo)}件")
    processed = 0
    for r in todo:
        a = r["attrs"]
        code = r["code"]
        code5 = code + "0" if len(code) == 4 else code
        try:
            bars = _jquants.get_list("/equities/bars/minute", code=code5,
                                      date=a["entry_date"])
        except _jquants.JQuantsError as e:
            a["intraday_error"] = str(e)
            processed += 1
            continue
        b930 = _find_bar(bars, "09:30")
        b1130 = _find_bar(bars, "11:30")
        if not b930 or not b1130 or not b930.get("O") or not b1130.get("C"):
            a["intraday_error"] = "missing 9:30 or 11:30 bar"
            processed += 1
            continue
        px_930 = b930["O"]
        px_1130 = b1130["C"]
        a["px_930"] = px_930
        a["px_1130"] = px_1130
        entry_open = a.get("entry_open")
        if entry_open:
            for n in DAYS:
                dn = a.get(f"d{n}_ret")
                if dn is None:
                    continue
                close_n = entry_open * (1 + dn / 100.0)
                a[f"t930_d{n}_ret"] = (close_n / px_930 - 1) * 100.0
                a[f"t1130_d{n}_ret"] = (close_n / px_1130 - 1) * 100.0
        processed += 1
        if processed % checkpoint_every == 0:
            atomic_write_json(out_path, {"records": records, "count": len(records),
                                         "partial": True}, indent=1)
            print(f"  ...{processed}/{len(todo)} ({code} {a['entry_date']})")
    atomic_write_json(out_path, {"records": records, "count": len(records)}, indent=1)
    print(f"[intraday] 完了 {processed}件")
    return {"processed": processed}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", type=Path, default=SPLIT_PATH)
    args = ap.parse_args()
    recs = json.loads(args.split.read_text())["records"]
    enrich_intraday(recs, out_path=args.split)


if __name__ == "__main__":
    main()

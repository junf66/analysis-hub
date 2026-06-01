"""J-Quants Standard /markets/short-sale-report を日次走査し、
大口空売り報告(全銘柄・10年)を取得して data/edge_candidates/short_sale_report.json に保存。

公表日 (disc_date) で走査。空応答日は無視・データ有り日のみ蓄積。
checkpoint+resume でクラッシュ耐性。用途: #8 補助・分析の追加軸。
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
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "short_sale_report.json"


def _load_checkpoint(out_path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not out_path.exists():
        return [], None
    try:
        d = json.loads(out_path.read_text())
        return d.get("records", []), d.get("last_date")
    except (json.JSONDecodeError, OSError):
        return [], None


def fetch_short_sale(date_from: str, date_to: str, *, out_path: Path = OUT_PATH,
                     checkpoint_every: int = 30) -> list[dict[str, Any]]:
    """/markets/short-sale-report を日次走査して全行蓄積。disc_date で走査。"""
    records, last = _load_checkpoint(out_path)
    start = (date.fromisoformat(last) + timedelta(days=1)) if last else date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if last:
        print(f"[short_sale] resume: {len(records)}件 / {last} の翌日から")
    d = start
    scanned = 0
    while d <= end:
        try:
            rows = _jquants.get_list("/markets/short-sale-report", disc_date=d.isoformat())
        except _jquants.JQuantsError:
            rows = []
        if rows:
            records.extend(rows)
        scanned += 1
        if scanned % checkpoint_every == 0:
            atomic_write_json(out_path, {"records": records, "count": len(records),
                                         "last_date": d.isoformat(), "partial": True}, indent=0)
            print(f"  ...{d.isoformat()} 累計 {len(records)}件 (checkpoint)")
        d += timedelta(days=1)
    atomic_write_json(out_path, {"records": records, "count": len(records),
                                 "last_date": end.isoformat()}, indent=0)
    print(f"[short_sale] 完了 {len(records)}件 ({date_from}〜{date_to})")
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2016-06-01", help="取得開始日")
    ap.add_argument("--until", default="2026-12-31", help="取得終了日")
    ap.add_argument("--out", type=Path, default=OUT_PATH, help="出力 JSON")
    args = ap.parse_args()
    fetch_short_sale(args.since, args.until, out_path=args.out)


if __name__ == "__main__":
    main()

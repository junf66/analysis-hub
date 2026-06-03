"""J-Quants Standard プランの指数データ /indices/bars/daily/topix から
TOPIX 日次OHLC (約10年分) を取得し data/topix_daily.json に保存する。

用途: 既存エッジ(②リートPO決定前ショート、中型decide短)のベータ調整(超過収益)算出。
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"


def fetch_topix(date_from: str, date_to: str) -> list[dict[str, Any]]:
    """TOPIX 日次OHLC を [date_from, date_to] で取得し Date昇順で返す。"""
    rows = _jquants.get_list("/indices/bars/daily/topix",
                              **{"from": date_from, "to": date_to})
    rows.sort(key=lambda r: r.get("Date") or "")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2016-01-01", help="取得開始日")
    ap.add_argument("--until", default="2026-12-31", help="取得終了日")
    ap.add_argument("--out", type=Path, default=OUT_PATH, help="出力 JSON")
    args = ap.parse_args()
    rows = fetch_topix(args.since, args.until)
    print(f"[topix] {len(rows)}日 取得 ({rows[0]['Date']}〜{rows[-1]['Date']})" if rows else "[topix] 取得0件")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, {"records": rows, "count": len(rows)}, indent=0)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

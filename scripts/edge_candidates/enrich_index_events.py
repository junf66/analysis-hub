"""TDnet索引の好材料イベントに価格(gap/分足9:10〜11:30)+減益%を付与する。

#1上方修正 / #3増配 / #5業務提携・受注 の検証用 (intraday出口)。同一 code+date は
1回に集約して enrich。fetch_buyback.build を流用 (SIGALRM timeout+checkpoint+resume)。
出力: data/edge_candidates/enriched_events.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json
from scripts.fetch_buyback import build

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INDEX_PATH = REPO_ROOT / "data" / "edge_candidates" / "tdnet_index.json"
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "enriched_events.json"

# intraday検証する候補のタグ (#1/#3/#5)。#4分割は多日保有のため別enrich。
INTRADAY_TAGS = {"good_kessan_up", "good_zouhai", "good_teikei", "good_juchu"}


def select_events(index_records: list[dict[str, Any]],
                  tags: set[str] = INTRADAY_TAGS) -> list[dict[str, Any]]:
    """索引から対象タグを含むイベントを (code,event_date) 単位に集約した record 群を返す。"""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for r in index_records:
        rtags = set(r.get("tags") or [])
        if not (rtags & tags):
            continue
        key = (r.get("code"), r.get("event_date"))
        if not key[0] or not key[1] or key in seen:
            continue
        seen[key] = {"code": key[0], "event_date": key[1], "event_type": "tdnet_event",
                     "source": "tdnet_index", "attrs": {},
                     "tags": sorted(rtags & (INTRADAY_TAGS | {"good_split"}))}
    return list(seen.values())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", type=Path, default=INDEX_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    index = json.loads(args.index.read_text())["records"]
    events = select_events(index)
    print(f"[enrich_index] 対象イベント {len(events)}件 (code+date集約後)")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    records = build(events, out_path=args.out)
    atomic_write_json(args.out, {"records": records, "count": len(records)}, indent=0)
    print(f"wrote {args.out} ({len(records)} records)")


if __name__ == "__main__":
    main()

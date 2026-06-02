"""buyback_earnings.json (自社株買い×同日決算) に翌営業日リターンを付与する。

約定可能性: 開示の多くは大引け後/引け間際 → 翌営業日寄りで entry。
出口: d0=当日寄→引(引け) / d1/d3/d5 = +N営業日引け。d1/d3/d5 は TOPIX(β=1)超過α も付与。
(d0 は当日内のため β 影響小→ raw のまま)

出力: data/edge_candidates/buyback_earnings.json を上書き (リターン付与)。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts._atomic import atomic_write_json
from scripts.edge_candidates import topix_adjust
from scripts.edge_candidates.enrich_common import enrich_events_by_code

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PATH = REPO_ROOT / "data" / "edge_candidates" / "buyback_earnings.json"
DAYS = [0, 1, 3, 5]          # 0 = 当日寄→引(引け)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=PATH)
    args = ap.parse_args()
    events = json.loads(args.path.read_text())["records"]
    ncodes = len({e["code"] for e in events})
    print(f"[buyback_returns] {len(events)}件 / {ncodes}銘柄 enrich開始")

    def _ckpt() -> None:
        atomic_write_json(args.path, {"records": events, "count": len(events), "partial": True}, indent=0)
        print(f"  ...checkpoint")

    enrich_events_by_code(events, DAYS, skip_bars=0, on_checkpoint=_ckpt)
    topix_adjust.enrich_with_alpha(events, [1, 3, 5])   # d0 は intraday のため α 不要
    atomic_write_json(args.path, {"records": events, "count": len(events)}, indent=0)
    got = sum(1 for e in events if (e.get("attrs") or {}).get("d0_ret") is not None)
    print(f"wrote {args.path} (d0_ret付与 {got}/{len(events)})")


if __name__ == "__main__":
    main()

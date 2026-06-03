"""J-Quants /listed/info から東証上場銘柄のスナップショットを取得し、
data/edge_candidates/listed_universe.json に保存する。

RSI 等の銘柄横断戦略の universe (~4000 銘柄) を確定するための前段。
1 コールで全銘柄返るので checkpoint 不要。
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "listed_universe.json"


def fetch_universe() -> list[dict[str, Any]]:
    """全上場銘柄の (Code, CompanyName, Sector33Code, MarketCode 等) を返す。"""
    rows = _jquants.get_list("/listed/info")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    rows = fetch_universe()
    print(f"[universe] {len(rows)}銘柄 取得")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, {"records": rows, "count": len(rows)}, indent=0)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

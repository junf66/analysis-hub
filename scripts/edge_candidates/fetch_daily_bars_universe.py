"""universe 内全銘柄について /equities/bars/daily を [since, until] で取得し、
data/edge_candidates/daily_bars_universe.json に蓄積する。

RSI(14) など銘柄横断の price-based 戦略の検証用。1銘柄=1API コール
(全期間まとめて取れる)。listed_universe.json を入力に取り、code 単位で
checkpoint+resume。クラッシュ耐性あり。

データサイズ目安: 4000銘柄 × ~750営業日 × ~10フィールド ≈ ~3M 行 / ~1GB JSON。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
UNIV_PATH = REPO_ROOT / "data" / "edge_candidates" / "listed_universe.json"
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "daily_bars_universe.json"

KEEP_FIELDS = ("Date", "Open", "High", "Low", "Close",
               "AdjustmentOpen", "AdjustmentHigh", "AdjustmentLow", "AdjustmentClose",
               "Volume", "AdjustmentVolume")


def _load_checkpoint(out_path: Path) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    if not out_path.exists():
        return {}, set()
    try:
        d = json.loads(out_path.read_text())
        bars = d.get("bars", {}) or {}
        return bars, set(d.get("done", []) or [])
    except (json.JSONDecodeError, OSError):
        return {}, set()


def _shrink(bar: dict[str, Any]) -> dict[str, Any]:
    return {k: bar.get(k) for k in KEEP_FIELDS if bar.get(k) is not None}


def fetch_bars(codes: list[str], date_from: str, date_to: str, *,
               out_path: Path = OUT_PATH, checkpoint_every: int = 50) -> dict[str, list[dict[str, Any]]]:
    """各 code の日足を取得して bars[code]=[bar,...] に蓄積。done に済み code を入れて resume。"""
    bars, done = _load_checkpoint(out_path)
    if done:
        print(f"[bars] resume: {len(done)}銘柄 済み")
    todo = [c for c in codes if c not in done]
    print(f"[bars] {len(todo)}銘柄 取得 ({len(done)}/{len(codes)} 既済)")
    for i, code in enumerate(todo, 1):
        try:
            rows = _jquants.get_list("/equities/bars/daily", code=code,
                                      **{"from": date_from, "to": date_to})
        except _jquants.JQuantsError as e:
            bars[code] = []
            done.add(code)
            print(f"  {code}: error {e}")
            continue
        kept = [_shrink(r) for r in rows if r.get("Date")]
        kept.sort(key=lambda b: b["Date"])
        bars[code] = kept
        done.add(code)
        if i % checkpoint_every == 0:
            atomic_write_json(out_path, {"bars": bars, "done": sorted(done),
                                         "count_codes": len(bars), "partial": True}, indent=0)
            print(f"  ...{i}/{len(todo)} 銘柄 完了 ({code} {len(kept)}本)")
    atomic_write_json(out_path, {"bars": bars, "done": sorted(done),
                                 "count_codes": len(bars)}, indent=0)
    print(f"[bars] 完了 {len(bars)}銘柄")
    return bars


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", type=Path, default=UNIV_PATH)
    ap.add_argument("--since", default="2024-01-01")
    ap.add_argument("--until", default="2026-05-31")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--market", default="0111,0112,0113",
                    help="MarketCode をカンマ区切りで指定 (空=全て)。"
                         "0111=東P, 0112=東S, 0113=東G")
    args = ap.parse_args()
    univ = json.loads(args.universe.read_text())["records"]
    markets = [m.strip() for m in args.market.split(",") if m.strip()] if args.market else []
    codes = []
    for u in univ:
        c = u.get("Code")
        if not c:
            continue
        if markets and u.get("MarketCode") not in markets:
            continue
        codes.append(c)
    print(f"[bars] universe filtered: {len(codes)}銘柄 (market={args.market or 'ALL'})")
    fetch_bars(codes, args.since, args.until, out_path=args.out)


if __name__ == "__main__":
    main()

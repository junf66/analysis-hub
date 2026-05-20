"""kouaku_records.json に価格データを付与する。

各レコードについて:
  - event_date  終値 → prev_close
  - 翌営業日   始値 → next_open
  - 翌営業日   終値 → next_close
  - gap_pct, next_day_close_ret, next_day_open_to_close_ret を計算

J-Quants v2 `/equities/bars/daily?code=...&from=...&to=...` を利用し、
event_date ±5 営業日のローソク足を一度に取得して使い回す。

分足 (~9:10 リターン) は v2 では `/equities/bars/minute` 等を契約次第で使える想定。
現環境では未契約 / 未確認なので、まずは日足ベースで EV を計算しておく。
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts import _jquants

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_ROOT / "data" / "kouaku_records.json"


def _bars(code: str, since: date, until: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in _jquants.get(
        "/equities/bars/daily",
        code=code,
        **{"from": since.isoformat(), "to": until.isoformat()},
    ):
        rows.append(r)
    rows.sort(key=lambda r: r.get("Date") or "")
    return rows


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old * 100.0


def enrich_record(rec: dict[str, Any], *, window_days: int = 10) -> dict[str, Any]:
    code = rec["code"]
    ev = date.fromisoformat(rec["event_date"])
    since = ev - timedelta(days=window_days)
    until = ev + timedelta(days=window_days)
    bars = _bars(code, since, until)
    if not bars:
        rec["attrs"]["price_error"] = "no bars"
        return rec

    # event_date 当日 (なければ <= ev の最終営業日)
    event_idx = None
    for i, b in enumerate(bars):
        if b["Date"] == ev.isoformat():
            event_idx = i
            break
    if event_idx is None:
        # 直前の bar に fallback (休場日に開示 → 翌営業日寄り)
        for i, b in enumerate(bars):
            if b["Date"] > ev.isoformat():
                event_idx = i - 1
                break
    if event_idx is None or event_idx + 1 >= len(bars):
        rec["attrs"]["price_error"] = "no next-day bar"
        return rec

    today = bars[event_idx]
    nxt = bars[event_idx + 1]

    prev_close = today.get("AdjC") or today.get("C")
    next_open = nxt.get("AdjO") or nxt.get("O")
    next_high = nxt.get("AdjH") or nxt.get("H")
    next_low = nxt.get("AdjL") or nxt.get("L")
    next_close = nxt.get("AdjC") or nxt.get("C")

    rec["attrs"].update({
        "prev_close": prev_close,
        "next_open": next_open,
        "next_high": next_high,
        "next_low": next_low,
        "next_close": next_close,
        "gap_pct": _pct(next_open, prev_close),
        "next_day_open_to_close_ret": _pct(next_close, next_open),
        "next_day_open_to_high_ret": _pct(next_high, next_open),
        "next_day_open_to_low_ret": _pct(next_low, next_open),
        "next_day_full_ret": _pct(next_close, prev_close),
        "event_bar_date": today.get("Date"),
        "next_bar_date": nxt.get("Date"),
    })
    return rec


def enrich_all(records: list[dict[str, Any]], *, sleep_sec: float = 0.0) -> list[dict[str, Any]]:
    import time as _time

    out: list[dict[str, Any]] = []
    for i, rec in enumerate(records, 1):
        try:
            out.append(enrich_record(rec))
        except _jquants.JQuantsError as e:
            rec["attrs"]["price_error"] = str(e)
            out.append(rec)
        if sleep_sec:
            _time.sleep(sleep_sec)
        if i % 25 == 0:
            print(f"  ... enriched {i}/{len(records)}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--sleep", type=float, default=0.05, help="API レート制限緩和")
    args = ap.parse_args()

    payload = json.loads(args.path.read_text())
    records = payload["records"]
    print(f"enriching {len(records)} records")
    payload["records"] = enrich_all(records, sleep_sec=args.sleep)
    args.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"saved → {args.path}")


if __name__ == "__main__":
    main()

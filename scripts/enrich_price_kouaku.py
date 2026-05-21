"""kouaku_records.json に価格データを付与する。

各レコードについて:
  - event_date  終値 → prev_close
  - 翌営業日   始値 → next_open
  - 翌営業日   終値 → next_close
  - gap_pct, next_day_close_ret, next_day_open_to_close_ret を計算
  - 分足アドオン (`/equities/bars/minute`) で 9:05/9:10/9:15/9:30/10:00/前場引 リターン

J-Quants v2 `/equities/bars/daily?code=...&from=...&to=...` で event_date ±10 営業日のローソク足を、
`/equities/bars/minute?code=...&date=...` で翌営業日の分足を取得する。
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


def _minute_bars(code: str, d: str) -> list[dict[str, Any]]:
    """指定日 1 日分の分足。`d` は ISO 形式 (YYYY-MM-DD) or YYYYMMDD。"""
    rows = _jquants.get_list("/equities/bars/minute", code=code, date=d)
    rows.sort(key=lambda r: r.get("Time") or "")
    return rows


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old * 100.0


# 値幅制限ロック判定: 翌寄り = 翌高 = 翌安 = 翌引 (=> 約定不能) かつ |gap| >= 15%
# J-Quants 日足は yen 単位の正確な float なので == は安全だが、念のため tolerance を許容。
_LIMIT_LOCK_PRICE_TOL = 0.01  # 0.01 円
_LIMIT_LOCK_GAP_PCT = 15.0


def _is_limit_locked(prev_close: Any, no: Any, nh: Any, nl: Any, nc: Any) -> bool:
    if None in (prev_close, no, nh, nl, nc) or not prev_close:
        return False
    tol = _LIMIT_LOCK_PRICE_TOL
    if abs(nh - nl) > tol or abs(nh - no) > tol or abs(nh - nc) > tol:
        return False
    gap_pct = (no - prev_close) / prev_close * 100.0
    return abs(gap_pct) >= _LIMIT_LOCK_GAP_PCT


# 9:00 寄り → 各時刻 (bar の close) の経過時刻でリターンを取る。
_INTRADAY_TARGETS = [
    ("09:05", "next_day_905_ret"),
    ("09:10", "next_day_910_ret"),
    ("09:15", "next_day_915_ret"),
    ("09:30", "next_day_930_ret"),
    ("10:00", "next_day_1000_ret"),
    ("11:30", "next_day_morning_ret"),
]


def _enrich_minute(rec: dict[str, Any], code: str, next_date: str) -> None:
    """翌営業日の分足から 9:05〜前場引リターンを補完。失敗時は静かに skip。

    illiquid 銘柄では 9:00 に歩み値がない (= bar が存在しない) ことがあるので、
    初値は「Time が最も早い bar の O」を採用。各 target 時刻 t は「Time >= t の
    最初の bar の C」(なければ最後の bar の C) を採用する。
    """
    try:
        mbars = _minute_bars(code, next_date)
    except _jquants.JQuantsError as e:
        rec["attrs"]["minute_error"] = str(e)
        return
    if not mbars:
        rec["attrs"]["minute_error"] = "no minute bars"
        return
    # 初値: 最初の bar の O
    first = mbars[0]
    base = first.get("O")
    if not base:
        rec["attrs"]["minute_error"] = "no open in first bar"
        return
    rec["attrs"]["next_open_900"] = base
    rec["attrs"]["next_open_first_time"] = first.get("Time")
    rec["attrs"].pop("minute_error", None)
    for t, key in _INTRADAY_TARGETS:
        # Time >= t を満たす最初の bar
        b = next((mb for mb in mbars if (mb.get("Time") or "") >= t), None)
        if b is None:
            continue
        c = b.get("C")
        if c is None:
            continue
        rec["attrs"][key] = _pct(c, base)


def enrich_record(rec: dict[str, Any], *, window_days: int = 10) -> dict[str, Any]:
    """1 record に日足 + 分足由来の attrs を書き込んで返す (失敗時は price_error/minute_error をセット)。"""
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
        "limit_locked": _is_limit_locked(prev_close, next_open, next_high, next_low, next_close),
    })
    next_date = nxt.get("Date")
    if next_date:
        _enrich_minute(rec, code, next_date)
    return rec


def _already_enriched(rec: dict[str, Any]) -> bool:
    a = rec.get("attrs") or {}
    # 価格 enrich 完了サイン: next_open がセットされている、または price_error が記録済
    return a.get("next_open") is not None or a.get("price_error") is not None


def enrich_all(records: list[dict[str, Any]], *, sleep_sec: float = 0.0, force: bool = False) -> list[dict[str, Any]]:
    """records 全件に enrich を適用 (force=False なら既に enrich 済の record は skip)。"""
    import time as _time

    out: list[dict[str, Any]] = []
    skipped = 0
    for i, rec in enumerate(records, 1):
        if not force and _already_enriched(rec):
            out.append(rec)
            skipped += 1
            continue
        try:
            out.append(enrich_record(rec))
        except _jquants.JQuantsError as e:
            rec["attrs"]["price_error"] = str(e)
            out.append(rec)
        if sleep_sec:
            _time.sleep(sleep_sec)
        if i % 25 == 0:
            print(f"  ... enriched {i}/{len(records)} (skipped already-enriched: {skipped})")
    if skipped:
        print(f"  skipped {skipped} already-enriched records (use --force to re-fetch)")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--sleep", type=float, default=0.05, help="API レート制限緩和")
    ap.add_argument("--force", action="store_true", help="既存 enrich を破棄して再取得")
    args = ap.parse_args()

    payload = json.loads(args.path.read_text())
    records = payload["records"]
    print(f"enriching {len(records)} records (force={args.force})")
    payload["records"] = enrich_all(records, sleep_sec=args.sleep, force=args.force)
    from scripts._atomic import atomic_write_json
    atomic_write_json(args.path, payload)
    print(f"saved → {args.path}")


if __name__ == "__main__":
    main()

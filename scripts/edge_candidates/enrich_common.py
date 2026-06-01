"""イベント (code, event_date) 周辺の日足から +N日リターンを付与する共通ヘルパ。

#7 信用買残激減 / #8 空売り残急増 など「シグナル日 → 約定可能日寄り → +N日引け」の
ロング検証で共有する。約定可能性のため skip_bars で公表ラグ (signal の翌営業日以降) を
吸収できる:
  - entry = event_date より後の (skip_bars+1) 本目の足の寄り
  - d{n}_ret = entry から n 営業日後の足の引け (調整後優先)

returns_from_bars は純関数 (テスト可)、compute_event_returns が J-Quants fetch ラッパ。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from scripts import _jquants


def returns_from_bars(bars: list[dict[str, Any]], event_date: str, days: list[int],
                      *, skip_bars: int = 0) -> dict[str, Any]:
    """日足 bars から entry_open / d{n}_ret を計算した attrs dict を返す。

    skip_bars: event_date 翌営業日を 0 とし、公表ラグ分だけ entry を後ろにずらす本数。
    """
    rows = sorted([b for b in bars if b.get("Date") and b.get("C") is not None],
                  key=lambda b: b["Date"])
    after = [b for b in rows if b["Date"] > event_date]
    if len(after) <= skip_bars:
        return {"price_error": "no entry bar"}
    entry = after[skip_bars]
    o = entry.get("AdjO") or entry.get("O")
    if not o:
        return {"price_error": "no entry open"}
    out: dict[str, Any] = {"entry_date": entry["Date"], "entry_open": o}
    for n in days:
        idx = skip_bars + n
        if idx < len(after):
            c = after[idx].get("AdjC") or after[idx].get("C")
            if c:
                out[f"d{n}_ret"] = (c / o - 1.0) * 100.0
    return out


def compute_event_returns(rec: dict[str, Any], days: list[int], *, skip_bars: int = 0,
                          window_fwd_days: int = 40) -> None:
    """rec["code"], rec["event_date"] から日足を取得し attrs に entry_open/d{n}_ret を付与。

    skip_bars で公表ラグを吸収。失敗時は attrs["price_error"]。1 event = 1 API call。
    """
    a = rec.setdefault("attrs", {})
    code = rec["code"]
    code5 = code + "0" if len(code) == 4 else code
    ev = date.fromisoformat(rec["event_date"])
    try:
        bars = _jquants.get_list("/equities/bars/daily", code=code5,
                                 **{"from": (ev - timedelta(days=5)).isoformat(),
                                    "to": (ev + timedelta(days=window_fwd_days)).isoformat()})
    except _jquants.JQuantsError as e:
        a["price_error"] = str(e)
        return
    res = returns_from_bars(bars, rec["event_date"], days, skip_bars=skip_bars)
    a.update(res)

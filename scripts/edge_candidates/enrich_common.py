"""イベント (code, event_date) 周辺の日足から +N日リターンを付与する共通ヘルパ。

#7 信用買残激減 / #8 空売り残急増 など「シグナル日 → 約定可能日寄り → +N日引け」の
ロング検証で共有する。約定可能性のため skip_bars で公表ラグ (signal の翌営業日以降) を
吸収できる:
  - entry = event_date より後の (skip_bars+1) 本目の足の寄り
  - d{n}_ret = entry から n 営業日後の足の引け (調整後優先)

returns_from_bars は純関数 (テスト可)、compute_event_returns が J-Quants fetch ラッパ。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Callable

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


def enrich_events_by_code(events: list[dict[str, Any]], days: list[int], *, skip_bars: int = 0,
                          window_back: int = 5, window_fwd: int = 40,
                          on_checkpoint: Callable[[], None] | None = None,
                          checkpoint_every: int = 200) -> int:
    """events を銘柄ごとにまとめ、1銘柄=1 API call で全イベントの d{n}_ret を付与する。

    同一銘柄の複数イベントを 1 度の日足取得 ([最古event-window_back, 最新event+window_fwd])
    でまかなうため、event 単位 fetch (compute_event_returns) より大幅に呼び出しが減る。
    on_checkpoint があれば checkpoint_every 銘柄ごとに呼ぶ (途中保存用)。処理 event 数を返す。
    """
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_code[e["code"]].append(e)
    processed = 0
    for ci, (code, evs) in enumerate(by_code.items(), 1):
        code5 = code + "0" if len(code) == 4 else code
        dates = sorted(e["event_date"] for e in evs)
        d_from = (date.fromisoformat(dates[0]) - timedelta(days=window_back)).isoformat()
        d_to = (date.fromisoformat(dates[-1]) + timedelta(days=window_fwd)).isoformat()
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=code5,
                                     **{"from": d_from, "to": d_to})
        except _jquants.JQuantsError as err:
            for ev in evs:
                ev.setdefault("attrs", {})["price_error"] = str(err)
            bars = None
        if bars is not None:
            for ev in evs:
                ev.setdefault("attrs", {}).update(
                    returns_from_bars(bars, ev["event_date"], days, skip_bars=skip_bars))
        processed += len(evs)
        if on_checkpoint and ci % checkpoint_every == 0:
            on_checkpoint()
    return processed

"""split_multiday の event_date>=2024-06-01 について J-Quants 分足を
取得し、+1日 (entry_date) の 始値/9:30/11:30/引け の **生値** を attrs に追加する。

J-Quants 分足 add-on の契約範囲は 2024-06-01 以降 (実 API 照合で確定。
2024-05-21〜05-31 は契約対象外で HTTP 400)。それ以前は分足なしのため
intraday 戦略の検証不能。

重要: 分足は分割調整なしの**生値**。entry_open(AdjO) や d{N}_ret(調整値ベース)
と直接混ぜると分割係数の分だけ壊れるため、ここでは生値のみ保存し、派生
リターンの計算は analyze_split_intraday 側で生値ベースに統一して行う
(エントリー→+N の窓内に権利落ちは入らないため raw 窓リターン = 調整リターン)。

保存する生値:
  - px_open  : 前場最初のバー(=09:00 寄り)の Open   ← 生値ベースの基準
  - px_930   : 09:30 以降の前場最初のバーの Open
  - px_1130  : 11:30 以前の最後の約定バーの Close (前場引け)
  - px_close : 最大 Time バーの Close (大引け、15:00→15:30 変動に対応)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPLIT_PATH = REPO_ROOT / "data" / "edge_candidates" / "split_multiday.json"
MINUTE_AVAILABLE_FROM = "2024-06-01"
DAYS = [3, 5, 10]


MORNING_CLOSE = "11:30"  # 前場引け時刻


def _px_open(bars: list[dict[str, Any]]) -> float | None:
    """前場最初(=最小Time)のバーの Open = 生値ベースの寄り。"""
    morning = [b for b in bars if str(b.get("Time", "")) <= MORNING_CLOSE and b.get("O")]
    if not morning:
        return None
    return min(morning, key=lambda b: str(b.get("Time", "")))["O"]


def _px_930(bars: list[dict[str, Any]]) -> float | None:
    """09:30 時点の Open。約定の薄い銘柄向けに「09:30以降の前場最初のバー」を採る。"""
    morning = [b for b in bars if str(b.get("Time", "")) <= MORNING_CLOSE]
    for b in sorted(morning, key=lambda b: str(b.get("Time", ""))):
        if str(b.get("Time", "")) >= "09:30" and b.get("O"):
            return b["O"]
    return None


def _px_1130(bars: list[dict[str, Any]]) -> float | None:
    """前場引け = 11:30 以前の最後の約定バーの Close (11:30 ちょうどが無くても可)。"""
    morning = [b for b in bars if str(b.get("Time", "")) <= MORNING_CLOSE and b.get("C")]
    if not morning:
        return None
    return max(morning, key=lambda b: str(b.get("Time", "")))["C"]


def enrich_intraday(records: list[dict[str, Any]], *, out_path: Path = SPLIT_PATH,
                    checkpoint_every: int = 50) -> dict[str, int]:
    """対象 event の attrs に px_930 / px_1130 / t930_d{N}_ret / t1130_d{N}_ret を追加。"""
    # px_open 未取得を対象 (生値基準の追加・バー選択改善のため px_930 済も再処理)。
    todo = [r for r in records
            if r.get("event_date", "") >= MINUTE_AVAILABLE_FROM
            and not (r.get("attrs") or {}).get("price_error")
            and (r.get("attrs") or {}).get("entry_date")
            and (r.get("attrs") or {}).get("px_open") is None]
    print(f"[intraday] 対象 {len(todo)}件")
    processed = 0
    for r in todo:
        a = r["attrs"]
        code = r["code"]
        code5 = code + "0" if len(code) == 4 else code
        try:
            bars = _jquants.get_list("/equities/bars/minute", code=code5,
                                      date=a["entry_date"])
        except _jquants.JQuantsError as e:
            a["intraday_error"] = str(e)
            processed += 1
            continue
        px_open = _px_open(bars)
        px_930 = _px_930(bars)
        px_1130 = _px_1130(bars)
        # 引け値 = 最も遅い時刻のバーの Close。
        with_c = [b for b in bars if b.get("C")]
        px_close = max(with_c, key=lambda b: str(b.get("Time", "")))["C"] if with_c else None
        if not px_open or not px_930 or not px_1130 or not px_close:
            a["intraday_error"] = "missing intraday bar"
            processed += 1
            continue
        a.pop("intraday_error", None)  # 再試行で成功したらエラーを消す
        a["px_open"] = px_open
        a["px_930"] = px_930
        a["px_1130"] = px_1130
        a["px_close"] = px_close
        # 旧バージョンが書いた壊れた派生値が残っていれば除去 (analyze 側で生値再計算)。
        for n in DAYS:
            for tag in ("t930", "t1130", "tclose"):
                a.pop(f"{tag}_d{n}_ret", None)
        processed += 1
        if processed % checkpoint_every == 0:
            atomic_write_json(out_path, {"records": records, "count": len(records),
                                         "partial": True}, indent=1)
            print(f"  ...{processed}/{len(todo)} ({code} {a['entry_date']})")
    atomic_write_json(out_path, {"records": records, "count": len(records)}, indent=1)
    print(f"[intraday] 完了 {processed}件")
    return {"processed": processed}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", type=Path, default=SPLIT_PATH)
    args = ap.parse_args()
    recs = json.loads(args.split.read_text())["records"]
    enrich_intraday(recs, out_path=args.split)


if __name__ == "__main__":
    main()

"""自社株買い (TDnet 適時開示) を取得し、価格 (gap/分足 9:10〜11:30) と減益% を付与する。

【契約前の準備スクリプト】TDnet アドオン契約後に実行する想定。
- 自社株買い一覧: J-Quants Pro `/markets/share_buyback_tdnet` (要 TDnet アドオン、未検証)
- 価格 enrich   : enrich_price_kouaku.enrich_record を再利用 (日足+分足、検証済)
- 減益%        : /fins/summary の NxFNp(来期予想) vs NP(今期実績) (検証済)

出力: data/buyback_records.json (共通スキーマ: code / event_date / attrs)

検証用: 自社株買い一覧が取れない契約前でも、`--events CODE:YYYY-MM-DD,...` で
手動イベントを与えれば price+減益 の enrich を試せる (例: キッコーマン 2801:2026-04-24)。
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json
from scripts.enrich_price_kouaku import enrich_record

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data" / "buyback_records.json"

# 自社株買いフィードの規模フィールド候補 (実スキーマ未確認のため複数試す)
_SIZE_FIELD_CANDIDATES = [
    "PlannedTotalSharesPct", "PurchaseSharesRatio", "RatioToOutstanding",
    "TotalSharesToBuy", "MaxSharesToBuy", "PlannedShares",
    "MaxPurchaseAmount", "TotalPurchaseAmount", "PlannedAmount",
]
_DATE_FIELD_CANDIDATES = ["DisclosedDate", "DiscDate", "Date", "DisclosureDate"]


def _f(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def fetch_buyback_events(date_from: str, date_to: str) -> list[dict[str, Any]]:
    """Pro エンドポイントから自社株買い開示を取得 (要 TDnet アドオン)。

    スキーマ未確認なので raw をそのまま返す。最初の1件で keys を出力する。
    """
    rows = _jquants.get_list(
        "/markets/share_buyback_tdnet", base=_jquants.PRO_BASE_URL,
        **{"from": date_from, "to": date_to},
    )
    if rows:
        print(f"[buyback] 取得 {len(rows)}件  実スキーマ keys = {sorted(rows[0].keys())}")
    return rows


def _extract_size(raw: dict[str, Any]) -> dict[str, Any]:
    """raw から規模らしきフィールドを拾う (見つかった候補を全部保持)。"""
    out: dict[str, Any] = {"buyback_raw": raw}
    for k in _SIZE_FIELD_CANDIDATES:
        if k in raw and raw[k] not in (None, ""):
            out[f"buyback_{k}"] = raw[k]
    return out


def buyback_to_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    """raw 自社株買い行を共通スキーマ record に変換 (code を4桁化、規模フィールド抽出)。"""
    code = raw.get("Code") or raw.get("code")
    date = next((raw[k] for k in _DATE_FIELD_CANDIDATES if raw.get(k)), None)
    if not code or not date:
        return None
    code = str(code)
    if len(code) == 5 and code.endswith("0"):
        code4 = code[:4]
    else:
        code4 = code
    return {
        "code": code4,
        "event_date": str(date)[:10],
        "event_type": "share_buyback",
        "source": "tdnet_share_buyback",
        "attrs": _extract_size(raw),
    }


def attach_earnings(rec: dict[str, Any]) -> dict[str, Any]:
    """/fins/summary から、event_date 時点の減益% (来期予想NP vs 今期実績NP) を付与。"""
    code = rec["code"]
    code5 = code + "0" if len(code) == 4 else code
    try:
        rows = _jquants.get_list("/fins/summary", code=code5)
    except _jquants.JQuantsError as e:
        rec["attrs"]["earnings_error"] = str(e)
        return rec
    ev = rec["event_date"]
    # event_date 以前で最も近い開示 (この開示が材料)
    cands = [r for r in rows if (r.get("DiscDate") or "") <= ev]
    if not cands:
        rec["attrs"]["earnings_error"] = "no statement on/before event"
        return rec
    s = max(cands, key=lambda r: r.get("DiscDate") or "")
    np_act = _f(s.get("NP"))           # 今期実績NP
    np_nxf = _f(s.get("NxFNp"))        # 来期予想NP (FY決算時)
    np_fcur = _f(s.get("FNP"))         # 今期予想NP (中間決算時)
    # 減益%: 来期予想 vs 今期実績 を主とし、無ければ今期予想を使う
    decline = None
    if np_nxf is not None and np_act not in (None, 0):
        decline = (np_nxf - np_act) / np_act * 100.0
    rec["attrs"].update({
        "earn_disc_date": s.get("DiscDate"),
        "earn_doctype": s.get("DocType"),
        "np_actual": np_act,
        "np_forecast_next": np_nxf,
        "np_forecast_cur": np_fcur,
        "forecast_decline_pct": decline,
        "shares_out": _f(s.get("ShOutFY")),
    })
    return rec


def build(events: list[dict[str, Any]], *, sleep_sec: float = 0.2) -> list[dict[str, Any]]:
    """events 各件に price (gap/分足) + 減益% を付与した records を返す。"""
    import time
    out = []
    for i, rec in enumerate(events, 1):
        try:
            enrich_record(rec)          # gap / 9:10〜11:30 / limit-lock (検証済ロジック)
            attach_earnings(rec)
        except _jquants.JQuantsError as e:
            rec["attrs"]["price_error"] = str(e)
        out.append(rec)
        if sleep_sec:
            time.sleep(sleep_sec)
        if i % 50 == 0:
            print(f"  ...{i}/{len(events)} enriched")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2021-01-01", help="自社株買い取得 開始日")
    ap.add_argument("--until", default="2026-12-31", help="自社株買い取得 終了日")
    ap.add_argument("--events", help="検証用: CODE:DATE をカンマ区切り (Pro契約前のenrichテスト)")
    ap.add_argument("--out", type=Path, default=OUT_PATH, help="出力 JSON パス (既定 data/buyback_records.json)")
    args = ap.parse_args()

    if args.events:
        events = []
        for tok in args.events.split(","):
            code, _, d = tok.partition(":")
            events.append({"code": code.strip(), "event_date": d.strip(),
                           "event_type": "share_buyback", "source": "manual", "attrs": {}})
        print(f"[manual] {len(events)} 件で price+減益 enrich をテスト")
    else:
        raw = fetch_buyback_events(args.since, args.until)
        events = [r for r in (buyback_to_record(x) for x in raw) if r]
        print(f"[buyback] レコード化 {len(events)}件")

    records = build(events)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, {"records": records, "count": len(records)}, indent=1)
    print(f"wrote {args.out} ({len(records)} records)")


if __name__ == "__main__":
    main()

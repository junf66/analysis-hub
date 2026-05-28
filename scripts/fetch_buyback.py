"""自社株買い (TDnet 適時開示) を取得し、価格 (gap/分足 9:10〜11:30) と減益% を付与する。

データ源 (すべて契約済アクセスで取得可):
- 自社株買い決定: J-Quants TDnet アドオン `/td/list` を日次走査し
  DiscItems に 11105 (自己株式の取得) を含み Title に「決定」を含む開示を抽出
  (取得状況/終了の報告は除外)。
- 価格 enrich  : enrich_price_kouaku.enrich_record を再利用 (日足+分足、検証済)
- 減益%       : /fins/summary の NxFNp(来期予想) vs NP(今期実績)

規模(%): 自社株買い開示は PDF のみで API に構造化されないため未取得。
disc_no を保持し、後段で PDF パース or 手動付与する想定。

出力: data/buyback_records.json (共通スキーマ: code / event_date / attrs)

検証用: `--events CODE:YYYY-MM-DD,...` で /td/list を介さず price+減益 enrich を試せる。
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json
from scripts.enrich_price_kouaku import enrich_record

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data" / "buyback_records.json"

BUYBACK_DISC_ITEM = "11105"  # TDnet カテゴリコード: 自己株式の取得


def _f(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def is_buyback_decision(row: dict[str, Any]) -> bool:
    """/td/list の1行が「自己株式の取得に係る事項の決定」開示かを判定。

    DiscItems に 11105 を含み Title に「決定」を含む (取得状況/終了の報告は除外)。
    """
    items = row.get("DiscItems") or []
    title = row.get("Title") or ""
    return (BUYBACK_DISC_ITEM in items and "決定" in title
            and "状況" not in title and "終了" not in title)


def fetch_buyback_events(date_from: str, date_to: str) -> list[dict[str, Any]]:
    """TDnet `/td/list` を日次走査し自社株買い決定開示を抽出 (要 TDnet アドオン)。"""
    d0, d1 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    out: list[dict[str, Any]] = []
    schema_printed = False
    d = d0
    while d <= d1:
        try:
            rows = _jquants.get_list("/td/list", date=d.isoformat())
        except _jquants.JQuantsError:
            rows = []
        for r in rows:
            if is_buyback_decision(r):
                if not schema_printed:
                    print(f"[buyback] /td/list keys = {sorted(r.keys())}")
                    schema_printed = True
                out.append(r)
        d += timedelta(days=1)
    print(f"[buyback] 自社株買い決定 {len(out)}件 ({date_from}〜{date_to})")
    return out


def buyback_to_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    """/td/list の自社株買い開示行を共通スキーマ record に変換 (code を4桁化)。"""
    code = str(raw.get("Code") or "")
    disc_date = raw.get("DiscDate")
    if not code or not disc_date:
        return None
    code4 = code[:4] if len(code) == 5 and code.endswith("0") else code
    return {
        "code": code4,
        "event_date": str(disc_date)[:10],
        "event_type": "share_buyback_decision",
        "source": "jquants_td_list",
        "attrs": {
            "disc_no": raw.get("DiscNo"),
            "disc_time": raw.get("DiscTime"),
            "title": raw.get("Title"),
            "disc_items": raw.get("DiscItems"),
            "docs": raw.get("Docs"),
        },
    }


def attach_earnings(rec: dict[str, Any]) -> dict[str, Any]:
    """/fins/summary から event_date 時点の減益% (来期予想NP vs 今期実績NP) を付与。"""
    code = rec["code"]
    code5 = code + "0" if len(code) == 4 else code
    try:
        rows = _jquants.get_list("/fins/summary", code=code5)
    except _jquants.JQuantsError as e:
        rec["attrs"]["earnings_error"] = str(e)
        return rec
    ev = rec["event_date"]
    cands = [r for r in rows if (r.get("DiscDate") or "") <= ev]
    if not cands:
        rec["attrs"]["earnings_error"] = "no statement on/before event"
        return rec
    s = max(cands, key=lambda r: r.get("DiscDate") or "")
    np_act = _f(s.get("NP"))
    np_nxf = _f(s.get("NxFNp"))
    decline = (np_nxf - np_act) / np_act * 100.0 if (np_nxf is not None and np_act not in (None, 0)) else None
    rec["attrs"].update({
        "earn_disc_date": s.get("DiscDate"),
        "earn_doctype": s.get("DocType"),
        "np_actual": np_act,
        "np_forecast_next": np_nxf,
        "np_forecast_cur": _f(s.get("FNP")),
        "forecast_decline_pct": decline,
        "shares_out": _f(s.get("ShOutFY")),
    })
    return rec


def _is_enriched(rec: dict[str, Any]) -> bool:
    a = rec.get("attrs") or {}
    return a.get("next_open") is not None or a.get("price_error") is not None


def _load_done(out_path: Path | None) -> dict[tuple, dict[str, Any]]:
    """既存 out_path から enrich 済 record を (code,event_date) -> record で返す (resume用)。"""
    if not out_path or not Path(out_path).exists():
        return {}
    try:
        recs = json.loads(Path(out_path).read_text()).get("records", [])
    except (json.JSONDecodeError, OSError):
        return {}
    return {(r.get("code"), r.get("event_date")): r for r in recs if _is_enriched(r)}


def build(events: list[dict[str, Any]], *, out_path: Path | None = None,
          sleep_sec: float = 0.2, checkpoint_every: int = 100) -> list[dict[str, Any]]:
    """events 各件に price (gap/分足) + 減益% を付与した records を返す。

    out_path 指定時: checkpoint_every 件ごとに途中結果を保存 (クラッシュ耐性) し、
    既に enrich 済の (code,event_date) は再利用してスキップ (resume)。
    """
    import time
    done = _load_done(out_path)
    if done:
        print(f"[resume] enrich 済 {len(done)} 件をスキップ")
    out: list[dict[str, Any]] = []
    new_done = 0
    for i, rec in enumerate(events, 1):
        cached = done.get((rec.get("code"), rec.get("event_date")))
        if cached is not None:
            out.append(cached)
            continue
        try:
            enrich_record(rec)
            attach_earnings(rec)
        except _jquants.JQuantsError as e:
            rec["attrs"]["price_error"] = str(e)
        out.append(rec)
        new_done += 1
        if sleep_sec:
            time.sleep(sleep_sec)
        if out_path and new_done % checkpoint_every == 0:
            atomic_write_json(out_path, {"records": out, "count": len(out), "partial": True}, indent=1)
            print(f"  ...{i}/{len(events)} enriched (checkpoint, new={new_done})")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2021-01-01", help="自社株買い取得 開始日")
    ap.add_argument("--until", default="2026-12-31", help="自社株買い取得 終了日")
    ap.add_argument("--events", help="検証用: CODE:DATE をカンマ区切り (td/list を介さず enrich テスト)")
    ap.add_argument("--out", type=Path, default=OUT_PATH, help="出力 JSON パス (既定 data/buyback_records.json)")
    args = ap.parse_args()

    if args.events:
        events = [{"code": c.strip(), "event_date": d.strip(), "event_type": "share_buyback_decision",
                   "source": "manual", "attrs": {}}
                  for c, _, d in (tok.partition(":") for tok in args.events.split(","))]
        print(f"[manual] {len(events)} 件で price+減益 enrich をテスト")
    else:
        raw = fetch_buyback_events(args.since, args.until)
        events = [r for r in (buyback_to_record(x) for x in raw) if r]
        print(f"[buyback] レコード化 {len(events)}件")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    records = build(events, out_path=args.out)
    atomic_write_json(args.out, {"records": records, "count": len(records)}, indent=1)
    print(f"wrote {args.out} ({len(records)} records)")


if __name__ == "__main__":
    main()

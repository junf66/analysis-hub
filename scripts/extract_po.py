"""po-tracker (cache/po/po_records.json) を共通スキーマに展開する。

1 PO レコードにつき最大 3 イベント (announce/decide/deliver) を発行する。
価格は po-tracker 側で既に enrich 済 (J-Quants の追加 fetch は不要)。
ステージ別に kouaku 互換命名 (prev_close / next_open / gap_pct / next_day_*_ret) で
attrs に正規化して書き出すことで、analyze / backtest を kouaku と同じ
インターフェイスで扱える。

出力:
  data/po_records.json
    {schema_version, source, last_updated, count, records: [...]}

各 record:
  id            : "po:<ref_id>:<stage>"
  code          : 4 桁文字列
  event_date    : ISO date (該当ステージの日付)
  event_type    : "po_announce" | "po_decide" | "po_deliver"
  source        : "po-tracker"
  ref_id        : po-tracker 側 PO ID (同 PO の 3 イベントで共有)
  stage         : "announce" | "decide" | "deliver"
  po_type       : "普通" | "リート"
  lending_type  : "貸借" | "信用" | "" (売禁等)
  legacy_record : bool
  concurrent_earnings  : bool (決算同時、EV 評価から除外推奨)
  split_within_po_window : bool (株式分割窓 = 価格調整懸念)
  stale_incomplete : bool
  attrs         : ステージ別に正規化された価格 + 原本フィールド (_raw)
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_PATH = REPO_ROOT / "cache" / "po" / "po_records.json"
OUT_PATH = REPO_ROOT / "data" / "po_records.json"

SCHEMA_VERSION = "po.v1"
SOURCE = "po-tracker"

# ステージ → (event_type, event_date_field)
_STAGES: list[tuple[str, str, str]] = [
    ("announce", "po_announce", "announce_date"),
    ("decide", "po_decide", "decision_date"),
    ("deliver", "po_deliver", "delivery_date"),
]


def _code4(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    return s.zfill(4) if s.isdigit() and len(s) <= 4 else s


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- ステージ別 attrs 正規化 -------------------------------------------------
#
# kouaku 側の attrs 命名規則 (prev_close / next_open / gap_pct /
# next_day_open_to_close_ret / next_day_905_ret ... / next_day_morning_ret) に
# 寄せる。価格欠損時は None を保持。
#
# announce ステージ : 発表翌日エッジ (普通株、翌寄り→9:10 等で利確)
#   prev_close      = announce_day_close
#   next_open       = next_open
#   gap_pct         = next_day_gu_pct
#   next_day_905_ret 〜 next_day_morning_ret は po-tracker のそのまま
#
# decide ステージ : 決定日エッジ (リート短、翌寄り→決定日引け買戻)
#   ref_open        = dec_open (決定日寄り) ← "next_open" 相当
#   ref_close       = dec_close (決定日引け)
#   ret_open        = next_open→dec_open リターン (% / po-tracker fielda)
#   ret_close       = next_open→dec_close リターン (% / po-tracker)
#
# deliver ステージ : 受渡日エッジ (普通株、受渡日 GD → 寄り→引け)
#   prev_close      = prev_close_before_delivery
#   next_open       = delivery_open
#   next_close      = delivery_close
#   gap_pct         = delivery_gap_pct
#   next_day_open_to_close_ret = delivery_ret


def _attrs_announce(raw: dict[str, Any]) -> dict[str, Any]:
    a: dict[str, Any] = {
        "prev_close": _f(raw.get("announce_day_close")),
        "next_open": _f(raw.get("next_open")),
        "gap_pct": _f(raw.get("next_day_gu_pct")),
    }
    for k in (
        "next_day_905_ret",
        "next_day_910_ret",
        "next_day_915_ret",
        "next_day_930_ret",
        "next_day_1000_ret",
        "next_day_morning_ret",
    ):
        v = _f(raw.get(k))
        if v is not None:
            a[k] = v
    # max_price / open_to_max は寄り後の最高値到達 = ロング側天井参考
    if raw.get("max_price") is not None:
        a["next_day_high"] = _f(raw.get("max_price"))
    if raw.get("open_to_max") is not None:
        a["next_day_open_to_high_ret"] = _f(raw.get("open_to_max"))
    return a


def _attrs_decide(raw: dict[str, Any]) -> dict[str, Any]:
    """決定日ステージ: next_open (=announce 翌寄り) を基準とした決定日価格。"""
    return {
        "ref_open": _f(raw.get("next_open")),  # = announce 翌寄り = decide 戦略のエントリー
        "dec_open": _f(raw.get("dec_open")),
        "dec_close": _f(raw.get("dec_close")),
        "ret_open": _f(raw.get("ret_open")),    # next_open → dec_open
        "ret_close": _f(raw.get("ret_close")),  # next_open → dec_close
    }


def _attrs_deliver(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "prev_close": _f(raw.get("prev_close_before_delivery")),
        "next_open": _f(raw.get("delivery_open")),
        "next_close": _f(raw.get("delivery_close")),
        "gap_pct": _f(raw.get("delivery_gap_pct")),
        "next_day_open_to_close_ret": _f(raw.get("delivery_ret")),
        "issue_price": _f(raw.get("issue_price")),
        "discount_rate": _f(raw.get("discount_rate")),
    }


_STAGE_ATTRS_FN = {
    "announce": _attrs_announce,
    "decide": _attrs_decide,
    "deliver": _attrs_deliver,
}


# ---- メイン展開 -------------------------------------------------------------

def expand_record(raw: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """1 PO レコードを最大 3 イベントに展開する。"""
    code = _code4(raw.get("code"))
    if code is None:
        return
    ref_id = raw.get("id")
    if not ref_id:
        return
    po_type = raw.get("type")
    lending_type = raw.get("lending_type") or ""

    for stage, event_type, date_field in _STAGES:
        event_date = raw.get(date_field)
        if not event_date:
            continue
        # date 文字列の頭 10 桁だけ採用 (タイムスタンプ混入耐性)
        event_date = str(event_date)[:10]
        attrs = dict(_STAGE_ATTRS_FN[stage](raw))
        attrs["_raw_keys"] = sorted(raw.keys())  # トレース用
        yield {
            "id": f"po:{ref_id}:{stage}",
            "code": code,
            "event_date": event_date,
            "event_type": event_type,
            "source": SOURCE,
            "ref_id": str(ref_id),
            "stage": stage,
            "po_type": po_type,
            "lending_type": lending_type,
            "legacy_record": bool(raw.get("legacy")),
            "concurrent_earnings": bool(raw.get("concurrent_earnings")),
            "split_within_po_window": bool(raw.get("split_within_po_window")),
            "stale_incomplete": bool(raw.get("stale_incomplete")),
            "name": raw.get("name"),
            "year": raw.get("year"),
            "market": raw.get("market"),
            "sector17": raw.get("sector17"),
            "sector33": raw.get("sector33"),
            "po_scale": _f(raw.get("po_scale")),
            "market_cap": _f(raw.get("market_cap")),
            "po_pct": _f(raw.get("po_pct")),
            "dilution": _f(raw.get("dilution")),
            "status": raw.get("status"),
            "attrs": attrs,
        }


def expand_all(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """全 PO 生レコードを共通スキーマ events に展開して結合する。"""
    out: list[dict[str, Any]] = []
    for r in records:
        out.extend(expand_record(r))
    return out


def drop_reason(raw: dict[str, Any]) -> str | None:
    """raw PO が 0 event になる理由を返す (1 件以上展開できるなら None)。

    無言ドロップを避けるための分類。no_stage_date は全ステージ日付欠損
    (= announce/decision/delivery すべて未確定) のため展開できないケース。
    """
    if _code4(raw.get("code")) is None:
        return "no_code"
    if not raw.get("id"):
        return "no_id"
    if not any(raw.get(date_field) for _, _, date_field in _STAGES):
        return "no_stage_date"
    return None


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"raw PO cache not found: {path}. Run `python -m fetchers.po` first."
        )
    return json.loads(path.read_text())


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--raw", type=Path, default=RAW_PATH, help="po-tracker 生 JSON のパス"
    )
    ap.add_argument(
        "--out", type=Path, default=OUT_PATH, help="共通スキーマ出力先"
    )
    ap.add_argument("--force", action="store_true", help="既存より激減しても上書き (安全ガード無効化)")
    args = ap.parse_args()

    raw = _load_raw(args.raw)
    records = raw.get("records", [])
    events = expand_all(records)
    dropped = Counter(reason for r in records if (reason := drop_reason(r)) is not None)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "raw_last_updated": raw.get("last_updated"),
        "count_raw": len(records),
        "count": len(events),
        "count_dropped": sum(dropped.values()),
        "dropped_reasons": dict(dropped),
        "stage_counts": dict(Counter(e["stage"] for e in events)),
        "type_counts": dict(Counter(e["po_type"] for e in events)),
        "records": events,
    }

    from scripts._atomic import atomic_write_records

    atomic_write_records(args.out, payload, force=args.force)
    print(f"extracted {len(records)} PO → {len(events)} events (dropped {payload['count_dropped']})")
    if payload["dropped_reasons"]:
        print(f"  dropped_reasons: {payload['dropped_reasons']}")
    print(f"  stage_counts: {payload['stage_counts']}")
    print(f"  type_counts:  {payload['type_counts']}")
    print(f"  saved → {args.out}")


if __name__ == "__main__":
    main()

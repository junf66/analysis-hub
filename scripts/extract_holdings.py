"""holdings-tracker 生データ (cache/holdings/holdings.json) を共通スキーマに展開する。

holdings 側で価格が既に enrich 済 (open_to_close_pct / gap_pct / open_to_0910_pct ...) の
ため、J-Quants の追加 fetch は不要。PO と同様、価格フィールドを kouaku 互換命名
(prev_close / next_open / gap_pct / next_day_open_to_close_ret / next_day_910_ret ...) に
正規化して attrs に格納し、analyze / backtest を kouaku・PO と同じインターフェイスで扱える。

partition 用の属性 (holder_category / purpose_category / gap_label / holding_ratio ...) は
event 直下に持たせる。

出力:
  data/holdings_records.json
    {schema_version, source, last_updated, count, *_counts, records: [...]}

各 record:
  id            : "holdings:<raw id>"
  code          : 4 桁文字列
  event_date    : ISO date (提出日)
  event_type    : "holdings_filing" 等
  source        : "edinet"
  ref_id        : holdings-tracker 側 id
  holder_category / holder_category_jp   : 保有者区分 (外資ファンド/国内ファンド/その他 ...)
  purpose_category / purpose_category_jp : 保有目的 (純投資/取引関係 ...)
  holding_ratio / previous_ratio / ratio_change : 保有割合 (%)
  filer_freq_180d : 直近 180 日の同一提出者頻度
  has_joint_holders : 共同保有フラグ
  gap_label       : GU / GD / flat 等 (holdings 側ラベル)
  market / sector_17_name / market_cap_oku / turnover_oku / pbr / per / volume_ratio
  low_ratio_suspect : 品質フラグ (EV 評価から除外)
  attrs           : kouaku 互換の価格メトリクス
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_PATH = REPO_ROOT / "cache" / "holdings" / "holdings.json"
OUT_PATH = REPO_ROOT / "data" / "holdings_records.json"

SCHEMA_VERSION = "holdings.v1"
SOURCE = "edinet"

_VALID_EVENT_TYPES = {
    "holdings_filing",
    "holdings_change",
    "holdings_correction",
    "holdings_filing_correction",
    "holdings_change_correction",
}

# holdings 生 price フィールド → kouaku 互換 attrs 名
_PRICE_MAP: list[tuple[str, str]] = [
    ("prev_close", "prev_close"),
    ("open_price", "next_open"),
    ("close_price", "next_close"),
    ("day_high", "next_high"),
    ("day_low", "next_low"),
    ("gap_pct", "gap_pct"),
    ("open_to_close_pct", "next_day_open_to_close_ret"),
    ("open_to_high_pct", "next_day_open_to_high_ret"),
    ("open_to_low_pct", "next_day_open_to_low_ret"),
    ("open_to_0905_pct", "next_day_905_ret"),
    ("open_to_0910_pct", "next_day_910_ret"),
    ("open_to_0915_pct", "next_day_915_ret"),
    ("open_to_0930_pct", "next_day_930_ret"),
    ("open_to_1000_pct", "next_day_1000_ret"),
    ("open_to_1130_pct", "next_day_morning_ret"),
    ("high_to_close_pct", "next_day_high_to_close_ret"),
    ("d5_pct", "d5_ret"),
    ("d10_pct", "d10_ret"),
]

# partition / フィルタ用に event 直下へ持ち上げる属性
_DIM_KEYS = [
    "holder_category", "holder_category_jp",
    "purpose_category", "purpose_category_jp",
    "gap_label", "market", "sector_17_name",
    "doc_type_code", "doc_description", "filer_name",
]
_FLOAT_DIM_KEYS = [
    "holding_ratio", "previous_ratio", "ratio_change",
    "market_cap_oku", "turnover_oku", "pbr", "per", "volume_ratio",
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


def _attrs(raw: dict[str, Any]) -> dict[str, Any]:
    a: dict[str, Any] = {}
    for src, dst in _PRICE_MAP:
        v = _f(raw.get(src))
        if v is not None:
            a[dst] = v
    return a


def drop_reason(raw: dict[str, Any]) -> str | None:
    """raw を共通スキーマ化できない理由を返す (展開可能なら None)。

    無言ドロップを避けるため、除外理由を分類可能にする。
    """
    if not _code4(raw.get("code")):
        return "no_code"  # 銘柄コード未解決 (非上場/特定不能) → 株価不能で分析対象外
    if not raw.get("event_date"):
        return "no_event_date"
    et = raw.get("event_type")
    if not et:
        return "no_event_type"
    if not raw.get("id"):
        return "no_id"
    if et not in _VALID_EVENT_TYPES:
        return f"bad_event_type:{et}"
    return None


def expand_record(raw: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """1 holdings 生レコード → 共通スキーマ event 1 件 (不適格は yield しない)。"""
    if drop_reason(raw) is not None:
        return
    code = _code4(raw.get("code"))
    event_date = raw.get("event_date")
    event_type = raw.get("event_type")
    ref_id = raw.get("id")

    event: dict[str, Any] = {
        "id": f"holdings:{ref_id}",
        "code": code,
        "event_date": str(event_date)[:10],
        "event_type": event_type,
        "source": raw.get("source") or SOURCE,
        "ref_id": str(ref_id),
        "has_joint_holders": bool(raw.get("has_joint_holders")),
        "filer_freq_180d": raw.get("filer_freq_180d"),
        "low_ratio_suspect": bool(raw.get("low_ratio_suspect")),
        "next_business_day": raw.get("next_business_day"),
        "attrs": _attrs(raw),
    }
    for k in _DIM_KEYS:
        event[k] = raw.get(k)
    for k in _FLOAT_DIM_KEYS:
        event[k] = _f(raw.get(k))
    yield event


def expand_all(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """全 holdings 生レコードを共通スキーマ events 配列に展開して結合する。"""
    out: list[dict[str, Any]] = []
    for r in records:
        out.extend(expand_record(r))
    return out


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"raw holdings cache not found: {path}. Run `python -m fetchers.holdings` first."
        )
    return json.loads(path.read_text())


def build_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """holdings 生 payload → 共通スキーマ payload (メタ + 分布カウント + events)。"""
    records = raw.get("records", [])
    events = expand_all(records)
    dropped = Counter(reason for r in records if (reason := drop_reason(r)) is not None)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "raw_last_updated": raw.get("last_updated"),
        "count_raw": len(records),
        "count": len(events),
        "count_dropped": sum(dropped.values()),
        "dropped_reasons": dict(dropped),
        "event_type_counts": dict(Counter(e["event_type"] for e in events)),
        "purpose_counts": dict(Counter(e.get("purpose_category_jp") for e in events)),
        "holder_counts": dict(Counter(e.get("holder_category_jp") for e in events)),
        "records": events,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=RAW_PATH, help="holdings 生 JSON のパス")
    ap.add_argument("--out", type=Path, default=OUT_PATH, help="共通スキーマ出力先")
    ap.add_argument("--force", action="store_true", help="既存より激減しても上書き (安全ガード無効化)")
    args = ap.parse_args()

    raw = _load_raw(args.raw)
    payload = build_payload(raw)

    from scripts._atomic import atomic_write_records

    atomic_write_records(args.out, payload, force=args.force)
    print(f"extracted {payload['count_raw']} holdings → {payload['count']} events "
          f"(dropped {payload['count_dropped']})")
    if payload["dropped_reasons"]:
        print(f"  dropped_reasons: {payload['dropped_reasons']}")
    print(f"  purpose_counts: {payload['purpose_counts']}")
    print(f"  holder_counts:  {payload['holder_counts']}")
    print(f"  saved → {args.out}")


if __name__ == "__main__":
    main()

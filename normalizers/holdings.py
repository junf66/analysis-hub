"""holdings 生データ → 共通スキーマ変換。

holdings-tracker 側で既に canonical フィールド (id, code, event_date, event_type, source)
が約束されているため、本 normalizer はパススルー + attrs 格納のみ。
"""
from __future__ import annotations

from typing import Any, Iterable, Iterator

SOURCE = "edinet"

_VALID_EVENT_TYPES = {
    "holdings_filing",
    "holdings_change",
    "holdings_correction",
    "holdings_filing_correction",
    "holdings_change_correction",
}


def normalize_record(record: dict[str, Any]) -> Iterator[dict[str, Any]]:
    code = record.get("code")
    event_date = record.get("event_date")
    event_type = record.get("event_type")
    rec_id = record.get("id")
    if not code or not event_date or not event_type or not rec_id:
        return
    if event_type not in _VALID_EVENT_TYPES:
        return
    yield {
        "id": f"holdings:{rec_id}",
        "code": str(code),
        "event_date": event_date,
        "event_type": event_type,
        "source": record.get("source") or SOURCE,
        "ref_id": rec_id,
        "attrs": record,
    }


def normalize(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in records:
        out.extend(normalize_record(r))
    return out

"""kouaku_mixed 生レコード → 共通スキーマ変換 (パススルー)。

extract_mixed_disclosures.py が既に共通スキーマ準拠の dict を出力しているため、
本 normalizer はバリデーションと attrs 詰め直しのみ。
"""
from __future__ import annotations

from typing import Any, Iterable, Iterator

SOURCE = "tdnet+fins"

_VALID_EVENT_TYPES = {"kouaku_mixed"}


def normalize_record(record: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """1 kouaku_mixed record を共通スキーマ event 1 件として yield する。"""
    code = record.get("code")
    event_date = record.get("event_date")
    event_type = record.get("event_type")
    rec_id = record.get("id")
    if not code or not event_date or not event_type or not rec_id:
        return
    if event_type not in _VALID_EVENT_TYPES:
        return
    yield {
        "id": rec_id,
        "code": str(code),
        "event_date": event_date,
        "event_type": event_type,
        "source": record.get("source") or SOURCE,
        "ref_id": record.get("ref_id") or rec_id,
        "attrs": {
            "subpattern": record.get("subpattern"),
            "good_factors": record.get("good_factors", []),
            "bad_factors": record.get("bad_factors", []),
            **(record.get("attrs") or {}),
        },
    }


def normalize(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """全 kouaku records を共通スキーマ events 配列に展開して結合する。"""
    out: list[dict[str, Any]] = []
    for r in records:
        out.extend(normalize_record(r))
    return out

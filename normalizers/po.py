"""PO 生データ → 共通スキーマ変換。

共通スキーマ:
  id          : str   (源泉プレフィックス付き、例 "po:csv_1:announce")
  code        : str   (4桁文字列。先頭ゼロ保持)
  event_date  : str   (ISO date, "YYYY-MM-DD"。欠損は None)
  event_type  : str   ("po_announce" | "po_decide" | "po_deliver")
  source      : str   ("po-tracker")

  ref_id      : str   (PO 単位の識別子。同一 PO の announce/decide/deliver で共有)
  attrs       : dict  (元レコードの全フィールド)

「銘柄コード × 日付」で横断結合できるよう、1つの PO につき最大 3 イベント
(announce/decide/deliver) を発行する。
"""
from __future__ import annotations

from typing import Any, Iterable, Iterator

SOURCE = "po-tracker"

_EVENT_TO_DATE_FIELD = {
    "po_announce": "announce_date",
    "po_decide": "decision_date",
    "po_deliver": "delivery_date",
}


def _ensure_code(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # 4桁ゼロパディング (5桁は REIT 等あり得るのでそのまま)
    return s.zfill(4) if s.isdigit() and len(s) <= 4 else s


def normalize_record(record: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """1 PO レコードを最大 3 イベントに展開する。"""
    code = _ensure_code(record.get("code"))
    if code is None:
        return
    ref_id = record.get("id")
    if ref_id is None:
        return

    for event_type, date_field in _EVENT_TO_DATE_FIELD.items():
        event_date = record.get(date_field)
        if not event_date:
            continue
        yield {
            "id": f"po:{ref_id}:{event_type.split('_', 1)[1]}",
            "code": code,
            "event_date": event_date,
            "event_type": event_type,
            "source": SOURCE,
            "ref_id": ref_id,
            "attrs": record,
        }


def normalize(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in records:
        out.extend(normalize_record(r))
    return out

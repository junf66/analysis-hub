"""DiscTime バケット分類と関連ユーティリティ。

複数の script で同じロジックを書き直して名前がずれるのを防ぐため集約。
境界定義は docs/SCHEMA.md と一致させる。
"""
from __future__ import annotations

from typing import Any

# 表示順 (大引け後を先に、ノイジーな unknown を末尾)
BUCKET_ORDER = ["大引け後", "引け間際", "場中", "寄り中", "寄前", "unknown"]


def disc_bucket_from_time(t: str | None) -> str:
    """HH:MM:SS 文字列 → bucket 名。

    境界:
      "00:00:00" - "08:59:59" → 寄前
      "09:00:00" - "10:59:59" → 寄り中
      "11:00:00" - "14:59:59" → 場中
      "15:00:00" - "15:29:59" → 引け間際
      "15:30:00" -            → 大引け後
    """
    if not t:
        return "unknown"
    h = t[:2]
    if h < "09":
        return "寄前"
    if h < "11":
        return "寄り中"
    if h < "15":
        return "場中"
    if h == "15" and t < "15:30":
        return "引け間際"
    return "大引け後"


def disc_bucket(rec: dict[str, Any]) -> str:
    """kouaku_mixed レコード → 最も早い disc_time から bucket を決定。

    複数の good/bad factor がある場合は最早を採用 (= 一番先に出た開示)。
    """
    times = [
        f.get("disc_time")
        for f in rec.get("good_factors", []) + rec.get("bad_factors", [])
        if f.get("disc_time")
    ]
    if not times:
        return "unknown"
    return disc_bucket_from_time(min(times))

"""イベント日時点(point-in-time)の銘柄属性を引くヘルパ。

cache/master_history.json (年次スナップショット, fetch_master_history.py 生成) を読み、
(code, date) に対して **その日以前で最も新しいスナップショット** の属性を返す。
単一スナップショットの遡及適用(誤分類)＋上場廃止銘柄の脱落(生存バイアス)を解消する。

過去日マスタ未取得(cache無し)の場合は available() が False を返すので、呼び出し側は
従来の単一スナップショットにフォールバックできる。
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = REPO_ROOT / "cache" / "master_history.json"


class PitMaster:
    """年次マスタ履歴を読み、イベント日時点の属性を返す。"""

    def __init__(self) -> None:
        raw = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
        self._dates = sorted(raw)                 # snapshot 日付 (昇順)
        self._snap = raw                          # {date: {code5: attrs}}

    def available(self) -> bool:
        return bool(self._dates)

    @staticmethod
    def _c5(code: str) -> str:
        code = str(code)
        return code + "0" if len(code) == 4 else code

    def _snapshot_for(self, date: str) -> dict:
        """date 以前で最新のスナップショット。date が最古より前なら最古を使う。"""
        chosen = self._dates[0]
        for d in self._dates:
            if d <= date:
                chosen = d
            else:
                break
        return self._snap[chosen]

    def attrs(self, code: str, date: str) -> dict:
        """イベント日時点の {scale_band,S17Nm,MrgnNm,ScaleCat} (無ければ {})。"""
        if not self._dates or not date:
            return {}
        return self._snapshot_for(date).get(self._c5(code), {})

    def scale_band(self, code: str, date: str) -> str | None:
        return self.attrs(code, date).get("scale_band")

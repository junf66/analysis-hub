"""kouaku_mixed のローカル成果物ロード fetcher。

他の fetcher と違いリモート取得はしない (生データ取得は scripts/fetch_disclosures.py
が J-Quants API を叩く)。本モジュールは extract_mixed_disclosures.py が出力した
data/kouaku_records.json をロードして timeline 等に供給する。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDS_PATH = REPO_ROOT / "data" / "kouaku_records.json"


def load_cached(path: Path | None = None) -> dict[str, Any]:
    """data/kouaku_records.json を読み込んで返す。存在しなければ FileNotFoundError。"""
    p = path or RECORDS_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found. Run scripts/extract_mixed_disclosures.py first."
        )
    return json.loads(p.read_text())

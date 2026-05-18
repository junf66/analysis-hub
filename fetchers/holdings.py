"""holdings-tracker (https://github.com/junf66/stocks-Large-holding-report) からデータを取得する fetcher。

このハブからは fetch のみ。生データは cache/holdings/ に保存し、normalizer 以降が利用する。
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HOLDINGS_URL = (
    "https://raw.githubusercontent.com/junf66/stocks-Large-holding-report/main/data/holdings.json"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "cache" / "holdings"


@dataclass
class FetchResult:
    records_path: Path
    count: int
    last_updated: str
    sample: bool
    event_type_counts: dict[str, int]


def _download(url: str, dest: Path) -> bytes:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "analysis-hub/fetchers/holdings.py"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    dest.write_bytes(data)
    return data


def fetch(cache_dir: Path | None = None) -> FetchResult:
    """holdings.json をダウンロードしてキャッシュする。"""
    target_dir = cache_dir or CACHE_DIR
    records_path = target_dir / "holdings.json"
    raw = _download(HOLDINGS_URL, records_path)
    payload = json.loads(raw)
    return FetchResult(
        records_path=records_path,
        count=payload.get("record_count", len(payload.get("records", []))),
        last_updated=payload.get("last_updated", ""),
        sample=bool(payload.get("sample", False)),
        event_type_counts=dict(payload.get("event_type_counts", {})),
    )


def load_cached(cache_dir: Path | None = None) -> dict[str, Any]:
    """キャッシュ済の holdings JSON を読み込んで返す。未キャッシュなら例外。"""
    target_dir = cache_dir or CACHE_DIR
    records_path = target_dir / "holdings.json"
    if not records_path.exists():
        raise FileNotFoundError(f"cache miss: {records_path} (run fetch() first)")
    return json.loads(records_path.read_text())


if __name__ == "__main__":
    result = fetch()
    print(f"fetched {result.count} records (last_updated={result.last_updated}, sample={result.sample})")
    print(f"  event_type_counts: {result.event_type_counts}")
    print(f"  records: {result.records_path}")

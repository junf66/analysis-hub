"""po-tracker (https://github.com/junf66/po-tracker) からデータを取得する fetcher。

このハブからは fetch のみ。生データは cache/po/ に保存し、normalizer 以降が利用する。
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PO_RECORDS_URL = "https://raw.githubusercontent.com/junf66/po-tracker/main/data/po_records.json"
PO_AUDIT_URL = "https://raw.githubusercontent.com/junf66/po-tracker/main/data/po_audit.json"

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "cache" / "po"


@dataclass
class FetchResult:
    records_path: Path
    audit_path: Path
    count: int
    last_updated: str


def _download(url: str, dest: Path) -> bytes:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "analysis-hub/fetchers/po.py"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    dest.write_bytes(data)
    return data


def fetch(cache_dir: Path | None = None) -> FetchResult:
    """po-tracker の records / audit JSON をダウンロードしてキャッシュする。"""
    target_dir = cache_dir or CACHE_DIR
    records_path = target_dir / "po_records.json"
    audit_path = target_dir / "po_audit.json"

    records_bytes = _download(PO_RECORDS_URL, records_path)
    _download(PO_AUDIT_URL, audit_path)

    payload = json.loads(records_bytes)
    return FetchResult(
        records_path=records_path,
        audit_path=audit_path,
        count=payload.get("count", len(payload.get("records", []))),
        last_updated=payload.get("last_updated", ""),
    )


def load_cached(cache_dir: Path | None = None) -> dict[str, Any]:
    """キャッシュ済の records JSON を読み込んで返す。未キャッシュなら例外。"""
    target_dir = cache_dir or CACHE_DIR
    records_path = target_dir / "po_records.json"
    if not records_path.exists():
        raise FileNotFoundError(f"cache miss: {records_path} (run fetch() first)")
    return json.loads(records_path.read_text())


if __name__ == "__main__":
    result = fetch()
    print(f"fetched {result.count} records (last_updated={result.last_updated})")
    print(f"  records: {result.records_path}")
    print(f"  audit:   {result.audit_path}")

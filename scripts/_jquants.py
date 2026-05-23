"""J-Quants API v2 シンクライアント。

v1 廃止 (2025-12-22) 後、認証は `x-api-key` ヘッダ方式に統一。
- 通常エンドポイント : https://api.jquants.com/v2
- Pro エンドポイント : https://api.jquants-pro.com/v2 (TDnet/自社株買い等)

`JQUANTS_API_KEY` 環境変数からキーを取得する。
ページングは pagination_key を再帰追跡する。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

BASE_URL = "https://api.jquants.com/v2"
PRO_BASE_URL = "https://api.jquants-pro.com/v2"


class JQuantsError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("JQUANTS_API_KEY")
    if not key:
        raise JQuantsError("JQUANTS_API_KEY env var is not set")
    return key


def _get(url: str, *, retries: int = 3, backoff: float = 1.5) -> dict[str, Any]:
    headers = {"x-api-key": _api_key(), "User-Agent": "analysis-hub/scripts/_jquants.py"}
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read()[:400].decode("utf-8", errors="replace")
            if e.code in (429, 500, 502, 503, 504):
                last_err = JQuantsError(f"HTTP {e.code}: {body}")
                time.sleep(backoff * (2**attempt))
                continue
            raise JQuantsError(f"HTTP {e.code} {url}: {body}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(backoff * (2**attempt))
    raise JQuantsError(f"giving up after {retries} retries: {last_err}")


def get(path: str, *, base: str = BASE_URL, **params: Any) -> Iterator[dict[str, Any]]:
    """指定エンドポイントを叩き、`data` 配列を要素単位で yield する。

    `pagination_key` を自動追跡。レスポンスに data 以外のフィールドがあれば
    最初のページのみのデフォルト値として無視 (data 部分だけ取り出す)。
    """
    qs = {k: v for k, v in params.items() if v is not None}
    pagination_key: str | None = None
    while True:
        q = dict(qs)
        if pagination_key:
            q["pagination_key"] = pagination_key
        url = f"{base}{path}"
        if q:
            url += "?" + urllib.parse.urlencode(q)
        payload = _get(url)
        rows = payload.get("data") or []
        for row in rows:
            yield row
        pagination_key = payload.get("pagination_key")
        if not pagination_key:
            return


def get_list(path: str, *, base: str = BASE_URL, **params: Any) -> list[dict[str, Any]]:
    """get() の結果を list として一括取得 (ページネーション込み)。"""
    return list(get(path, base=base, **params))


def is_pro_available() -> bool:
    """Pro ホストへの疎通が可能か (allowlist と契約の双方を兼ねて確認)。"""
    try:
        next(get("/markets/share_buyback_tdnet", base=PRO_BASE_URL, date="20250122"))
        return True
    except StopIteration:
        return True  # data 空でもホスト疎通自体は成功
    except JQuantsError:
        return False

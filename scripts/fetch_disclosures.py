"""TDnet 適時開示および /fins/summary を 5 年分 fetch して cache/ に保存する。

主軸ソース:
  - J-Quants     `/fins/summary`                 ... 決算/業績修正/配当 (全銘柄分)
  - yanoshin     `/webapi/tdnet/list/{date}.json` ... TDnet 全タイトル (自社株買い / TOB / 優待 等)
  - J-Quants Pro `/markets/share_buyback_tdnet`  ... 自社株買い (Pro 契約者のみ)

実行:
  python -m scripts.fetch_disclosures           # 5年分本番
  python -m scripts.fetch_disclosures --probe   # 直近 14 日だけ取って疎通確認
  python -m scripts.fetch_disclosures --codes 7990,4246,5253  # 指定銘柄の /fins/summary だけ
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from scripts import _jquants

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "cache" / "disclosures"

# ---- TDnet (yanoshin) -----------------------------------------------------

TDNET_BASE_URL = "https://webapi.yanoshin.jp/webapi/tdnet/list"
TDNET_USER_AGENT = "analysis-hub/scripts/fetch_disclosures.py"


class TdnetFetchError(RuntimeError):
    pass


def _trading_days(since: date, until: date) -> list[date]:
    """営業日 (HolDiv=='1') と半休 ('2') のみ返す。

    J-Quants /markets/calendar の HolDiv:
      0 = 非営業日 (祝日), 1 = 営業日, 2 = 半休日 (大納会等), 3 = 非営業日 (週末)
    """
    rows = _jquants.get_list(
        "/markets/calendar",
        holidaydivision="1",
        **{"from": since.isoformat(), "to": until.isoformat()},
    )
    out: list[date] = []
    for r in rows:
        if r.get("HolDiv") in ("1", "2"):
            out.append(date.fromisoformat(r["Date"]))
    return sorted(out)


# ---- 自社株買い (Pro) -----------------------------------------------------

def fetch_share_buyback_day(d: date) -> list[dict[str, Any]]:
    """ある営業日の自社株買い TDnet 開示。Pro endpoint。"""
    return _jquants.get_list(
        "/markets/share_buyback_tdnet",
        base=_jquants.PRO_BASE_URL,
        date=d.strftime("%Y%m%d"),
    )


def fetch_share_buyback_range(since: date, until: date, *, sleep_sec: float = 0.0) -> dict[str, list[dict[str, Any]]]:
    """期間内の自社株買い TDnet 開示を日別マップで返す。営業日のみ。"""
    import time as _time

    days = _trading_days(since, until)
    out: dict[str, list[dict[str, Any]]] = {}
    for i, d in enumerate(days, 1):
        try:
            out[d.isoformat()] = fetch_share_buyback_day(d)
        except _jquants.JQuantsError as e:
            print(f"  ! {d}: {e}")
            continue
        if sleep_sec:
            _time.sleep(sleep_sec)
        if i % 50 == 0:
            print(f"  ... {i}/{len(days)} days fetched")
    return out


# ---- /fins/summary (全銘柄) ----------------------------------------------

def fetch_fins_summary(*, code: str | None = None, since: date | None = None, until: date | None = None) -> list[dict[str, Any]]:
    """銘柄指定 or 日付範囲指定で /fins/summary を取得。

    /fins/summary は code 指定 / date 指定が可能。両方 None ならエラー。
    """
    params: dict[str, Any] = {}
    if code:
        params["code"] = code
    if since:
        params["from"] = since.isoformat()
    if until:
        params["to"] = until.isoformat()
    if not params:
        raise ValueError("fetch_fins_summary: pass code= or since/until=")
    return _jquants.get_list("/fins/summary", **params)


def fetch_fins_summary_range_by_date(
    since: date,
    until: date,
    *,
    sleep_sec: float = 0.15,
) -> dict[str, list[dict[str, Any]]]:
    """日次で /fins/summary を回収。営業日のみ。

    /fins/summary は `date` 単独パラメータも受ける (公式 v1 互換)。
    レート制限対策のため標準で 0.15s sleep を挟む。
    """
    import time as _time

    days = _trading_days(since, until)
    out: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for i, d in enumerate(days, 1):
        try:
            rows = _jquants.get_list("/fins/summary", date=d.isoformat())
        except _jquants.JQuantsError as e:
            print(f"  ! {d}: {e}")
            continue
        out[d.isoformat()] = rows
        total += len(rows)
        if sleep_sec:
            _time.sleep(sleep_sec)
        if i % 50 == 0:
            print(f"  ... fins/summary {i}/{len(days)} days, last={d}: total {total} rows")
    return out


# ---- TDnet (yanoshin) -----------------------------------------------------

def _normalize_tdnet_code(raw: Any) -> str:
    """yanoshin は 5 桁 (4桁証券コード + 末尾 0) を返すので 4 桁に正規化。

    数字以外 (外国株の ETF 等) はそのまま返す。
    """
    s = str(raw or "").strip()
    if len(s) == 5 and s.isdigit():
        return s[:-1]
    return s


def _normalize_tdnet_item(item: dict[str, Any]) -> dict[str, Any]:
    """yanoshin {Tdnet: {...}} ラッパを剥がして以下形式の dict を返す。

    keys: id / pubdate / code / company_name / title / document_url / markets
    pubdate は "YYYY-MM-DD HH:MM:SS" 文字列のまま (DiscTime 用に後段で分解)。
    """
    t = item.get("Tdnet") if isinstance(item, dict) else None
    if not isinstance(t, dict):
        return {}
    return {
        "id": t.get("id"),
        "pubdate": t.get("pubdate"),
        "code": _normalize_tdnet_code(t.get("company_code")),
        "company_name": t.get("company_name"),
        "title": t.get("title") or "",
        "document_url": t.get("document_url"),
        "markets": t.get("markets_string"),
    }


def fetch_tdnet_public_day(d: date, *, limit: int = 2000, timeout: float = 30.0, retries: int = 3) -> list[dict[str, Any]]:
    """yanoshin 経由で指定営業日の TDnet 適時開示一覧を取得。

    成功すれば `_normalize_tdnet_item` で正規化済の dict 配列を返す。
    pre 営業日チェックは呼び出し側責務 (この関数は休日でも空配列を返す可能性あり)。
    """
    import time as _time

    url = f"{TDNET_BASE_URL}/{d.strftime('%Y%m%d')}.json"
    if limit:
        url += f"?limit={int(limit)}"
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": TDNET_USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read()[:200].decode("utf-8", errors="replace")
            if e.code in (429, 500, 502, 503, 504):
                last_err = TdnetFetchError(f"HTTP {e.code}: {detail}")
                _time.sleep(1.5 * (2**attempt))
                continue
            raise TdnetFetchError(f"HTTP {e.code} {url}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            _time.sleep(1.5 * (2**attempt))
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise TdnetFetchError(f"{url}: invalid JSON: {body[:200]!r}") from e
        items = data.get("items") or []
        out: list[dict[str, Any]] = []
        for it in items:
            row = _normalize_tdnet_item(it)
            if row:
                out.append(row)
        return out
    raise TdnetFetchError(f"giving up after {retries} retries: {last_err}")


def fetch_tdnet_range(since: date, until: date, *, sleep_sec: float = 0.5) -> dict[str, list[dict[str, Any]]]:
    """期間内の TDnet 適時開示を日別マップで返す。営業日のみ。

    yanoshin に明示レート制限はないが、礼節的に既定 0.5s sleep を挟む。
    """
    import time as _time

    days = _trading_days(since, until)
    out: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for i, d in enumerate(days, 1):
        try:
            rows = fetch_tdnet_public_day(d)
        except TdnetFetchError as e:
            print(f"  ! {d}: {e}")
            continue
        out[d.isoformat()] = rows
        total += len(rows)
        if sleep_sec:
            _time.sleep(sleep_sec)
        if i % 50 == 0:
            print(f"  ... tdnet {i}/{len(days)} days, last={d}: total {total} rows")
    return out


# ---- フォールバック構造 (スタブ) ----------------------------------------

def fetch_edinet_day(d: date) -> list[dict[str, Any]]:
    """EDINET API フォールバック (現環境では allowlist 未許可)。"""
    raise NotImplementedError("EDINET fetch is Phase 2; host not in allowlist")


# ---- 保存 -----------------------------------------------------------------

def _save(path: Path, payload: Any) -> None:
    from scripts._atomic import atomic_write_json
    atomic_write_json(path, payload)


def save_buyback(by_date: dict[str, list[dict[str, Any]]], cache_dir: Path = CACHE_DIR) -> Path:
    """自社株買い by_date dict を share_buyback_tdnet.json にアトミック書き込み。"""
    out = cache_dir / "share_buyback_tdnet.json"
    payload = {
        "source": "jquants-pro:share_buyback_tdnet",
        "by_date": by_date,
        "record_count": sum(len(v) for v in by_date.values()),
    }
    _save(out, payload)
    return out


def save_fins_summary(by_date: dict[str, list[dict[str, Any]]], cache_dir: Path = CACHE_DIR) -> Path:
    """/fins/summary by_date dict を fins_summary.json にアトミック書き込み。"""
    out = cache_dir / "fins_summary.json"
    payload = {
        "source": "jquants:fins/summary",
        "by_date": by_date,
        "record_count": sum(len(v) for v in by_date.values()),
    }
    _save(out, payload)
    return out


def save_tdnet_all(by_date: dict[str, list[dict[str, Any]]], cache_dir: Path = CACHE_DIR) -> Path:
    """TDnet (yanoshin) by_date dict を tdnet_all.json にアトミック書き込み。"""
    out = cache_dir / "tdnet_all.json"
    payload = {
        "source": "yanoshin:tdnet",
        "by_date": by_date,
        "record_count": sum(len(v) for v in by_date.values()),
    }
    _save(out, payload)
    return out


def save_fins_summary_by_code(by_code: dict[str, list[dict[str, Any]]], cache_dir: Path = CACHE_DIR) -> Path:
    """/fins/summary by_code dict を fins_summary_by_code.json にアトミック書き込み。"""
    out = cache_dir / "fins_summary_by_code.json"
    payload = {
        "source": "jquants:fins/summary",
        "by_code": by_code,
        "record_count": sum(len(v) for v in by_code.values()),
    }
    _save(out, payload)
    return out


# ---- CLI ------------------------------------------------------------------

def _parse_codes(s: str | None) -> list[str]:
    if not s:
        return []
    return [c.strip().zfill(4) for c in s.split(",") if c.strip()]


def main(argv: Iterable[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="ISO date 開始 (既定: 5年前)")
    ap.add_argument("--until", help="ISO date 終了 (既定: 今日)")
    ap.add_argument("--probe", action="store_true", help="疎通確認のみ (直近10営業日)")
    ap.add_argument("--codes", help="この銘柄だけ /fins/summary を取る (カンマ区切り)")
    ap.add_argument("--skip-buyback", action="store_true", help="Pro 自社株買い fetch を skip (allowlist 未通の時用)")
    ap.add_argument("--skip-fins", action="store_true", help="/fins/summary を skip")
    ap.add_argument("--skip-tdnet", action="store_true", help="yanoshin TDnet 全タイトル fetch を skip")
    ap.add_argument("--tdnet-sleep", type=float, default=0.5, help="yanoshin リクエスト間 sleep 秒 (default 0.5)")
    args = ap.parse_args(list(argv) if argv is not None else None)

    today = date.today()
    until = date.fromisoformat(args.until) if args.until else today
    since = date.fromisoformat(args.since) if args.since else today - timedelta(days=365 * 5)
    if args.probe:
        since = until - timedelta(days=14)

    codes = _parse_codes(args.codes)

    print(f"=== fetch_disclosures: since={since} until={until} ===")

    # --- 自社株買い (Pro) ---
    if not args.skip_buyback and not codes:
        if _jquants.is_pro_available():
            print(f"-- self-buyback TDnet (Pro) --")
            by_date = fetch_share_buyback_range(since, until)
            path = save_buyback(by_date)
            print(f"  saved {sum(len(v) for v in by_date.values())} records → {path}")
        else:
            print("-- self-buyback: Pro host not reachable (allowlist/契約不足), skipped --")

    # --- TDnet (yanoshin) ---
    if not args.skip_tdnet and not codes:
        print(f"-- TDnet (yanoshin) --")
        by_date_tdnet = fetch_tdnet_range(since, until, sleep_sec=args.tdnet_sleep)
        path = save_tdnet_all(by_date_tdnet)
        print(f"  saved {sum(len(v) for v in by_date_tdnet.values())} records → {path}")

    # --- /fins/summary ---
    if not args.skip_fins:
        if codes:
            print(f"-- /fins/summary by code: {codes} --")
            by_code: dict[str, list[dict[str, Any]]] = {}
            for c in codes:
                rows = fetch_fins_summary(code=c)
                by_code[c] = rows
                print(f"  {c}: {len(rows)} rows")
            path = save_fins_summary_by_code(by_code)
            print(f"  saved → {path}")
        else:
            print(f"-- /fins/summary by date --")
            by_date = fetch_fins_summary_range_by_date(since, until)
            path = save_fins_summary(by_date)
            print(f"  saved {sum(len(v) for v in by_date.values())} records → {path}")


if __name__ == "__main__":
    main()

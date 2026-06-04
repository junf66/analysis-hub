"""EDINET「自己株券買付状況報告書」(docTypeCode=170)から自社株買い規模%を取得して
data/edge_candidates/buyback_ratios.json に append する(過去分補完)。

TDnet PDF は約5週間で消えるため enrich_buyback_pdf では最新分しか取れない。EDINET は
過去も取得できるので、歴史分の規模%(発行済株式総数に対する割合)を補完する。
⚠️ EDINET は「実施状況(実績/累計)」で TDnet の「取得枠上限%(決定時)」とは意味が異なるため
   source="edinet" でタグ付けして区別する(既存 TDnet 分は source="tdnet" を付与)。

前提: 環境変数 EDINET_API_KEY = **公式 EDINET API v2 の Subscription-Key**。
  ⚠️ サードパーティ edinetdb.jp(EDINET DB) のキー(`edb_` 始まり)とは別物。後者は宛先・認証方式・
     レート上限(100req/日)が異なり本スクリプトでは 401 になる。公式キーは下記で無料発行:
     https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1 (32桁hex / `edb_` なし)。
ネットワーク許可 api.edinet-fsa.go.jp。
parse_edinet_csv は純関数(stdlibのみ)で合成CSVでテスト可能。CIはネットワーク非依存。
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "buyback_ratios.json"
LIST_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
DOC_URL = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"
BUYBACK_DOCTYPE = "170"  # 自己株券買付状況報告書

# EDINET CSV の項目名(部分一致)→ 出力キー
_RATIO_KW = "発行済株式総数に対する割合"
_SHARES_KW = "取得した株式の総数"
_AMOUNT_KW = "取得価額の総額"


def _to_num(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"-?[\d,]+\.?\d*", s)
    return float(m.group(0).replace(",", "")) if m else None


def parse_edinet_csv(text: str) -> dict[str, float | None]:
    """EDINET CSV(タブ区切り, 項目名×値)から 規模%/取得株数/取得価額を抽出(純関数)。"""
    name_to_val: dict[str, str] = {}
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        name = cols[1].strip().strip('"')
        val = cols[-1].strip().strip('"')
        if name and name not in name_to_val:
            name_to_val[name] = val

    def find(kw: str) -> str | None:
        for name, val in name_to_val.items():
            if kw in name:
                return val
        return None

    ratio = _to_num(find(_RATIO_KW))
    # EDINET の割合は小数(0.0308)で入ることが多い → %へ正規化(1以下なら×100)
    if ratio is not None and ratio <= 1.0:
        ratio *= 100.0
    return {"buyback_ratio_pct": round(ratio, 3) if ratio is not None else None,
            "buyback_max_shares": _to_num(find(_SHARES_KW)),
            "buyback_max_amount": _to_num(find(_AMOUNT_KW))}


def sec_to_code4(sec_code: str | None) -> str | None:
    """EDINET secCode(5桁・末尾0)を 4桁証券コードに変換。"""
    if not sec_code:
        return None
    return sec_code[:-1] if len(sec_code) == 5 and sec_code.endswith("0") else sec_code


def _validate_official_key(key: str | None) -> str:
    """公式 EDINET API v2 の Subscription-Key として妥当か検査(純関数・テスト可能)。

    edinetdb.jp(EDINET DB) のキー(`edb_` 始まり)は別サービス用で本スクリプトでは 401 に
    なるため、早期に分かりやすく失敗させる。
    """
    if not key:
        raise SystemExit("EDINET_API_KEY 未設定。公式 EDINET API v2 の Subscription-Key を設定してください。")
    if key.startswith("edb_"):
        raise SystemExit(
            "EDINET_API_KEY が edinetdb.jp(EDINET DB) のキー(edb_...)です。本スクリプトは"
            "公式 EDINET API v2(api.edinet-fsa.go.jp)用のため弾かれます(401)。"
            "公式キーを https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1 で無料発行し設定してください。"
        )
    return key


def _key() -> str:
    return _validate_official_key(os.environ.get("EDINET_API_KEY"))


def list_buyback_docs(date: str, key: str) -> list[dict[str, Any]]:
    """指定日の documents.json から docTypeCode=170 の書類メタを返す。"""
    url = f"{LIST_URL}?date={date}&type=2&Subscription-Key={key}"
    data = json.load(urllib.request.urlopen(url, timeout=40))
    return [r for r in data.get("results", []) if str(r.get("docTypeCode")) == BUYBACK_DOCTYPE]


def fetch_doc_csv(doc_id: str, key: str) -> str:
    """type=5(CSV zip)を取得し、含まれる CSV を結合した本文テキストを返す(UTF-16/UTF-8自動)。"""
    url = f"{DOC_URL.format(doc_id=doc_id)}?type=5&Subscription-Key={key}"
    blob = urllib.request.urlopen(url, timeout=60).read()
    out = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            if name.lower().endswith(".csv"):
                raw = z.read(name)
                for enc in ("utf-16", "utf-16-le", "utf-8-sig", "utf-8"):
                    try:
                        out.append(raw.decode(enc))
                        break
                    except UnicodeDecodeError:
                        continue
    return "\n".join(out)


def _load_existing() -> tuple[list[dict[str, Any]], set[str]]:
    """既存 buyback_ratios.json を読み、source 未設定の既存分に tdnet を付与。(records, 済みキー)。"""
    if not OUT_PATH.exists():
        return [], set()
    data = json.loads(OUT_PATH.read_text())
    recs = data.get("records", [])
    for r in recs:
        r.setdefault("source", "tdnet")
    seen = {f"{r.get('code')}_{r.get('event_date')}" for r in recs}
    return recs, seen


def run(date_from: str, date_to: str, *, sleep_sec: float = 1.5, limit: int | None = None) -> dict[str, Any]:
    """date_from..date_to の自己株券買付状況報告書を取得し buyback_ratios.json に append。"""
    key = _key()
    records, seen = _load_existing()
    failed: list[str] = []
    d0 = datetime.date.fromisoformat(date_from)
    d1 = datetime.date.fromisoformat(date_to)
    added = 0
    day = d0
    while day <= d1:
        try:
            docs = list_buyback_docs(day.isoformat(), key)
        except Exception:  # noqa: BLE001
            docs = []
        for doc in docs:
            code = sec_to_code4(doc.get("secCode"))
            ev = doc.get("periodEnd") or doc.get("submitDateTime", "")[:10]
            kk = f"{code}_{ev}"
            if not code or kk in seen:
                continue
            doc_id = doc.get("docID")
            try:
                parsed = parse_edinet_csv(fetch_doc_csv(doc_id, key))
                records.append({"code": code, "event_date": ev, "edinet_doc_id": doc_id,
                                "source": "edinet", **parsed})
                seen.add(kk)
                added += 1
            except Exception:  # noqa: BLE001
                failed.append(doc_id)
            time.sleep(sleep_sec)
            if limit and added >= limit:
                day = d1
                break
        if added and added % 20 == 0:
            _write(records, failed)
        day += datetime.timedelta(days=1)
    _write(records, failed)
    return {"records": records, "count": len(records), "failed": failed, "added": added}


def _write(records: list[dict[str, Any]], failed: list[str]) -> None:
    atomic_write_json(OUT_PATH, {"records": records, "count": len(records),
                                 "failed": sorted(set(failed))}, indent=0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="date_from", default="2018-01-01")
    ap.add_argument("--to", dest="date_to", default=datetime.date.today().isoformat())
    ap.add_argument("--sleep", type=float, default=1.5)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    print(f"[edinet_buyback] {args.date_from}..{args.date_to} の自己株券買付状況報告書(170)を取得...")
    res = run(args.date_from, args.date_to, sleep_sec=args.sleep, limit=args.limit)
    ok = sum(1 for r in res["records"] if r.get("source") == "edinet" and r.get("buyback_ratio_pct") is not None)
    print(f"[edinet_buyback] 完了 計{res['count']}件(edinet規模%取得{ok}件 / 今回追加{res['added']} / 失敗{len(res['failed'])}) → {OUT_PATH}")


if __name__ == "__main__":
    main()

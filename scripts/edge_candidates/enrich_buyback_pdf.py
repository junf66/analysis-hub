"""自社株買い決定(TDnet)のPDFから「発行済株式数に対する割合(%)」等を抽出して公開する。

背景: 自社株買いの規模(%)は PDF のみで J-Quants API に構造化されない(fetch_buyback.py の制約)。
TDnet PDF を yanoshin の document_url 経由で取得し、本文を正規表現でパースして補完する。
受け手(stocks-Large-holding-report)は buyback_ratio_pct を「自社株買い」chip の数値範囲filterに使う。

入力: data/edge_candidates/td_buyback_decisions.json (DiscItems=11105, Code/DiscDate/DiscNo)
出力: data/edge_candidates/buyback_ratios.json
  {records:[{code, event_date, disc_no, buyback_ratio_pct, buyback_max_shares,
             buyback_max_amount, title}], count, failed:[disc_no,...]}

依存: pypdf(本文抽出)。**遅延importなので CI(stdlibのみ)では import されず、正規表現パースは
合成テキストでテスト可能**。バッチ実行時のみ `pip install pypdf cffi` が要る。
yanoshin/PDFホストへの network 許可が必要。rate limit 5秒/req、checkpoint+resume、失敗は記録。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_PATH = REPO_ROOT / "data" / "edge_candidates" / "td_buyback_decisions.json"
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "buyback_ratios.json"
YANOSHIN_BASE = "https://webapi.yanoshin.jp/webapi/tdnet/list"

# 規模%: 「発行済株式総数(自己株式を除く)に対する割合 X%」/ 逆順「X% ... 発行済」
_RE_RATIO = (re.compile(r"発行済株式総数.{0,60}?に対する割合.{0,15}?([\d.]+)\s*[%％]"),
             re.compile(r"([\d.]+)\s*[%％].{0,30}?発行済株式"))
_RE_SHARES = re.compile(r"取得する株式の総数.{0,60}?([\d,]+)\s*株")
_RE_AMOUNT = re.compile(r"取得価額の総額.{0,60}?([\d,]+)\s*円")


def _num(s: str | None) -> float | None:
    return float(s.replace(",", "")) if s else None


BUYBACK_ITEM = "11105"  # 自己株式取得の DiscItems コード


def merge_decisions(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """既存 + 新規の自社株買い決定を DiscNo で重複排除して返す(新しい順)。"""
    by_no = {str(r.get("DiscNo")): r for r in existing}
    for r in new:
        by_no[str(r.get("DiscNo"))] = r
    return sorted(by_no.values(), key=lambda r: r.get("DiscDate", ""), reverse=True)


def fetch_recent_decisions(days_back: int = 14) -> list[dict[str, Any]]:
    """直近 days_back 日の /td/list から DiscItems=11105(自社株買い決定)を集める(週次更新用)。"""
    import datetime
    from scripts import _jquants
    out: list[dict[str, Any]] = []
    today = datetime.date.today()
    for d in range(days_back):
        day = (today - datetime.timedelta(days=d)).isoformat()
        try:
            rows = _jquants.get_list("/td/list", date=day)
        except Exception:  # noqa: BLE001  休日や一時失敗はスキップ
            continue
        for r in rows:
            if BUYBACK_ITEM in (r.get("DiscItems") or "").split("|"):
                out.append(r)
    return out


def parse_buyback_text(text: str) -> dict[str, float | None]:
    """PDF本文テキストから 規模%/取得上限株数/取得上限金額 を抽出する(純関数・依存なし)。"""
    t = text.replace("\n", "").replace(" ", "").replace("　", "")
    ratio = None
    for rgx in _RE_RATIO:
        m = rgx.search(t)
        if m:
            ratio = float(m.group(1))
            break
    sh = _RE_SHARES.search(t)
    am = _RE_AMOUNT.search(t)
    return {"buyback_ratio_pct": ratio,
            "buyback_max_shares": _num(sh.group(1) if sh else None),
            "buyback_max_amount": _num(am.group(1) if am else None)}


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """PDFバイト列から本文テキストを返す(pypdf を遅延import)。"""
    from pypdf import PdfReader  # 遅延import: CI(stdlib)では未使用
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "".join(p.extract_text() or "" for p in reader.pages)


def yanoshin_doc_url(code4: str, yyyymmdd: str, cache: dict[str, list[dict[str, Any]]]) -> str | None:
    """yanoshin から (code4, 日付) の自己株式取得開示の document_url を返す。日付単位でcache。"""
    if yyyymmdd not in cache:
        url = f"{YANOSHIN_BASE}/{yyyymmdd}-{yyyymmdd}.json?limit=300"
        cache[yyyymmdd] = json.load(urllib.request.urlopen(url, timeout=30)).get("items", [])
    for it in cache[yyyymmdd]:
        t = it.get("Tdnet", it)
        cc = str(t.get("company_code", ""))
        cc = cc[:4] if len(cc) == 5 else cc
        if cc == code4 and "自己株式" in str(t.get("title", "")):
            return t.get("document_url")
    return None


def _load_checkpoint() -> tuple[dict[str, dict[str, Any]], set[str]]:
    """既存 buyback_ratios.json から (disc_no→record, 済みdisc_no) を返す。"""
    if not OUT_PATH.exists():
        return {}, set()
    data = json.loads(OUT_PATH.read_text())
    by = {r["disc_no"]: r for r in data.get("records", [])}
    return by, set(by) | set(data.get("failed", []))


def enrich(records: list[dict[str, Any]], *, limit: int | None = None,
           sleep_sec: float = 5.0) -> dict[str, Any]:
    """各 buyback 決定の PDF をパースして規模%等を付与。checkpoint+resume・失敗記録。"""
    done_rec, done_ids = _load_checkpoint()
    cache: dict[str, list[dict[str, Any]]] = {}
    failed = {i for i in done_ids if i not in done_rec}
    todo = [r for r in records if str(r.get("DiscNo")) not in done_ids]
    if limit:
        todo = todo[:limit]
    for i, r in enumerate(todo, 1):
        dn = str(r.get("DiscNo"))
        code4 = str(r.get("Code", ""))[:4]
        ymd = (r.get("DiscDate") or "").replace("-", "")
        try:
            durl = yanoshin_doc_url(code4, ymd, cache)
            if not durl:
                failed.add(dn)
                continue
            pdf = urllib.request.urlopen(durl, timeout=40).read()
            parsed = parse_buyback_text(extract_pdf_text(pdf))
            done_rec[dn] = {"code": code4, "event_date": r.get("DiscDate"), "disc_no": dn,
                            "title": r.get("Title"), **parsed}
            failed.discard(dn)
        except Exception:  # noqa: BLE001  ネット/PDF失敗は記録して継続
            failed.add(dn)
        if i % 25 == 0:
            _write(done_rec, failed)
            print(f"  ...{i}/{len(todo)} ({dn} ratio={done_rec.get(dn, {}).get('buyback_ratio_pct')})")
        time.sleep(sleep_sec)
    _write(done_rec, failed)
    return {"records": list(done_rec.values()), "count": len(done_rec), "failed": sorted(failed)}


def _write(done_rec: dict[str, dict[str, Any]], failed: set[str]) -> None:
    atomic_write_json(OUT_PATH, {"records": sorted(done_rec.values(), key=lambda x: x["disc_no"]),
                                 "count": len(done_rec), "failed": sorted(failed)}, indent=0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None, help="先頭N件のみ(テスト用)")
    ap.add_argument("--sleep", type=float, default=5.0, help="req間スリープ秒")
    ap.add_argument("--refresh-list", type=int, default=0, metavar="DAYS",
                    help="直近DAYS日の自社株買い決定を /td/list から取得して td_buyback_decisions.json に追加(週次cron用)")
    args = ap.parse_args()
    recs = json.loads(SRC_PATH.read_text()).get("records", []) if SRC_PATH.exists() else []
    if args.refresh_list:
        new = fetch_recent_decisions(args.refresh_list)
        recs = merge_decisions(recs, new)
        atomic_write_json(SRC_PATH, {"records": recs, "count": len(recs)}, indent=0)
        print(f"[buyback_pdf] 決定リスト更新: 新規候補{len(new)}件 → 合計{len(recs)}件")
    # PDF実体は release.tdnet.info が約5週間しか保持しないため、新しい順に処理して
    # 取得可能ウィンドウを優先する。過去分は404=取得不能(将来の週次実行で前進蓄積する設計)。
    recs.sort(key=lambda r: r.get("DiscDate", ""), reverse=True)
    print(f"[buyback_pdf] {len(recs)}件の自社株買い決定をパース (新しい順, limit={args.limit})...")
    res = enrich(recs, limit=args.limit, sleep_sec=args.sleep)
    ok = sum(1 for r in res["records"] if r.get("buyback_ratio_pct") is not None)
    print(f"[buyback_pdf] 完了 {res['count']}件 (規模%取得 {ok}件 / 失敗 {len(res['failed'])}件) → {OUT_PATH}")


if __name__ == "__main__":
    main()

"""EDINET目論見書から各IPOの実ロックアップ条項(後N日目)を取得する。

ロックアップ解除ショート(🟡昇格候補)の検証は『上場+90/180暦日』の汎用マークで行ってきたが、
真の解除日は各社の有価証券届出書「募集又は売出しに関する特別記載事項 ４ ロックアップについて」
に明記される(例: キオクシア=「上場日後180日目の2025年6月15日まで」)。本スクリプトで実条項を取得し、
汎用マークを真の解除日に置換して再検証できるようにする。

手順(レジューム可):
  1. ipo_96ut_ratings の各codeに equities_master の社名・ipo_bars_raw の上場日を結合。
  2. EDINET documents.json を上場前の窓[上場-45〜-3日]で日次走査(キャッシュ)、社名一致の
     有価証券届出書(docTypeCode 030/040)を特定。最新の届出を採用。
  3. type=1 ZIP の PublicDoc *.htm から「後(\\d+)日目」を全抽出 → lockup_days。
  4. data/edge_candidates/ipo_lockup_terms.json に逐次保存。

network: api.edinet-fsa.go.jp(allowlist要)。鍵=環境変数 EDINET_API_KEY。
"""
from __future__ import annotations

import datetime
import html
import io
import json
import os
import re
import urllib.request
import zipfile
from pathlib import Path

from scripts._atomic import atomic_write_json

REPO = Path(__file__).resolve().parent.parent.parent
RATINGS = REPO / "data" / "edge_candidates" / "ipo_96ut_ratings.json"
MASTER = REPO / "data" / "edge_candidates" / "equities_master.json"
BARS = REPO / "cache" / "ipo_bars_raw.json"
DAYS_CACHE = REPO / "cache" / "edinet_days.json"          # date -> [{docID,docType,filer}]
OUT = REPO / "data" / "edge_candidates" / "ipo_lockup_terms.json"

API = "https://api.edinet-fsa.go.jp/api/v2"
KEY = os.environ.get("EDINET_API_KEY", "")
SREG = {"030", "040"}            # 有価証券届出書 / 訂正
WINDOW = (45, 3)                 # 上場前 [−45日, −3日] を走査


def _c5(code: str) -> str:
    code = str(code)
    return code if len(code) == 5 else code + "0"


def _norm(name: str) -> str:
    """社名正規化(株式会社・記号・空白除去)。"""
    if not name:
        return ""
    for x in ("株式会社", "(株)", "（株）", " ", "　", "・", "．", "."):
        name = name.replace(x, "")
    return name


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "analysis-hub"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _day_docs(date: str, cache: dict) -> list[dict]:
    """指定日のEDINET届出一覧(有報届出のみ最小フィールド)をキャッシュ付きで返す。"""
    if date in cache:
        return cache[date]
    try:
        d = json.loads(_get(f"{API}/documents.json?date={date}&type=2&Subscription-Key={KEY}", 30))
        docs = [{"docID": x["docID"], "docType": x.get("docTypeCode"), "filer": x.get("filerName")}
                for x in d.get("results", []) if x.get("docTypeCode") in SREG]
    except Exception:  # noqa: BLE001  非提出日/エラーは空
        docs = []
    cache[date] = docs
    return docs


def _extract_lockup(docid: str) -> dict:
    """届出ZIP本文の『ロックアップについて』節から日数(日目/日間)・解除日・価格解除を抽出。

    VC等の短期ロックは「90日間」、主要株主は「上場日後180日目」等と表現が割れるため両方拾う。
    誤検出を避けるためロックアップ節(見出し〜次節or一定長)に限定して走査する。
    """
    raw = _get(f"{API}/documents/{docid}?type=1&Subscription-Key={KEY}", 90)
    z = zipfile.ZipFile(io.BytesIO(raw))
    txt = ""
    for n in z.namelist():
        # 本文は PublicDoc/*.htm だが、新規IPOの030等は XBRL/PublicDoc/*ixbrl.htm のみのことがある
        if "PublicDoc/" in n and n.lower().endswith((".htm", ".html")):
            txt += html.unescape(re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", z.read(n).decode("utf-8", "ignore"))))
    days: set[int] = set()
    dates: set[str] = set()
    price_release = False
    # 「ロックアップについて」見出し以降(目論見書交付/その他の記載 までを上限)を節とみなす
    for m in re.finditer(r"ロックアップについて", txt):
        end = txt.find("目論見書の交付", m.start())
        seg = txt[m.start(): end if 0 <= end - m.start() <= 6000 else m.start() + 6000]
        for d in re.findall(r"後\s*(\d{1,3})\s*日目|起算して?\s*(\d{1,3})\s*日|(\d{1,3})\s*日間", seg):
            for g in d:
                if g:
                    days.add(int(g))
        dates.update(re.findall(r"\d{4}年\s*\d{1,2}月\s*\d{1,2}日", seg))
        if re.search(r"(\d(?:\.\d)?|[０-９])\s*倍", seg):
            price_release = True
    # 節が取れない場合のフォールバック(全文の「後N日目」)
    if not days:
        days.update(int(m) for m in re.findall(r"後\s*(\d{1,3})\s*日目", txt))
    return {"lockup_days": sorted(d for d in days if 30 <= d <= 400),
            "lockup_dates": sorted(dates)[:6], "price_release": price_release}


def _addc(dstr: str, n: int) -> str:
    y, m, d = map(int, dstr.split("-"))
    return (datetime.date(y, m, d) + datetime.timedelta(days=n)).isoformat()


def main() -> None:
    ratings = json.loads(RATINGS.read_text())["records"]
    master = {str(r["Code"]): r for r in json.loads(MASTER.read_text())["records"]}
    bars = json.loads(BARS.read_text())
    days_cache = json.loads(DAYS_CACHE.read_text()) if DAYS_CACHE.exists() else {}
    out = json.loads(OUT.read_text()) if OUT.exists() else {}

    todo = []
    for r in ratings:
        code = r["code"]
        if out.get(code, {}).get("status") == "ok":   # ok は確定済みゆえ skip(非okは再試行)
            continue
        m = master.get(_c5(code))
        rows = bars.get(code) or []
        if not m or not m.get("CoName") or not rows:
            out[code] = {"status": "no_name_or_bars"}
            continue
        listing = min(d for d, *_ in rows)
        todo.append((code, _norm(m["CoName"]), listing))

    print(f"対象 {len(todo)} 社 (既取得 {sum(1 for v in out.values() if v.get('lockup_days') is not None)})", flush=True)
    for i, (code, coname, listing) in enumerate(todo, 1):
        # 窓内の社名一致届出を全部集める(訂正040は条項を省くことがあるので原本030を優先)
        matches = []
        for back in range(WINDOW[1], WINDOW[0] + 1):
            for doc in _day_docs(_addc(listing, -back), days_cache):
                fn = _norm(doc["filer"])
                if fn and (coname in fn or fn in coname or (len(coname) >= 4 and coname[:4] in fn)):
                    matches.append(doc)
        # 030(原本)を先に、040(訂正)を後に試し、最初に「後N日目」が取れた届出を採用
        matches.sort(key=lambda d: (d["docType"] != "030", d["docID"]))
        result = None
        for doc in matches:
            try:
                info = _extract_lockup(doc["docID"])
            except Exception as e:  # noqa: BLE001
                result = {"status": f"extract_err:{type(e).__name__}", "listing": listing, "docID": doc["docID"]}
                continue
            if info["lockup_days"]:
                result = {"status": "ok", "listing": listing, "docID": doc["docID"],
                          "lockup_days": info["lockup_days"], "lockup_dates": info["lockup_dates"],
                          "price_release": info["price_release"]}
                break
            result = {"status": "no_lockup_text", "listing": listing, "docID": doc["docID"],
                      "lockup_days": [], "price_release": info["price_release"]}
        out[code] = result or {"status": "no_doc", "listing": listing}
        if i % 10 == 0:
            atomic_write_json(DAYS_CACHE, days_cache)
            atomic_write_json(OUT, out)
            print(f"  {i}/{len(todo)} ({code} {out[code].get('status')} {out[code].get('lockup_days')})", flush=True)

    atomic_write_json(DAYS_CACHE, days_cache)
    atomic_write_json(OUT, out)
    ok = [v for v in out.values() if v.get("status") == "ok"]
    print(f"完了: ok {len(ok)} / 全 {len(out)}", flush=True)
    import collections
    dist = collections.Counter(tuple(v["lockup_days"]) for v in ok if v.get("lockup_days"))
    print("lockup_days 分布(上位):", dist.most_common(12), flush=True)


if __name__ == "__main__":
    main()

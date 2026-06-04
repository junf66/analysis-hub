"""EDINET「自己株券買付状況報告書」(docTypeCode=220/230)から自社株買いの取得枠規模%を
取得して data/edge_candidates/buyback_ratios.json に append する(過去分補完)。

TDnet PDF は約5週間で消えるため enrich_buyback_pdf では最新分しか取れない。EDINET は
それより長く保持するので過去分の規模%(発行済株式総数に対する取得枠割合)を補完する。

⚠️ 重要な実データ知見(2026-06 公式キーで実測):
- docTypeCode は **220=自己株券買付状況報告書 / 230=訂正版**(170 は訂正半期報告書で別物)。
- 報告書 CSV は離散 XBRL 要素ではなく **テキストブロック**に数値が埋め込まれている。
  「取締役会(株主総会)決議による取得の状況」=取得枠(株数・金額)+取得期間+累計、
  「保有状況」=発行済株式総数。決議枠の株数と金額は区切り無しで連結されるため
  カンマ区切り(\\d{1,3}(,\\d{3})*)で一意に分割する。
- buyback_ratio_pct = 取得枠株数 / 発行済株式総数 ×100(=決定枠%)。TDnet の取得枠上限%と
  同一指標なので tdnet/edinet バッジが直接比較できる(source で区別)。
- EDINET はこの報告書を **約1年(縦覧期間)しか保持しない**。8年遡及は不可能で、
  取得可能なのは概ね直近12か月(実測 2025-06〜)のみ。

前提: 環境変数 EDINET_API_KEY (Subscription-Key)。ネットワーク許可 api.edinet-fsa.go.jp。
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
BUYBACK_DOCTYPES = {"220", "230"}  # 自己株券買付状況報告書 / 訂正版

# 全角数字→半角(日付の月日が全角で入るため正規化してから解析)
_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
# カンマ区切り整数(連結された複数値を一意に分割できる)
_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})*")


def _nums(s: str) -> list[float]:
    """カンマ区切り整数の連続(区切り無し連結含む)を数値リストへ分解。"""
    return [float(m.replace(",", "")) for m in _NUM_RE.findall(s)]


def _decision_date(res: str) -> str | None:
    """決議状況テキストから取締役会/株主総会の決議日(最初の1件)を YYYY-MM-DD で返す。"""
    m = re.search(r"(?:取締役会|株主総会)[（(]\s*(\d{4})年(\d{1,2})月(\d{1,2})日", res)
    if not m:
        return None
    y, mo, da = (int(g) for g in m.groups())
    return f"{y:04d}-{mo:02d}-{da:02d}"


def _report_end(res: str, hold: str, blocks: dict[str, str]) -> str | None:
    """報告対象月末日を YYYY-MM-DD。

    各テキストブロック冒頭の「YYYY年MM月DD日現在」(=報告月末日)を採用。無ければ
    提出日(FilingDateCoverPage)。さらに無ければ None(呼び出し側で提出日時を補完)。

    注意: 「報告期間、表紙」の至日は買付プログラムの取得期間終了予定(将来日)であり
    報告対象月末ではない。これを拾うと event_date が未来日になる(過去の不具合)。
    """
    for blk in (hold, res):
        m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日現在", blk)
        if m:
            y, mo, da = (int(g) for g in m.groups())
            return f"{y:04d}-{mo:02d}-{da:02d}"
    fd = blocks.get("提出日、表紙", "").strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", fd):
        return fd[:10]
    return None


def parse_edinet_csv(text: str) -> dict[str, Any]:
    """EDINET 自己株券買付状況報告書 CSV(タブ区切り)から取得枠規模%等を抽出(純関数)。

    返り値: buyback_ratio_pct(取得枠株数/発行済×100), buyback_max_shares(取得枠株数),
    buyback_max_amount(取得枠金額・円), issued_shares(発行済株式総数),
    cumulative_shares/cumulative_amount(報告月末累計取得), decision_date(決議日),
    report_end(報告対象月末日)。
    """
    text = text.translate(_FW_DIGITS)
    blocks: dict[str, str] = {}
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) >= 2:
            name = cols[1].strip().strip('"')
            val = cols[-1].strip().strip('"')
            if name:
                blocks.setdefault(name, val)

    def _clean(s: str) -> str:
        return s.replace("　", " ")

    res = _clean(blocks.get("取締役会決議による取得の状況 [テキストブロック]", "")
                 or blocks.get("株主総会決議による取得の状況 [テキストブロック]", ""))
    hold = _clean(blocks.get("保有状況 [テキストブロック]", ""))
    alltext = _clean(" ".join(blocks.values()))

    # 発行済株式総数(「(保有自己株式数を除く)」等の注記を挟む場合あり)
    mi = (re.search(r"発行済株式総数(?:[（(][^）)]*[）)])?\s*(\d{1,3}(?:,\d{3})*)", hold)
          or re.search(r"発行済株式総数(?:[（(][^）)]*[）)])?\s*(\d{1,3}(?:,\d{3})*)", alltext))
    issued = float(mi.group(1).replace(",", "")) if mi else None

    # 取得枠(決議状況): 取得期間の閉じ括弧の後〜「報告月における取得」の手前の数値群。
    # 「1,000,000（上限）1,400,000,000」「8,000,000株を上限とする。10,000,000,000円…」等の
    # 注記混じりでもカンマ区切りで株数・金額を取り出せる。
    frame_sh = frame_amt = None
    mf = re.search(r"決議状況.*?取得期間[^）)]*[）)]\s*(.*?)報告月における取得", res)
    if mf:
        n = _nums(mf.group(1))
        if len(n) >= 2:
            frame_sh, frame_amt = n[0], n[1]
        elif n:
            frame_sh = n[0]

    # 報告月末累計取得(株数・金額)。未取得は「－－」で数値無し→None。
    cum_sh = cum_amt = None
    mc = re.search(r"累計取得自己株式\s*(.*?)(?:自己株式取得の進捗|[（(]注|$)", res)
    if mc:
        n = _nums(mc.group(1))
        if len(n) >= 2:
            cum_sh, cum_amt = n[0], n[1]

    ratio = round(frame_sh / issued * 100, 3) if (frame_sh and issued) else None
    return {
        "buyback_ratio_pct": ratio,
        "buyback_max_shares": frame_sh,
        "buyback_max_amount": frame_amt,
        "issued_shares": issued,
        "cumulative_shares": cum_sh,
        "cumulative_amount": cum_amt,
        "decision_date": _decision_date(res),
        "report_end": _report_end(res, hold, blocks),
    }


def sec_to_code4(sec_code: str | None) -> str | None:
    """EDINET secCode(5桁・末尾0)を 4桁証券コードに変換。"""
    if not sec_code:
        return None
    return sec_code[:-1] if len(sec_code) == 5 and sec_code.endswith("0") else sec_code


def _key() -> str:
    key = os.environ.get("EDINET_API_KEY")
    if not key:
        raise SystemExit("EDINET_API_KEY 未設定。環境変数に Subscription-Key を設定してください。")
    if key.startswith("edb_"):
        raise SystemExit("EDINET_API_KEY が edb_ で始まる=edinetdb.jp(別サービス)のキーです。"
                         "公式 EDINET API(api.edinet-fsa.go.jp)の Subscription-Key を設定してください。")
    return key


def list_buyback_docs(date: str, key: str) -> list[dict[str, Any]]:
    """指定日の documents.json から自己株券買付状況報告書(220/230, CSV有)の書類メタを返す。"""
    url = f"{LIST_URL}?date={date}&type=2&Subscription-Key={key}"
    data = json.load(urllib.request.urlopen(url, timeout=40))
    return [r for r in data.get("results", [])
            if str(r.get("docTypeCode")) in BUYBACK_DOCTYPES and r.get("csvFlag") == "1"]


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
    """既存 buyback_ratios.json を読み、source 未設定の既存分に tdnet を付与。
    返り値: (records, 取得済 edinet_doc_id 集合)。docID 単位で resume する。"""
    if not OUT_PATH.exists():
        return [], set()
    data = json.loads(OUT_PATH.read_text())
    recs = data.get("records", [])
    for r in recs:
        r.setdefault("source", "tdnet")
    seen_docs = {r["edinet_doc_id"] for r in recs if r.get("edinet_doc_id")}
    return recs, seen_docs


def run(date_from: str, date_to: str, *, sleep_sec: float = 1.5, limit: int | None = None) -> dict[str, Any]:
    """date_from..date_to の自己株券買付状況報告書を取得し buyback_ratios.json に append。

    docID 単位で resume(既取得はスキップ)。EDINET は同報告書を約1年しか保持しないため
    取得可能なのは概ね直近12か月のみ(それ以前の日付は 0 件で返る)。
    """
    key = _key()
    records, seen_docs = _load_existing()
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
            doc_id = doc.get("docID")
            if not doc_id or doc_id in seen_docs:
                continue
            code = sec_to_code4(doc.get("secCode"))
            if not code:
                seen_docs.add(doc_id)  # 投信等で証券コード無し→以後スキップ
                continue
            try:
                parsed = parse_edinet_csv(fetch_doc_csv(doc_id, key))
            except Exception:  # noqa: BLE001
                failed.append(doc_id)
                time.sleep(sleep_sec)
                continue
            ev = parsed.pop("report_end") or doc.get("submitDateTime", "")[:10]
            records.append({"code": code, "event_date": ev, "edinet_doc_id": doc_id,
                            "source": "edinet", **parsed})
            seen_docs.add(doc_id)
            added += 1
            time.sleep(sleep_sec)
            if added % 20 == 0:
                _write(records, failed)
            if limit and added >= limit:
                _write(records, failed)
                return {"records": records, "count": len(records), "failed": failed, "added": added}
        day += datetime.timedelta(days=1)
    _write(records, failed)
    return {"records": records, "count": len(records), "failed": failed, "added": added}


def _write(records: list[dict[str, Any]], failed: list[str]) -> None:
    atomic_write_json(OUT_PATH, {"records": records, "count": len(records),
                                 "failed": sorted(set(failed))}, indent=0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="date_from", default="2025-06-01",
                    help="開始日(EDINET は約1年しか保持しないため既定は直近窓の先頭)")
    ap.add_argument("--to", dest="date_to", default=datetime.date.today().isoformat())
    ap.add_argument("--sleep", type=float, default=1.5)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    print(f"[edinet_buyback] {args.date_from}..{args.date_to} の自己株券買付状況報告書(220/230)を取得...")
    res = run(args.date_from, args.date_to, sleep_sec=args.sleep, limit=args.limit)
    ok = sum(1 for r in res["records"]
             if r.get("source") == "edinet" and r.get("buyback_ratio_pct") is not None)
    print(f"[edinet_buyback] 完了 計{res['count']}件"
          f"(edinet規模%取得{ok}件 / 今回追加{res['added']} / 失敗{len(res['failed'])}) → {OUT_PATH}")


if __name__ == "__main__":
    main()

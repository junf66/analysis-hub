"""キッコーマン型（自社株買い決定 × 同日決算短信）データセットを公式APIで組成する。

背景: kouaku 分類器は決算 NP YoY を ±10%、修正/配当を ±3% の閾値でタグ化するため、
中立帯(軽い増減益・軽微修正)の自社株買い同日決算が丸ごと未分析だった (キッコーマン型)。
本スクリプトは TDnet 公式インデックス(/td/bulk) と財務情報(/fins/summary) を突き合わせ、
減益/増益の "程度" を連続値(NP/OP/Sales YoY)で付与した event リストを作る。

出力: data/edge_candidates/buyback_earnings.json
後段の enrich (翌営業日リターン) → magnitude 別 EV 検証へ渡す。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "buyback_earnings.json"
BUYBACK_DISC_ITEM = "11105"          # 自己株式取得 (公式 DiscItems)
_YOY_FIELDS = [("NP", "np_yoy"), ("OP", "op_yoy"), ("OdP", "odp_yoy"), ("Sales", "sales_yoy")]


def fetch_td_bulk() -> list[dict[str, str]]:
    """/td/bulk の権威適時開示インデックス(過去5年・全件 CSV.gz)を取得して行 dict のリストで返す。"""
    key = os.environ["JQUANTS_API_KEY"]
    meta = json.load(urllib.request.urlopen(
        urllib.request.Request(f"{_jquants.BASE_URL}/td/bulk", headers={"x-api-key": key}), timeout=60))
    raw = urllib.request.urlopen(meta["url"], timeout=180).read()
    txt = gzip.decompress(raw).decode("utf-8", "ignore")
    return list(csv.DictReader(io.StringIO(txt)))


def _has_item(row: dict[str, str], code: str) -> bool:
    return code in (row.get("DiscItems") or "").split("|")


def select_buyback_with_earnings(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """自己株式取得(決定) と 決算短信 が同日に出た (code, date) を抽出する (純関数)。"""
    buy = {}
    for r in rows:
        if _has_item(r, BUYBACK_DISC_ITEM) and ("決定" in r["Title"] or "取得に係る" in r["Title"]):
            buy.setdefault((r["Code"], r["DiscDate"]), r["DiscTime"])
    kessan = {(r["Code"], r["DiscDate"]) for r in rows if "決算短信" in (r.get("Title") or "")}
    out = []
    for (code, date), disc_time in sorted(buy.items()):
        if (code, date) in kessan:
            out.append({"code": code, "event_date": date, "event_type": "buyback_earnings",
                        "source": "td_official", "attrs": {"disc_time": disc_time}})
    return out


def compute_yoy(summary: list[dict[str, Any]], disc_date: str) -> dict[str, Any]:
    """その code の /fins/summary から disc_date 開示の決算行を探し、同 period 前年比 YoY を算出。"""
    # period(CurPerType) × 年(CurPerEn 西暦) → 各指標値
    by_pt: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for r in summary:
        pt, pe = r.get("CurPerType"), (r.get("CurPerEn") or "")
        if not pt or len(pe) < 4:
            continue
        vals = {}
        for fld, _ in _YOY_FIELDS:
            v = r.get(fld)
            if v not in (None, ""):
                try:
                    vals[fld] = float(v)
                except (TypeError, ValueError):
                    pass
        by_pt[pt][pe[:4]] = vals
    row = next((r for r in summary if r.get("DiscDate") == disc_date), None)
    if row is None:
        return {}
    pt, yr = row.get("CurPerType"), (row.get("CurPerEn") or "")[:4]
    out: dict[str, Any] = {"per_type": pt, "fins_disc_date": disc_date}
    if not (pt and yr.isdigit()):
        return out
    prev = by_pt.get(pt, {}).get(str(int(yr) - 1), {})
    for fld, key in _YOY_FIELDS:
        cur = by_pt.get(pt, {}).get(yr, {}).get(fld)
        pv = prev.get(fld)
        if cur is not None and pv not in (None, 0):
            out[key] = (cur / pv - 1.0) * 100.0
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    print("[buyback_earnings] /td/bulk 取得中...")
    rows = fetch_td_bulk()
    events = select_buyback_with_earnings(rows)
    codes = sorted({e["code"] for e in events})
    print(f"[buyback_earnings] 同日(自社株買い決定×決算短信) {len(events)}件 / {len(codes)}銘柄。YoY付与開始")
    cache: dict[str, list[dict[str, Any]]] = {}
    for i, code in enumerate(codes, 1):
        try:
            cache[code] = _jquants.get_list("/fins/summary", code=code)
        except _jquants.JQuantsError:
            cache[code] = []
        if i % 200 == 0:
            print(f"  ...summary {i}/{len(codes)}")
    for e in events:
        e["attrs"].update(compute_yoy(cache.get(e["code"], []), e["event_date"]))
    atomic_write_json(args.out, {"records": events, "count": len(events)}, indent=0)
    got = sum(1 for e in events if e["attrs"].get("np_yoy") is not None)
    print(f"wrote {args.out} (np_yoy付与 {got}/{len(events)})")


if __name__ == "__main__":
    main()

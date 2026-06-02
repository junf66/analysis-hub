"""kouaku の実績ベース部分を公式データ(/td/bulk DiscItems + /fins/summary)で再構築する。

公式化できる範囲: 決算 NP YoY → 減益(genshu)/増益(kouhou)、配当 DivAnn YoY → 増配(zouhai)/
減配(genhai)、自己株式取得(jisha, DiscItems 11105)、株式分割(split, 11107)。同日 good×bad で
mixed イベントを構成し subpattern を付与、既存 kouaku と (code,event_date) で突き合わせて
再現率を検証する。

制約: 来期予想純利益(forecast NP)が /fins/summary に無く /fins/details は契約外(403)のため、
kahou/kahou_nx/kouhou_nx(予想ベース, 確定エッジ zouhai_kahou_nx を含む)は再構築対象外。
出力: data/edge_candidates/kouaku_official.json + 標準出力に再現率。
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
FINS_PATH = REPO_ROOT / "data" / "edge_candidates" / "fins_summary.json"
KOUAKU_PATH = REPO_ROOT / "data" / "kouaku_records.json"
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "kouaku_official.json"
NP_BAD, NP_GOOD = -10.0, 10.0          # genshu / kouhou 閾値 (既存 NP_YOY_BAD_THRESHOLD_PCT と一致)
DIV_BAD, DIV_GOOD = -3.0, 3.0          # genhai / zouhai 閾値
JISHA_ITEM, SPLIT_ITEM = "11105", "11107"
KESSAN_ITEMS = {"11301", "11304", "11307", "11101"}


def fetch_td_bulk() -> list[dict[str, str]]:
    key = os.environ["JQUANTS_API_KEY"]
    meta = json.load(urllib.request.urlopen(
        urllib.request.Request(f"{_jquants.BASE_URL}/td/bulk", headers={"x-api-key": key}), timeout=60))
    txt = gzip.decompress(urllib.request.urlopen(meta["url"], timeout=180).read()).decode("utf-8", "ignore")
    return list(csv.DictReader(io.StringIO(txt)))


def _yoy(by_pt: dict, pt: str, yr: str, field: str) -> float | None:
    try:
        cur = by_pt[pt][yr][field]
        prev = by_pt[pt][str(int(yr) - 1)][field]
    except (KeyError, ValueError):
        return None
    return (cur / prev - 1.0) * 100.0 if (cur is not None and prev) else None


def _build_fins_index(summary: list[dict]) -> tuple[dict, dict]:
    """(DiscDate→決算行) と (period×年→指標) を返す。"""
    by_date = {}
    by_pt: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for r in summary:
        dd, pt, pe = r.get("DiscDate"), r.get("CurPerType"), (r.get("CurPerEn") or "")
        if dd:
            by_date[dd] = r
        if pt and len(pe) >= 4:
            for f in ("NP", "DivAnn", "DivFY"):
                v = r.get(f)
                if v not in (None, ""):
                    try:
                        by_pt[pt][pe[:4]][f] = float(v)
                    except (TypeError, ValueError):
                        pass
    return by_date, by_pt


def classify_event(td_items: set[str], fins_row: dict | None, by_pt: dict) -> tuple[list[str], list[str]]:
    """その日の DiscItems と決算行から good/bad 材料タグを返す (実績ベースのみ)。"""
    good, bad = [], []
    if JISHA_ITEM in td_items:
        good.append("jisha")
    if SPLIT_ITEM in td_items:
        good.append("split")
    if fins_row is not None:
        pt, yr = fins_row.get("CurPerType"), (fins_row.get("CurPerEn") or "")[:4]
        if pt and yr.isdigit():
            np_yoy = _yoy(by_pt, pt, yr, "NP")
            if np_yoy is not None:
                if np_yoy <= NP_BAD:
                    bad.append("genshu")
                elif np_yoy >= NP_GOOD:
                    good.append("kouhou")
            div_yoy = _yoy(by_pt, pt, yr, "DivAnn") or _yoy(by_pt, pt, yr, "DivFY")
            if div_yoy is not None:
                if div_yoy >= DIV_GOOD:
                    good.append("zouhai")
                elif div_yoy <= DIV_BAD:
                    bad.append("genhai")
    return good, bad


def reconstruct(td_rows: list[dict], fins: dict[str, list]) -> list[dict[str, Any]]:
    """公式データから実績ベースの mixed イベントを構成する。"""
    by_cd_items: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in td_rows:
        for c in (r.get("DiscItems") or "").split("|"):
            by_cd_items[(r["Code"], r["DiscDate"])].add(c)
    fins_idx = {code: _build_fins_index(rows) for code, rows in fins.items()}
    out = []
    for (code, date), items in by_cd_items.items():
        by_date, by_pt = fins_idx.get(code, ({}, {}))
        good, bad = classify_event(items, by_date.get(date), by_pt)
        if good and bad:
            out.append({"code": code[:-1] if len(code) == 5 and code.endswith("0") else code,
                        "event_date": date, "good": sorted(good), "bad": sorted(bad),
                        "subpattern": f"{good[0]}_{bad[0]}"})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    print("[kouaku_official] /td/bulk + fins_summary 読み込み...")
    td = fetch_td_bulk()
    fins = json.loads(FINS_PATH.read_text())["by_code"]
    events = reconstruct(td, fins)
    atomic_write_json(args.out, {"records": events, "count": len(events)}, indent=0)
    # 既存 kouaku(実績ベースのみ)との (code,date) 再現率
    kou = json.loads(KOUAKU_PATH.read_text())["records"]
    actual_sp = {"genshu", "kouhou", "zouhai", "genhai", "jisha", "split"}
    existing = {(r["code"], r["event_date"]) for r in kou
                if any(p in actual_sp for p in (r.get("subpattern") or "").split("_"))}
    rebuilt = {(r["code"], r["event_date"]) for r in events}
    inter = existing & rebuilt
    print(f"[kouaku_official] 再構築 {len(events)}件 / 既存(実績系) {len(existing)}件")
    print(f"  一致(code,date): {len(inter)} = 既存の {100*len(inter)/max(len(existing),1):.0f}% を再現")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

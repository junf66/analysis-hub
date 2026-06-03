"""軽い減益決算 × 同日の好材料(増配/自社株買い/株式分割) イベントを公式データで構成する。

分類器の決算NP YoY±10%閾値で脱落していた「軽い減益(±10%以内)+好材料」を、自社株買い限定
(キッコーマン型)から全好材料に広げて n を増やし最終判定するためのデータ基盤。
- 軽い減益: /fins/summary の決算 NP YoY が (-10%, 0)。
- 好材料: 増配(同決算の DivAnn YoY ≥ +3%) / 自社株買い(/td/bulk DiscItems 11105 同日) /
  株式分割(11107 同日)。1つ以上あれば採用。
出力: data/edge_candidates/mild_good.json (返り値は returns 未付与、enrich は別段)。
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
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "mild_good.json"
JISHA_ITEM, SPLIT_ITEM = "11105", "11107"
DIV_UP = 3.0          # 増配判定閾値
NP_MILD_LO = -10.0    # 軽い減益の下限 (-10%, 0)


def fetch_td_gooditems() -> dict[tuple[str, str], set[str]]:
    """(code, DiscDate) → {自己株式/分割 のうち存在する DiscItems} を /td/bulk から構成。"""
    key = os.environ["JQUANTS_API_KEY"]
    meta = json.load(urllib.request.urlopen(
        urllib.request.Request(f"{_jquants.BASE_URL}/td/bulk", headers={"x-api-key": key}), timeout=60))
    txt = gzip.decompress(urllib.request.urlopen(meta["url"], timeout=180).read()).decode("utf-8", "ignore")
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in csv.DictReader(io.StringIO(txt)):
        items = set((r.get("DiscItems") or "").split("|"))
        good = items & {JISHA_ITEM, SPLIT_ITEM}
        if good:
            out[(r["Code"], r["DiscDate"])] |= good
    return out


def _yoy(by_pt: dict, pt: str, yr: str, field: str) -> float | None:
    try:
        cur, prev = by_pt[pt][yr][field], by_pt[pt][str(int(yr) - 1)][field]
    except (KeyError, ValueError):
        return None
    return (cur / prev - 1.0) * 100.0 if (cur is not None and prev) else None


def build_events(fins: dict[str, list], td_good: dict[tuple[str, str], set[str]]) -> list[dict[str, Any]]:
    """軽い減益決算 × 同日好材料 のイベントを返す。"""
    out = []
    for code, rows in fins.items():
        by_pt: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
        for r in rows:
            pt, pe = r.get("CurPerType"), (r.get("CurPerEn") or "")
            if pt and len(pe) >= 4:
                for f in ("NP", "DivAnn", "DivFY"):
                    v = r.get(f)
                    if v not in (None, ""):
                        try:
                            by_pt[pt][pe[:4]][f] = float(v)
                        except (TypeError, ValueError):
                            pass
        for r in rows:
            dd, pt, yr = r.get("DiscDate"), r.get("CurPerType"), (r.get("CurPerEn") or "")[:4]
            if not (dd and pt and yr.isdigit()):
                continue
            np_yoy = _yoy(by_pt, pt, yr, "NP")
            if np_yoy is None or not (NP_MILD_LO < np_yoy < 0):
                continue
            goods = []
            div_yoy = _yoy(by_pt, pt, yr, "DivAnn") or _yoy(by_pt, pt, yr, "DivFY")
            if div_yoy is not None and div_yoy >= DIV_UP:
                goods.append("zouhai")
            td_items = td_good.get((code, dd), set())
            code4 = code[:-1] if len(code) == 5 and code.endswith("0") else code
            if JISHA_ITEM in td_items:
                goods.append("jisha")
            if SPLIT_ITEM in td_items:
                goods.append("split")
            if goods:
                out.append({"code": code4, "event_date": dd, "source": "official",
                            "attrs": {"np_yoy": np_yoy, "div_yoy": div_yoy, "goods": goods,
                                      "disc_time": r.get("DiscTime")}})
    out.sort(key=lambda e: (e["event_date"], e["code"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    print("[mild_good] /td/bulk(好材料DiscItems) + fins 読み込み...")
    td_good = fetch_td_gooditems()
    fins = json.loads(FINS_PATH.read_text())["by_code"]
    events = build_events(fins, td_good)
    atomic_write_json(args.out, {"records": events, "count": len(events)}, indent=0)
    from collections import Counter
    gc = Counter(g for e in events for g in e["attrs"]["goods"])
    print(f"[mild_good] 軽い減益×好材料 {len(events)}件 / 好材料内訳 {dict(gc)}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

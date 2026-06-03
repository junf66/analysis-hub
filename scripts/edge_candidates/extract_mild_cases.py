"""extract_mild_good の対称ケース。軽い○○ × 反対材料(同日)を公式データ(fins+td)で構成する。

extract_mild_good は「軽い減益(NP YoY -10〜0) × 好材料」。これを fins+td で復元できる3ケースに横展開:
  - mild_bad   : 軽い増益(NP YoY 0〜+10) × 反対材料=減配(DivFY YoY≤-3)
  - mild_zouhai: 微増配(DivFY YoY 0〜+3) × 反対材料=深い減益(NP YoY≤-10)
  - mild_genhai: 微減配(DivFY YoY -3〜0) × 好材料=自社株買い(11105)/分割(11107)
スキーマは mild_good.json と同形 {records:[{code,event_date,source,attrs:{核心指標, 材料list, disc_time}}], count}。

※来期予想NP(NxFNp)は /fins/summary に無く /fins/details は403のため、mild_kahou_nx /
  mild_kouhou_nx(軽い来期○○帯)は公式データから復元不能=本スクリプト対象外(別途要相談)。
※特損/下方修正の単独DiscItemsコードは未知のため、反対材料は fins 数値で取れる減配/減益に限定。
出力: data/edge_candidates/{mild_bad,mild_zouhai,mild_genhai}.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json
from scripts.edge_candidates.extract_mild_good import fetch_td_gooditems, _yoy, JISHA_ITEM, SPLIT_ITEM

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FINS_PATH = REPO_ROOT / "data" / "edge_candidates" / "fins_summary.json"
OUT_DIR = REPO_ROOT / "data" / "edge_candidates"

DEEP_DOWN = -10.0   # 深い減益/来期下方の閾値
DIV_CUT = -3.0      # 減配閾値
DIV_UP = 3.0        # 増配閾値


def _fins_by_pt(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, float]]]:
    """code の決算行から (CurPerType→年→{NP,DivFY}) を構成。"""
    by_pt: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        pt, pe = r.get("CurPerType"), (r.get("CurPerEn") or "")
        if pt and len(pe) >= 4:
            for f in ("NP", "DivFY"):
                v = r.get(f)
                if v not in (None, ""):
                    try:
                        by_pt[pt][pe[:4]][f] = float(v)
                    except (TypeError, ValueError):
                        pass
    return by_pt


def build_events(fins: dict[str, list], td_good: dict[tuple[str, str], set[str]],
                 case: str) -> list[dict[str, Any]]:
    """case に応じた『軽い核心 × 反対材料』イベントを返す。"""
    out: list[dict[str, Any]] = []
    for code, rows in fins.items():
        by_pt = _fins_by_pt(rows)
        for r in rows:
            dd, pt, yr = r.get("DiscDate"), r.get("CurPerType"), (r.get("CurPerEn") or "")[:4]
            if not (dd and pt and yr.isdigit()):
                continue
            np_yoy = _yoy(by_pt, pt, yr, "NP")
            div_yoy = _yoy(by_pt, pt, yr, "DivFY")
            td_items = td_good.get((code, dd), set())
            code4 = code[:-1] if len(code) == 5 and code.endswith("0") else code
            attrs: dict[str, Any] | None = None

            if case == "mild_bad":
                # 軽い増益(0<NP<10) × 減配(DivFY≤-3)
                if np_yoy is not None and 0 < np_yoy < 10 and div_yoy is not None and div_yoy <= DIV_CUT:
                    attrs = {"np_yoy": np_yoy, "div_yoy": div_yoy, "bads": ["genhai"]}
            elif case == "mild_zouhai":
                # 微増配(0<DivFY<3) × 深い減益(NP≤-10)
                if div_yoy is not None and 0 < div_yoy < DIV_UP and np_yoy is not None and np_yoy <= DEEP_DOWN:
                    attrs = {"div_yoy": div_yoy, "np_yoy": np_yoy, "bads": ["genshu"]}
            elif case == "mild_genhai":
                # 微減配(-3<DivFY<0) × 好材料(自社株買い/分割)
                if div_yoy is not None and DIV_CUT < div_yoy < 0:
                    goods = []
                    if JISHA_ITEM in td_items:
                        goods.append("jisha")
                    if SPLIT_ITEM in td_items:
                        goods.append("split")
                    if goods:
                        attrs = {"div_yoy": div_yoy, "np_yoy": np_yoy, "goods": goods}
            if attrs is not None:
                attrs["disc_time"] = r.get("DiscTime")
                out.append({"code": code4, "event_date": dd, "source": "official", "attrs": attrs})
    out.sort(key=lambda e: (e["event_date"], e["code"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default="mild_bad,mild_zouhai,mild_genhai")
    args = ap.parse_args()
    print("[mild_cases] /td/bulk(好材料DiscItems) + fins 読み込み...")
    td_good = fetch_td_gooditems()
    fins = json.loads(FINS_PATH.read_text())["by_code"]
    for case in [c.strip() for c in args.cases.split(",") if c.strip()]:
        events = build_events(fins, td_good, case)
        out_path = OUT_DIR / f"{case}.json"
        atomic_write_json(out_path, {"records": events, "count": len(events)}, indent=0)
        mats = Counter(m for e in events for m in (e["attrs"].get("goods") or e["attrs"].get("bads") or []))
        print(f"[{case}] {len(events)}件 / 材料内訳 {dict(mats)} → {out_path}")


if __name__ == "__main__":
    main()

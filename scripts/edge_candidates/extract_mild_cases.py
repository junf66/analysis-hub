"""extract_mild_good の対称ケース。軽い○○ × 反対材料(同日)を公式データ(fins+td)で構成する。

extract_mild_good は「軽い減益(NP YoY -10〜0) × 好材料」。これを fins+td で復元できる3ケースに横展開:
  - mild_bad   : 軽い増益(NP YoY 0〜+10) × 反対材料=減配(DivFY YoY≤-3)
  - mild_zouhai: 微増配(DivFY YoY 0〜+3) × 反対材料=深い減益(NP YoY≤-10)
  - mild_genhai: 微減配(DivFY YoY -3〜0) × 好材料=自社株買い(11105)/分割(11107)
スキーマは mild_good.json と同形 {records:[{code,event_date,source,attrs:{核心指標, 材料list, disc_time}}], count}。

※来期予想NP(NxFNp)は /fins/summary に無く /fins/details は403のため、mild_kahou_nx /
  mild_kouhou_nx(軽い来期○○帯)は公式データから復元不能=本スクリプト対象外(別途要相談)。

反対材料の DiscItems コード(/td/bulk 759k件の Title 突合で実証, 2026-06):
  - 特別損失   = 11201(親会社・n7344, 約7割が特損/減損) / 12201(子会社・n434) → tag "tokuson"
  - 業績予想修正 = 11350/11351/11352/11353(family・n35,706)。**コードは方向非依存**で
    上方/下方は本文XBRL(契約外403)にしか無い。タイトルに「下方/減額/赤字」等が明示された
    ~7%のみ下方と判定可 → tag "kabu_geho"(タイトル明示の下方修正に限定)。
  fins 数値で取れる減配/減益(genhai/genshu)に加え、上記2系統を同日反対材料として付与する。
出力: data/edge_candidates/{mild_bad,mild_zouhai,mild_genhai}.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json
from scripts.edge_candidates.extract_mild_good import (
    fetch_td_gooditems, iter_td_bulk_rows, _yoy, JISHA_ITEM, SPLIT_ITEM,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FINS_PATH = REPO_ROOT / "data" / "edge_candidates" / "fins_summary.json"
OUT_DIR = REPO_ROOT / "data" / "edge_candidates"

DEEP_DOWN = -10.0   # 深い減益/来期下方の閾値
DIV_CUT = -3.0      # 減配閾値
DIV_UP = 3.0        # 増配閾値

# 反対材料の DiscItems コード (docstring 参照, /td/bulk 実証)
TOKUSON_ITEMS = {"11201", "12201"}                       # 特別損失の計上 → "tokuson"
REVISION_ITEMS = {"11350", "11351", "11352", "11353"}    # 業績予想の修正(方向非依存) → 下方のみ採用
# 業績予想修正のうちタイトルに下方が明示されたもの (本文XBRL無しでも判定できる安全側)
DOWN_TITLE_RE = re.compile(r"下方|減額|赤字|損失計上|損失の計上")


def fetch_td_baditems() -> dict[tuple[str, str], set[str]]:
    """(code, DiscDate) → {同日に開示された反対材料タグ} を /td/bulk から構成。

    - 特別損失(11201/12201) → "tokuson"
    - 業績予想修正(11350-family) かつ タイトルに下方が明示 → "kabu_geho"
      (コードは方向非依存のため、本文の取れない分はタイトル下方語のみ採用=安全側)
    """
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in iter_td_bulk_rows():
        items = set((r.get("DiscItems") or "").split("|"))
        tags: set[str] = set()
        if items & TOKUSON_ITEMS:
            tags.add("tokuson")
        if (items & REVISION_ITEMS) and DOWN_TITLE_RE.search(r.get("Title") or ""):
            tags.add("kabu_geho")
        if tags:
            out[(r["Code"], r["DiscDate"])] |= tags
    return out


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
                 case: str, td_bad: dict[tuple[str, str], set[str]] | None = None) -> list[dict[str, Any]]:
    """case に応じた『軽い核心 × 反対材料』イベントを返す。

    td_bad があれば同日開示の特損/下方修正(tokuson/kabu_geho)を bads に加点する
    (反対材料側の case=mild_bad / mild_zouhai のみ。fins 由来の減配/減益に追加)。
    """
    td_bad = td_bad or {}
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
            extra_bads = sorted(td_bad.get((code, dd), set()))
            code4 = code[:-1] if len(code) == 5 and code.endswith("0") else code
            attrs: dict[str, Any] | None = None

            if case == "mild_bad":
                # 軽い増益(0<NP<10) × 減配(DivFY≤-3)
                if np_yoy is not None and 0 < np_yoy < 10 and div_yoy is not None and div_yoy <= DIV_CUT:
                    attrs = {"np_yoy": np_yoy, "div_yoy": div_yoy, "bads": ["genhai"] + extra_bads}
            elif case == "mild_zouhai":
                # 微増配(0<DivFY<3) × 深い減益(NP≤-10)
                if div_yoy is not None and 0 < div_yoy < DIV_UP and np_yoy is not None and np_yoy <= DEEP_DOWN:
                    attrs = {"div_yoy": div_yoy, "np_yoy": np_yoy, "bads": ["genshu"] + extra_bads}
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
    print("[mild_cases] /td/bulk(好材料+反対材料DiscItems) + fins 読み込み...")
    td_good = fetch_td_gooditems()
    td_bad = fetch_td_baditems()
    fins = json.loads(FINS_PATH.read_text())["by_code"]
    for case in [c.strip() for c in args.cases.split(",") if c.strip()]:
        events = build_events(fins, td_good, case, td_bad)
        out_path = OUT_DIR / f"{case}.json"
        atomic_write_json(out_path, {"records": events, "count": len(events)}, indent=0)
        mats = Counter(m for e in events for m in (e["attrs"].get("goods") or e["attrs"].get("bads") or []))
        print(f"[{case}] {len(events)}件 / 材料内訳 {dict(mats)} → {out_path}")


if __name__ == "__main__":
    main()

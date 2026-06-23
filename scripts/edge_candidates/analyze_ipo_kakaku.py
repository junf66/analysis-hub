"""IPO 需要強度(公募価格/想定価格)の検証(恒久版)。

ブックビルディングで仮条件を想定より上に出す(公募>想定)=強い需要、下に切る(公募<想定)=弱い需要。
これが初値ポップの大きさを決めるかを全166銘柄(2024-26)で検証し、さらに1分スキャル(GU20-50)の
追加フィルタになるかを見る。

所見: (1)需要強度→初値の強さは確定級の単調関係(公募<想定+17%≪想定超え+41.5%/n166・分足不要)。
(2)だがGU20-50を要求した時点で需要は織り込み済み(弱ブックはGU20%に届かず脱落)=1分エッジの
独立フィルタにはならない。需要強度はGUの上流(原因)でGU(結果)を取れば十分。

データ: ipo_kakaku_data.py(想定価格・手動転記) + ipo_96ut_ratings.json(初値・GU) +
analyze_ipo_kyushu._rows()(分足結合)。公募価格 = 初値/(1+GU%)。

使い方: python -m scripts.edge_candidates.analyze_ipo_kakaku
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

from scripts._atomic import atomic_write_text
from scripts.edge_candidates.analyze_ipo_kyushu import _rows, COST
from scripts.edge_candidates.ipo_kakaku_data import IPO_SOTEI

REPO = Path(__file__).resolve().parent.parent.parent
RATINGS = REPO / "data" / "edge_candidates" / "ipo_96ut_ratings.json"


def _demand(hatsune: float, gu: float, code: str) -> float | None:
    """需要強度 = 公募価格/想定価格。公募 = 初値/(1+GU%)。"""
    if code not in IPO_SOTEI:
        return None
    return (hatsune / (1 + gu / 100)) / IPO_SOTEI[code]


def _grp(vals: list[float]) -> str:
    if len(vals) < 3:
        return f"n{len(vals)}"
    win = sum(1 for a in vals if a > 0) / len(vals) * 100
    return f"平均{st.fmean(vals):+.1f}%/中央{st.median(vals):+.1f}%/勝{win:.0f}/n{len(vals)}"


def _cell(vals: list[float]) -> str:
    v = [a - COST for a in vals if a is not None]
    if len(v) < 3:
        return f"n{len(v)}"
    win = sum(1 for a in v if a > 0) / len(v) * 100
    return f"{st.fmean(v):+.2f}%/勝{win:.0f}/n{len(v)}"


def build_report() -> str:
    """需要強度→初値の強さ(全166)と、GU20-50 1分スキャルの追加フィルタ可否を md で返す。"""
    recs = json.loads(RATINGS.read_text())["records"]
    rs = [(_demand(r["hatsune"], r["gu_pct"], r["code"]), r["gu_pct"])
          for r in recs if _demand(r["hatsune"], r["gu_pct"], r["code"]) is not None]
    lo = [g for d, g in rs if d < 0.98]
    mid = [g for d, g in rs if 0.98 <= d < 1.02]
    hi = [g for d, g in rs if d >= 1.02]
    rat = {r["code"]: r for r in recs}
    g = [x for x in _rows() if 20 < x["gu"] <= 50 and x["code"] in IPO_SOTEI]
    for x in g:
        r = rat[x["code"]]
        x["dem"] = (r["hatsune"] / (1 + r["gu_pct"] / 100)) / IPO_SOTEI[x["code"]]
    midk = [x for x in g if 10 <= x["ky"] < 100]
    L = ["# IPO 需要強度(公募/想定) 検証", "",
         f"想定価格 by code(手動転記) n={len(rs)}。公募 = 初値/(1+GU%)。", "",
         "## (1) 需要強度 → 初値騰落率 (全166・分足不要)", "",
         "| 区分 | 初値GU |", "|---|---|",
         f"| 公募<想定 (仮条件を下げた=弱ブック) | {_grp(lo)} |",
         f"| 公募≈想定 (0.98-1.02) | {_grp(mid)} |",
         f"| 公募>想定 (仮条件を上げた=強ブック) | {_grp(hi)} |", "",
         f"→ 公募<想定 平均初値{st.fmean(lo):+.1f}% vs それ以外{st.fmean(mid + hi):+.1f}% "
         f"(差{st.fmean(lo) - st.fmean(mid + hi):+.1f}%)。単調で大n=ブック需要が初値ポップの源泉。", "",
         "## (2) 需要強度 × GU20-50 → 1分スキャル (分足・追加フィルタ可否)", "",
         f"- GU20-50 全: {_cell([x['r1'] for x in g])}",
         f"  - 公募<想定: {_cell([x['r1'] for x in g if x['dem'] < 0.98])}",
         f"  - 公募≈想定: {_cell([x['r1'] for x in g if 0.98 <= x['dem'] < 1.02])}",
         f"  - 公募>想定: {_cell([x['r1'] for x in g if x['dem'] >= 1.02])}",
         f"- 中吸×GU20-50 全: {_cell([x['r1'] for x in midk])}",
         f"  - 公募>=想定: {_cell([x['r1'] for x in midk if x['dem'] >= 0.98])}",
         f"  - 公募<想定: {_cell([x['r1'] for x in midk if x['dem'] < 0.98])}", "",
         "## 結論", "",
         "- 需要強度→初値の強さは確定級の単調関係(公募割れ+17%≪想定超え+41.5%)。だが**取引可能なのは初値ではない**。",
         "- GU20-50を要求した時点で弱ブック(公募<想定)はGU20%に届かず脱落済み(GU20-50内はほぼ全部 公募≥想定・n1)。",
         "- ＝需要強度はGU(初値ポップ=結果)の上流(原因)。**GUを使えば需要は織り込み済み=独立フィルタにならない**。",
         "- 機構確証としての価値: IPO1分エッジの正体は『ブック需要の強さを初値ポップ経由で取る』(過剰最適化でない裏付け)。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "reports" / "ipo_kakaku.md")
    args = ap.parse_args()
    rep = build_report()
    print(rep)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, rep)
    print(f"[ipo_kakaku] → {args.out}")


if __name__ == "__main__":
    main()

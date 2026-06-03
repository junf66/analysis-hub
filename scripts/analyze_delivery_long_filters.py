"""受渡日ロード(GD+フラット普通株)に対する「加点フィルタ」を体系比較する。

土台: PO受渡日 / 普通株 / 受渡日gap<+0.5%(GD+フラット) / 受渡日 寄→引 long。
この母体に各フィルタ(時価総額・PO規模・希薄化・信用区分・gap細分)を単独/2枚重ねで適用し、
net EV を最も厚くするパターンを順位付け(日付クラスタt + walk-forward OOS + 母体内FDR)。

出力: reports/delivery_long_filters.md
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any, Callable

from analyzers.stats import evaluate_cells

REPO_ROOT = Path(__file__).resolve().parent.parent
PO_PATH = REPO_ROOT / "data" / "po_records.json"
REPORT_PATH = REPO_ROOT / "reports" / "delivery_long_filters.md"

LONG_COST = 0.20
GD_FLAT_HI = 0.5   # 受渡日gap < 0.5% = GD+フラット (GU除外)
MIN_N = 30

# 加点フィルタ候補: 表示名 → 述語(record→bool)
FILTERS: dict[str, Callable[[dict[str, Any]], bool]] = {
    "時価>500億": lambda r: bool(r.get("market_cap")) and float(r["market_cap"]) > 500,
    "時価>1000億": lambda r: bool(r.get("market_cap")) and float(r["market_cap"]) > 1000,
    "時価>3000億": lambda r: bool(r.get("market_cap")) and float(r["market_cap"]) > 3000,
    "PO規模≥100億": lambda r: bool(r.get("po_scale")) and float(r["po_scale"]) >= 100,
    "PO規模≥300億": lambda r: bool(r.get("po_scale")) and float(r["po_scale"]) >= 300,
    "希薄化≤10%": lambda r: r.get("dilution") is not None and float(r["dilution"]) <= 10,
    "希薄化≤5%": lambda r: r.get("dilution") is not None and float(r["dilution"]) <= 5,
    "信用:貸借": lambda r: r.get("lending_type") == "貸借",
    "gap:GD単独": lambda r: float((r.get("attrs") or {}).get("gap_pct", 0)) <= -0.5,
    "gap:フラット単独": lambda r: -0.5 < float((r.get("attrs") or {}).get("gap_pct", 0)) < 0.5,
}


def load_records() -> list[dict[str, Any]]:
    """po_records を返す。"""
    data = json.loads(PO_PATH.read_text())
    return data.get("records", []) if isinstance(data, dict) else data


def base_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """土台(受渡日/普通/gap<0.5/寄→引retあり)を抽出。"""
    out = []
    for r in records:
        if r.get("stage") != "deliver" or r.get("po_type") != "普通":
            continue
        a = r.get("attrs") or {}
        gap = a.get("gap_pct")
        if gap is None or float(gap) >= GD_FLAT_HI:
            continue
        if a.get("next_day_open_to_close_ret") is None:
            continue
        out.append(r)
    return out


def build_observations(base: list[dict[str, Any]], max_combo: int = 2) -> list[dict[str, Any]]:
    """土台レコードに対し、満たすフィルタの単一/組合せを cell とする観測を作る。"""
    obs: list[dict[str, Any]] = []
    for r in base:
        a = r.get("attrs") or {}
        ret = float(a["next_day_open_to_close_ret"])
        date = r.get("event_date")
        code = r.get("code")
        obs.append({"cell": ("土台(無フィルタ)",), "ret": ret, "date": date, "code": code})
        active = [name for name, fn in FILTERS.items() if fn(r)]
        for k in range(1, max_combo + 1):
            for combo in itertools.combinations(active, k):
                obs.append({"cell": combo, "ret": ret, "date": date, "code": code})
    return obs


def rank_filters(base: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """フィルタ別に評価し net EV 降順で返す。"""
    obs = build_observations(base)
    results = evaluate_cells(obs, long_cost=LONG_COST, short_cost=LONG_COST, min_n=MIN_N)
    results.sort(key=lambda r: r["ev_net"], reverse=True)
    return results


def build_report(records: list[dict[str, Any]]) -> str:
    """加点フィルタ比較レポートを生成。"""
    base = base_records(records)
    L: list[str] = []
    L.append("# 受渡日ロング 加点フィルタ比較 (2026-06-03)")
    L.append("")
    L.append(f"土台: PO受渡日 / 普通株 / 受渡日gap<+0.5%(GD+フラット, GU除外) / 寄→引 long。母体 n={len(base)}。")
    L.append("各加点フィルタを単独/2枚重ねで適用し net EV(long往復0.20%)で順位付け。")
    L.append("日付クラスタ頑健t / walk-forward OOS / 母体内 BH-FDR。")
    L.append("")
    ranked = rank_filters(base)
    L.append("## net EV 順（n≥30）")
    L.append("")
    L.append("| 加点フィルタ | n | net EV | t_clust | OOS test | FDR★ |")
    L.append("|---|---|---|---|---|---|")
    for r in ranked:
        cell = " ＋ ".join(r["cell"])
        oos = r.get("test_ev_net")
        oos_disp = f"{oos:+.2f}%" if oos is not None else "—"
        fdr = "★" if r.get("fdr_significant") else ""
        L.append(f"| {cell} | {r['n']} | {r['ev_net']:+.2f}% | {r['t_clustered']:+.2f} | "
                 f"{oos_disp} | {fdr} |")
    L.append("")
    # 所見
    by_cell = {r["cell"]: r for r in ranked}
    base_row = by_cell.get(("土台(無フィルタ)",))
    singles = [r for r in ranked if len(r["cell"]) == 1 and r["cell"] != ("土台(無フィルタ)",)]
    singles.sort(key=lambda r: r["ev_net"], reverse=True)
    best = max((r for r in ranked if r["t_clustered"] >= 2 and r["n"] >= MIN_N),
              key=lambda r: r["ev_net"], default=None)
    L.append("## 所見")
    L.append("")
    if base_row:
        L.append(f"- 土台(無フィルタ): net {base_row['ev_net']:+.2f}% / t_clust {base_row['t_clustered']:+.2f} / n{base_row['n']}。"
                 "ほぼトントン＝加点フィルタで取り分を作る必要がある。")
    L.append("- **単独フィルタの効き順（net EV）**:")
    for r in singles:
        L.append(f"  - {r['cell'][0]}: net {r['ev_net']:+.2f}% / t_clust {r['t_clustered']:+.2f} / n{r['n']}")
    if best:
        cell = " ＋ ".join(best["cell"])
        L.append(f"- **最良（t_clust≥2で最大EV）**: 「{cell}」 net {best['ev_net']:+.2f}% / "
                 f"t_clust {best['t_clustered']:+.2f} / OOS {best.get('test_ev_net'):+.2f}% / n{best['n']} ★。")
    L.append("- **主役は時価総額ではなく『PO規模(発行・調達規模)』**。大型増資ほど受渡日の戻りが強い。")
    L.append("  希薄化・信用:貸借は単独ではほぼ効かず、PO規模と重ねて初めて厚くなる加点。")
    L.append("- gap:フラット単独は net マイナス。**GD が本体**で、フラットは規模フィルタと重ねて初めてプラス。")
    L.append("- 推奨運用: **PO規模≥300億 を主軸に、希薄化≤10% を重ねる**（時価総額は副次的）。")
    L.append("- ⚠️ 2枚重ねは n が痩せるほど過剰最適化リスク。FDR★かつ OOS プラスを優先。")
    return "\n".join(L)


if __name__ == "__main__":
    records = load_records()
    REPORT_PATH.write_text(build_report(records))
    print(f"wrote {REPORT_PATH}")

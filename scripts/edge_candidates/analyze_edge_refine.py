"""確定エッジ ④/②/①B のシャープ化（銘柄属性での効きどころ特定）。

既存の確定3エッジを、保有・方向はそのままに『どの銘柄で厚く張るか』を属性層別で詰める:
  ④ 増配+来期下方ショート (kouaku zouhai_kahou_nx 翌寄→引): GU/GD帯 × 規模。
  ② REIT事前売り (po decide リート 翌寄→決定日引け short): 時価総額 × 調達規模 × 希薄化。
  ①B 中型PO・GD買い (po announce 普通 中型 GD 翌寄→引 long): GD深さ × 調達規模 × 希薄化。

返りは lib._exit_stats を再利用（ショートは負号メトリクスを注入して net=-ret-cost に）。
方向別 net cost（short 0.15% / long 0.20%）・event_date クラスタ頑健t・各軸内 BH-FDR。

出力: reports/edge_refine.md
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from analyzers.stats import benjamini_hochberg
from scripts._atomic import atomic_write_text
from scripts.edge_candidates import lib

REPO = Path(__file__).resolve().parent.parent.parent
KOUAKU = REPO / "data" / "kouaku_records.json"
PO = REPO / "data" / "po_records.json"
ENR = REPO / "data" / "edge_candidates" / "po_enriched.json"
MASTER = REPO / "data" / "edge_candidates" / "equities_master.json"
OUT = REPO / "reports" / "edge_refine.md"

SHORT_COST = 0.15
LONG_COST = 0.20
MIN_N = 20


def _c5(code: str) -> str:
    code = str(code)
    return code if len(code) == 5 else code + "0"


def _bucketed(records: list[dict], metric: str, cost: float,
              keyfn: Callable[[dict], str], order: list[str] | None) -> list[tuple[str, dict]]:
    """records を keyfn で層別→各セル _exit_stats→セル横断 BH-FDR。表示順 order。"""
    grp: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        k = keyfn(r)
        if k is not None:
            grp[k].append(r)
    cells = []
    for k, recs in grp.items():
        s = lib._exit_stats(recs, metric, cost)
        if s and s["n"] >= MIN_N:
            s["key"] = k
            cells.append(s)
    if cells:
        for s, f in zip(cells, benjamini_hochberg([s["p"] for s in cells], 0.05)):
            s["fdr_significant"] = f
    keys = order or sorted(grp)
    return [(k, next((s for s in cells if s["key"] == k), None)) for k in keys]


def _rows(title: str, cells: list[tuple[str, dict]]) -> list[str]:
    L = [f"### {title}", "", "| 区分 | n | net EV | t_clust | 勝率 | OOS | FDR |", "|---|--:|--:|--:|--:|--:|:-:|"]
    for k, s in cells:
        if not s:
            L.append(f"| {k} | <{MIN_N} | — | — | — | — | |")
            continue
        oos = s["oos"] if s["oos"] is not None else 0.0
        L.append(f"| {k} | {s['n']} | {s['net_ev']:+.2f}% | {s['t_clust']:+.2f} | "
                 f"{s['win']:.0f}% | {oos:+.2f}% | {'★' if s.get('fdr_significant') else ''} |")
    L.append("")
    return L


# ---- ④ kouaku zouhai_kahou_nx short -----------------------------------------

def _gap_band(g) -> str | None:
    if g is None:
        return None
    if g <= -3:
        return "GD大(≤-3%)"
    if g < -0.5:
        return "GD(-3〜-0.5%)"
    if g <= 0.5:
        return "フラット(±0.5%)"
    if g < 3:
        return "GU(0.5〜3%)"
    return "GU大(≥3%)"


def section_zouhai(master: dict) -> list[str]:
    """④ 増配+来期下方ショートを 翌寄りGU/GD帯 × 規模 で層別した節を返す。"""
    recs = json.loads(KOUAKU.read_text())["records"]
    z = [r for r in recs if r.get("subpattern") == "zouhai_kahou_nx"]
    # ショート net = -(翌寄→引) - cost。負号メトリクスを注入。
    for r in z:
        a = r.get("attrs") or {}
        v = a.get("next_day_open_to_close_ret")
        a["_short_oc"] = -v if v is not None else None
        r["attrs"] = a
    full = lib._exit_stats(z, "_short_oc", SHORT_COST)
    L = ["## ④ 増配+来期下方ショート（翌寄→引・short net 0.15%）", "",
         f"母体 zouhai_kahou_nx {len(z)}件。全体: net{full['net_ev']:+.2f}% / t{full['t_clust']:+.2f} / "
         f"勝{full['win']:.0f}% / n{full['n']}。", ""]
    L += _rows("翌寄りGU/GD帯（前日終値比）",
               _bucketed(z, "_short_oc", SHORT_COST, lambda r: _gap_band((r.get("attrs") or {}).get("gap_pct")),
                         ["GD大(≤-3%)", "GD(-3〜-0.5%)", "フラット(±0.5%)", "GU(0.5〜3%)", "GU大(≥3%)"]))
    L += _rows("規模(equities_master)",
               _bucketed(z, "_short_oc", SHORT_COST,
                         lambda r: master.get(_c5(r["code"]), {}).get("scale_band"),
                         ["大型", "中型", "小型"]))
    return L


# ---- ② REIT decide short ----------------------------------------------------

def _mcap_band(v) -> str | None:
    if v is None:
        return None
    return "時価<500億" if v < 500 else "500-1500億" if v < 1500 else "≥1500億"


def _scale_band(v) -> str | None:
    if v is None:
        return None
    return "調達<100億" if v < 100 else "100-200億" if v < 200 else "≥200億"


def _dil_band(v) -> str | None:
    if v is None:
        return None
    return "希薄<10%" if v < 10 else "10-15%" if v < 15 else "≥15%"


def section_reit() -> list[str]:
    """② REIT事前売りを 時価総額 × 調達規模 × 希薄化 で層別した節を返す。"""
    recs = json.loads(PO.read_text())["records"]
    reit = [r for r in recs if r.get("stage") == "decide" and r.get("po_type") == "リート"]
    for r in reit:
        a = r.get("attrs") or {}
        v = a.get("ret_close")
        a["_short_rc"] = -v if v is not None else None
        r["attrs"] = a
    full = lib._exit_stats(reit, "_short_rc", SHORT_COST)
    L = ["## ② REIT事前売り（翌寄→決定日引け・short net 0.15%）", "",
         f"母体 REIT decide {len(reit)}件。全体: net{full['net_ev']:+.2f}% / t{full['t_clust']:+.2f} / "
         f"勝{full['win']:.0f}% / n{full['n']}。", ""]
    L += _rows("時価総額", _bucketed(reit, "_short_rc", SHORT_COST,
               lambda r: _mcap_band(r.get("market_cap")), ["時価<500億", "500-1500億", "≥1500億"]))
    L += _rows("調達規模(po_scale)", _bucketed(reit, "_short_rc", SHORT_COST,
               lambda r: _scale_band(r.get("po_scale")), ["調達<100億", "100-200億", "≥200億"]))
    L += _rows("希薄化(dilution)", _bucketed(reit, "_short_rc", SHORT_COST,
               lambda r: _dil_band(r.get("dilution")), ["希薄<10%", "10-15%", "≥15%"]))
    return L


# ---- ①B 中型 announce GD long -----------------------------------------------

def _gddepth(g) -> str | None:
    if g is None or g > -0.5:
        return None
    return "GD浅(-0.5〜-3%)" if g > -3 else "GD中(-3〜-8%)" if g > -8 else "GD深(≤-8%)"


def section_po_long(enr: dict) -> list[str]:
    """①B 中型PO・GD買いを GD深さ × 調達規模 × 希薄化 で層別した節を返す。"""
    recs = json.loads(PO.read_text())["records"]
    ann = []
    for r in recs:
        if r.get("stage") != "announce" or r.get("po_type") != "普通":
            continue
        e = enr.get(r["id"]) or {}
        if e.get("scale_band") != "中型":
            continue
        a = r.get("attrs") or {}
        if e.get("next_day_open_to_close_ret") is not None:
            a["next_day_open_to_close_ret"] = e["next_day_open_to_close_ret"]
        r["attrs"] = a
        ann.append(r)
    gd = [r for r in ann if (r.get("attrs") or {}).get("gap_pct") is not None
          and r["attrs"]["gap_pct"] <= -0.5]
    full = lib._exit_stats(gd, "next_day_open_to_close_ret", LONG_COST)
    L = ["## ①B 中型PO・GD買い（翌寄→引・long net 0.20%）", "",
         f"母体 中型announce普通 {len(ann)}件 / うちGD(≤-0.5%) {len(gd)}件。"
         f"GD全体: net{full['net_ev']:+.2f}% / t{full['t_clust']:+.2f} / 勝{full['win']:.0f}% / n{full['n']}。", ""]
    L += _rows("GD深さ", _bucketed(gd, "next_day_open_to_close_ret", LONG_COST,
               lambda r: _gddepth((r.get("attrs") or {}).get("gap_pct")),
               ["GD浅(-0.5〜-3%)", "GD中(-3〜-8%)", "GD深(≤-8%)"]))
    L += _rows("調達規模(po_scale)", _bucketed(gd, "next_day_open_to_close_ret", LONG_COST,
               lambda r: _scale_band(r.get("po_scale")), ["調達<100億", "100-200億", "≥200億"]))
    L += _rows("希薄化(dilution)", _bucketed(gd, "next_day_open_to_close_ret", LONG_COST,
               lambda r: _dil_band(r.get("dilution")), ["希薄<10%", "10-15%", "≥15%"]))
    return L


def report() -> str:
    """④/②/①B のシャープ化レポート(Markdown)を返す。"""
    master = {str(r["Code"]): r for r in json.loads(MASTER.read_text())["records"]}
    enr = json.loads(ENR.read_text())["by_id"]
    L = ["# 確定エッジ ④/②/①B シャープ化（属性層別）", "",
         "保有・方向は不変、『どの銘柄で厚く張るか』を属性で層別。short net 0.15% / long net 0.20%・",
         "event_dateクラスタ頑健t・各軸内BH-FDR。各軸は多重検定ゆえ単調性/勾配を重視（単一セル過信を避ける）。", ""]
    L += section_zouhai(master)
    L += section_reit()
    L += section_po_long(enr)
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    body = report()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, body)
    print(body)
    print(f"[edge_refine] → {args.out}")


if __name__ == "__main__":
    main()

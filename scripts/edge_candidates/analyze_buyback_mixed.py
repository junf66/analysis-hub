"""仕様A: 好悪混在×自社株買い 同時発表 ロングエッジ 本検証。

「決算が好悪混在 (増配 or 黒字化 = 好 ＋ 減益 or 減収 = 悪)」かつ「同日に自社株買い決定」を
全期間 (TDnet 2021-06〜) 抽出し、自社株買いの下値支えが通常の sell-the-news を覆すかを検証。

好悪フラグ (公式 /fins/summary で測れる範囲):
  好 = 増配 (DivFY YoY>0) / 黒字化 (前期NP<0 → 当期NP>0)
  悪 = 減益 (NP YoY<0) / 減収 (Sales YoY<0)
  ※上方/下方修正は予想値が /fins/summary に無く /fins/details 契約外(403)のため対象外 (留保)。

出口: 反応日(開示翌取引日)寄り起点。d0=寄→引(raw) / +1/+3/+5日引(TOPIX β=1 超過α)。ロング0.20%控除。
3段ガード: BH-FDR ＋ 日付クラスタ頑健t ＋ 非重複の正直t ＋ walk-forward OOS ＋ PITユニバース。
層化: 規模(PIT) / 寄り型(GU・フラット・GD) / 好悪の構成。

出力: reports/buyback_mixed_news.md
使い方: python -m scripts.edge_candidates.analyze_buyback_mixed
"""
from __future__ import annotations

import argparse
import datetime
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_text
from scripts.edge_candidates import event_combo_lib as ec

OUT_PATH = ec.REPO_ROOT / "reports" / "buyback_mixed_news.md"
_BUYBACK_KW = ("自己株式の取得", "自己株式取得")
_YOY_FIELDS = ["NP", "OP", "Sales", "DivFY"]


def _fins_index(rows: list[dict]) -> tuple[dict, dict]:
    """(DiscDate→決算行, (CurPerType, 年)→{field:val}) を返す。"""
    by_date: dict[str, dict] = {}
    by_pt: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for r in rows:
        dd, pt, pe = r.get("DiscDate"), r.get("CurPerType"), (r.get("CurPerEn") or "")
        if dd:
            by_date[dd] = r
        if pt and len(pe) >= 4:
            for f in _YOY_FIELDS:
                v = r.get(f)
                if v not in (None, ""):
                    try:
                        by_pt[(pt, pe[:4])][f] = float(v)
                    except (TypeError, ValueError):
                        pass
    return by_date, by_pt


def goodbad(by_date: dict, by_pt: dict, disc_date: str) -> dict[str, Any] | None:
    """disc_date の決算から好/悪フラグ・YoY を判定。決算行が無ければ None。"""
    row = by_date.get(disc_date)
    if row is None:
        return None
    pt, yr = row.get("CurPerType"), (row.get("CurPerEn") or "")[:4]
    if not (pt and yr.isdigit()):
        return None
    cur, prev = by_pt.get((pt, yr), {}), by_pt.get((pt, str(int(yr) - 1)), {})

    def yoy(f: str) -> float | None:
        c, p = cur.get(f), prev.get(f)
        return (c / p - 1.0) * 100.0 if (c is not None and p) else None

    np_yoy, sales_yoy = yoy("NP"), yoy("Sales")
    div_yoy = yoy("DivFY")
    np_cur, np_prev = cur.get("NP"), prev.get("NP")
    kuroji = (np_prev is not None and np_prev < 0 and np_cur is not None and np_cur > 0)
    good, bad = [], []
    if div_yoy is not None and div_yoy > 0:
        good.append("増配")
    if kuroji:
        good.append("黒字化")
    if np_yoy is not None and np_yoy < 0:
        bad.append("減益")
    if sales_yoy is not None and sales_yoy < 0:
        bad.append("減収")
    return {"np_yoy": np_yoy, "sales_yoy": sales_yoy, "div_yoy": div_yoy,
            "kuroji": kuroji, "good": good, "bad": bad,
            "mixed": bool(good) and bool(bad)}


def extract_events() -> list[dict[str, Any]]:
    """好悪混在×自社株買い同日決算イベントを抽出 (PIT属性付与済)。"""
    rows = ec.load_tdnet_rows()
    fins = ec.load_fins_by_code()
    mh = ec.load_master_history()
    # 自社株買い決定 と 決算短信 の同日 (code,date)
    buy_cd: dict[tuple[str, str], str] = {}
    kessan_cd: set[tuple[str, str]] = set()
    for r in rows:
        c, d, t = r["code"], r["date"], r["title"]
        if not c or not d:
            continue
        if any(k in t for k in _BUYBACK_KW) and ("決定" in t or "取得" in t):
            buy_cd.setdefault((c, d), r["time"])
        if "決算短信" in t:
            kessan_cd.add((c, d))
    fins_idx: dict[str, tuple[dict, dict]] = {}
    out: list[dict[str, Any]] = []
    for (code, date), dtime in sorted(buy_cd.items()):
        if (code, date) not in kessan_cd:
            continue
        c5 = ec.code5(code)
        if c5 not in fins_idx:
            fins_idx[c5] = _fins_index(fins.get(c5, []))
        gb = goodbad(*fins_idx[c5], date)
        if gb is None or not gb["mixed"]:
            continue
        pit = ec.pit_attrs(mh, code, date)
        out.append({"code": code, "event_date": date,
                    "attrs": {"disc_time": dtime, **gb, **pit}})
    return out


def _cells_for(events: list[dict], direction: str = "long") -> dict[str, dict]:
    """出口グリッドの stats を返し、FDR を出口横断で適用。"""
    out: dict[str, dict] = {}
    flat = []
    for label, metric, hold in ec.EXITS_LONG:
        s = ec.directional_stats(events, metric, direction, ec.LONG_COST, hold_days=hold)
        if s:
            out[label] = s
            flat.append(s)
    ec.apply_fdr(flat)
    return out


def _table(cells: dict[str, dict]) -> list[str]:
    L = ["| 出口 | n | 独立n | net EV | t_clust | 正直t | 勝率 | OOS | FDR | 判定 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for label, _m, _h in ec.EXITS_LONG:
        s = cells.get(label)
        if not s:
            continue
        mark = "★" if s.get("fdr_significant") else ""
        oos = s["oos"] if s["oos"] is not None else 0.0
        L.append(f"| {label} | {s['n']} | {s['n_indep']} | {s['net_ev']:+.2f}% | {s['t_clust']:+.2f} | "
                 f"{s['honest_t']:+.2f} | {s['win']:.0f}% | {oos:+.2f}% | {mark} | {ec.verdict(s)} |")
    return L


def build_report(events: list[dict]) -> str:
    """仕様A 検証レポートを Markdown で返す。"""
    dates = [e["event_date"] for e in events]
    n_band = {}
    for e in events:
        for g in e["attrs"]["good"]:
            for b in e["attrs"]["bad"]:
                n_band[f"{g}×{b}"] = n_band.get(f"{g}×{b}", 0) + 1
    L = [f"# 仕様A: 好悪混在×自社株買い 同時発表 ロング 本検証 ({datetime.date.today()})", "",
         f"対象 **{len(events)}件** ({min(dates)}〜{max(dates)})。好悪混在=(増配 or 黒字化) かつ (減益 or 減収)、"
         "同日に自社株買い決定。", "",
         "出口=反応日(開示翌取引日)寄り起点。d0=寄→引(raw) / +N日=TOPIX β=1 超過α。ロング往復0.20%控除。",
         "3段ガード: BH-FDR ＋ 日付クラスタ頑健t ＋ **非重複の正直t** ＋ walk-forward OOS ＋ PITユニバース。",
         "★通過=net>0.5% & t_clust>2 & 正直t>2 & FDR生存 & OOS>0。", "",
         "問い: 好材料ロングは既往で系統的マイナス(sell-the-news)。**自社株買いの下値支えがそれを覆すか**。", "",
         "## 0. 好悪の構成内訳", "",
         "| 構成 | n |", "|---|---|"]
    for k, v in sorted(n_band.items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v} |")
    L += ["", "## 1. 全体 (好悪混在 全件)", ""]
    L += _table(_cells_for(events))
    L += ["", "## 2. 規模別 (PIT)", ""]
    for band in ["大型", "中型", "小型"]:
        sub = [e for e in events if e["attrs"].get("scale_band") == band]
        if len(sub) < ec.MIN_N:
            L += [f"### {band} (n={len(sub)} <30 → 参考)", ""] if sub else []
            if not sub:
                continue
        else:
            L += [f"### {band} (n={len(sub)})", ""]
        L += _table(_cells_for(sub))
        L += [""]
    L += ["## 3. 寄り型別 (反応日ギャップ)", ""]
    for gb in ["GU(>+3%)", "フラット", "GD(<-3%)"]:
        sub = [e for e in events if ec.gap_bucket(e["attrs"].get("gap")) == gb]
        if not sub:
            continue
        L += [f"### {gb} (n={len(sub)})", ""]
        L += _table(_cells_for(sub))
        L += [""]
    L += ["## 4. 好悪の構成別 (増配×減益 / 黒字化×減収 等)", ""]
    for comp in sorted(n_band, key=lambda x: -n_band[x]):
        g, b = comp.split("×")
        sub = [e for e in events if g in e["attrs"]["good"] and b in e["attrs"]["bad"]]
        if len(sub) < ec.MIN_N:
            continue
        L += [f"### {comp} (n={len(sub)})", ""]
        L += _table(_cells_for(sub))
        L += [""]
    L += ["## 判定メモ", "",
          "- 上方/下方修正は予想値が公式APIに無く(/fins/details 403)、好悪フラグから除外=留保。",
          "- d0(寄→引)はraw、+N日はTOPIX超過α。約定: 開示翌取引日寄りで entry(大引け後開示でも可能)。",
          "- 自社株買いの支えで sell-the-news が覆るなら d0〜+5日αが net 正＋t>2＋FDR生存で出るはず。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    print("[buyback_mixed] イベント抽出中...")
    events = extract_events()
    print(f"[buyback_mixed] 好悪混在×自社株買い {len(events)}件。リターン付与中...")
    ebars = ec.load_event_bars()
    got = ec.enrich_returns(events, ebars)
    print(f"[buyback_mixed] d0_ret付与 {got}/{len(events)}。レポート生成中...")
    rep = build_report(events)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, rep)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

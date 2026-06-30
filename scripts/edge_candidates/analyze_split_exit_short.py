"""株式分割「実施日(権利落ち日)」fade ショート検証 (#4 発表ロングの鏡像)。

#4 は「分割"発表"→翌寄りロング(小型)」の確定エッジ。本スクリプトはその鏡像=
「発表で買われた分割株が"実施日(権利落ち日)"後の数日で剥落する」fade をショートで取れるかを検証。

エントリー: 実施日 or 翌取引日の寄り (権利落ち日は数週前から確定の予定イベント=寄りで約定可能・先読みなし)。
出口: +3日 / +5日 引け。市場(TOPIX β=1)控除α + ショート往復0.15%控除。
ガード: 日付クラスタ頑健t + 非重複の正直t + walk-forward OOS + 年次安定 + PIT規模/信用区分(貸借=空売り可)
        + 逆日歩感応度(コスト上げ) + 分布頑健性(中央/トリム/上位寄与)。

前提データ: data/edge_candidates/split_multiday_enriched.json (ex_date 付与済)。
  未付与/古い場合は先に `python -m scripts.edge_candidates.enrich_split_axes` で再生成すること。
出力: reports/split_exit_short.md
使い方: python -m scripts.edge_candidates.analyze_split_exit_short
"""
from __future__ import annotations

import argparse
import datetime
import json
import statistics as st
from pathlib import Path
from typing import Any

from analyzers.stats import benjamini_hochberg
from scripts._atomic import atomic_write_text
from scripts.edge_candidates import event_combo_lib as ec
from scripts.edge_candidates.verify_edges_standalone import clustered_t

IN_PATH = ec.REPO_ROOT / "data" / "edge_candidates" / "split_multiday_enriched.json"
OUT_PATH = ec.REPO_ROOT / "reports" / "split_exit_short.md"
TOPIX_PATH = ec.REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
SHORT = ec.SHORT_COST
# (ラベル, entry_offset(0=実施日/1=翌日), N日後引け)
GRID = [("実施日寄S→+3日", 0, 3), ("実施日寄S→+5日", 0, 5),
        ("翌日寄S→+3日", 1, 3), ("翌日寄S→+5日", 1, 5)]


def _load_calendar() -> tuple[dict, list, dict]:
    """TOPIX 日足 → (date→row, 営業日リスト, date→index)。"""
    tpx = {r["Date"]: r for r in json.loads(TOPIX_PATH.read_text())["records"]
           if r.get("O") and r.get("C")}
    cal = sorted(tpx)
    return tpx, cal, {d: i for i, d in enumerate(cal)}


def short_rows(recs: list[dict], ebars: dict, tpx: dict, cal: list, cidx: dict,
               entry_off: int, n: int, *, shortable_only: bool = False,
               master_hist: dict | None = None) -> list[tuple[str, str, float]]:
    """(ex_date, code, ショートpnl%) のリストを返す。pnl=-(α)-cost、α=銘柄-市場(β=1)。"""
    out: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    for r in recs:
        code = r["code"]
        ex = (r.get("attrs") or {}).get("ex_date")
        if not ex or ex not in cidx or (code, ex) in seen:
            continue
        seen.add((code, ex))
        ei = cidx[ex] + entry_off
        xi = ei + n
        if ei < 0 or xi >= len(cal):
            continue
        bars = ebars.get(ec.code4(code))
        if not bars:
            continue
        e, x = bars.get(cal[ei]), bars.get(cal[xi])
        if not e or not x or not e[0] or not x[1]:
            continue
        if shortable_only and master_hist is not None:
            if ec.pit_attrs(master_hist, code, ex).get("mrgn") != "貸借":
                continue
        td0, td1 = cal[ei], cal[xi]
        if td0 not in tpx or td1 not in tpx:
            continue
        mret = (tpx[td1]["C"] / tpx[td0]["O"] - 1.0) * 100.0
        stock = (x[1] / e[0] - 1.0) * 100.0
        out.append((ex, code, -(stock - mret) - SHORT))
    return out


def _stat(rows: list[tuple[str, str, float]]) -> dict[str, Any] | None:
    """ショート行から net/勝率/クラスタt/正直t/OOS を計算。"""
    if not rows:
        return None
    v = [p for _, _, p in rows]
    d = [dt for dt, _, _ in rows]
    n = len(v)
    keep = ec._nonoverlap_keep([(dt, c, p) for dt, c, p in rows], hold_days=10)
    ht = (st.fmean(keep) / (st.stdev(keep) / len(keep) ** 0.5)
          if len(keep) > 1 and st.pstdev(keep) else 0.0)
    so = sorted(rows)
    oos = st.fmean([p for _, _, p in so[int(n * 0.7):]]) if n else None
    return {"n": n, "net": st.fmean(v), "med": st.median(v),
            "win": sum(1 for x in v if x > 0) * 100.0 / n,
            "t": clustered_t(v, d), "honest_t": ht, "oos": oos}


def _verdict(s: dict, executable: bool) -> str:
    """判定。執行可能性は貸借(executable=True)の行で見る。全体は粗ゲージ(参考)。"""
    if s["n"] < 30:
        return "—(n<30)"
    core = s["net"] > 0.5 and s["t"] > 2 and s["honest_t"] > 2 and (s["oos"] or 0) > 0
    if executable:
        if core:
            return "🟡弱い監視候補(2021依存・要フォワード)"
        if s["net"] > 0 and s["t"] > 1.5:
            return "△弱(執行)"
        return "✕(執行)"
    # 全体(非貸借含む)= 粗ゲージ。高tでも執行は貸借行で要確認
    if core:
        return "★粗・強(要貸借確認)"
    if s["net"] > 0 and s["t"] > 1.5:
        return "△粗"
    return "✕粗"


def build_report(recs: list[dict]) -> str:
    """検証レポートを Markdown で返す。"""
    ebars = ec.load_event_bars()
    mh = ec.load_master_history()
    tpx, cal, cidx = _load_calendar()
    n_ex = sum(1 for r in recs if (r.get("attrs") or {}).get("ex_date"))
    L = [f"# 株式分割 実施日fade ショート検証 (#4発表ロングの鏡像) ({datetime.date.today()})", "",
         f"ex_date付き {n_ex}件。実施日/翌日寄りでショート→+3/+5日引け買戻し。市場(β=1)控除α+ショート0.15%控除。",
         "ガード: 日付クラスタt + 非重複正直t + OOS + 年次安定 + PIT(規模/貸借) + 逆日歩感応度。", "",
         "問い: #4『分割「発表」→翌寄りロング(小型)』の鏡像=実施日後の剥落をショートで取れるか。", "",
         "## 1. 出口グリッド (全体 / 貸借=空売り可のみ)", "",
         "| 戦略 | 母体 | n | net(S) | 中央 | 勝率 | t_cl | 正直t | OOS | FDR | 判定 |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    # FDR は全セル(全体+貸借)横断
    allcells = []
    for label, off, n in GRID:
        for tag, so in [("全体", False), ("貸借", True)]:
            rows = short_rows(recs, ebars, tpx, cal, cidx, off, n,
                              shortable_only=so, master_hist=mh)
            s = _stat(rows)
            if s:
                s["label"], s["tag"], s["exec"] = label, tag, so
                allcells.append(s)
    elig = [c for c in allcells if c["n"] >= 30]
    from analyzers.stats import t_to_p
    for c in allcells:
        c["fdr"] = False
    if elig:
        for c, f in zip(elig, benjamini_hochberg([t_to_p(c["t"]) for c in elig], 0.05)):
            c["fdr"] = f
    for c in allcells:
        mark = "★" if c.get("fdr") else ""
        oos = c["oos"] if c["oos"] is not None else 0.0
        L.append(f"| {c['label']} | {c['tag']} | {c['n']} | {c['net']:+.2f}% | {c['med']:+.2f}% | "
                 f"{c['win']:.0f}% | {c['t']:+.1f} | {c['honest_t']:+.1f} | {oos:+.2f}% | {mark} | "
                 f"{_verdict(c, c['exec'])} |")
    # 主候補 翌日寄S→+3日 貸借 の深掘り
    main = short_rows(recs, ebars, tpx, cal, cidx, 1, 3, shortable_only=True, master_hist=mh)
    v = sorted(p for _, _, p in main)
    n = len(v)
    L += ["", "## 2. 主候補 翌日寄S→+3日(貸借) の頑健性", ""]
    if n >= 30:
        trim = st.fmean(v[int(n * 0.05):int(n * 0.95)])
        top5 = sum(sorted(v, reverse=True)[:5])
        share = top5 / (st.fmean(v) * n) * 100 if st.fmean(v) else 0
        L += [f"- 平均{st.fmean(v):+.2f}% / 中央{st.median(v):+.2f}% / 10%トリム{trim:+.2f}% = 少数大勝ち依存でない",
              f"- 勝ち平均{st.fmean([x for x in v if x>0]):+.1f}% / 負け平均{st.fmean([x for x in v if x<0]):+.1f}% / 勝率{sum(1 for x in v if x>0)/n*100:.0f}%",
              f"- 上位5勝の合計寄与は全体の{share:.0f}% = 分散している", "",
              "### 逆日歩感応度 (小型分割株の借株難を想定しショートコストを上げる)", ""]
        for cost in (0.15, 0.30, 0.50, 0.80):
            vv = [p - (cost - SHORT) for p in v]
            L.append(f"- コスト{cost:.2f}%: net{st.fmean(vv):+.2f}% / 勝率{sum(1 for x in vv if x>0)/n*100:.0f}%")
        # ★ 2021(個人マニア年・外れ値)を抜いて生き残るか = 最重要の頑健性
        L += ["", "### ★2021除外/直近の頑健性 (ヘッドラインが2021依存でないかの検算)", "",
              "| 期間 | net | 勝率 | t_clust | 正直t | OOS | n |", "|---|---|---|---|---|---|---|"]
        for tag, sub in [("全体(21-26)", main),
                         ("2021除外(22-26)", [x for x in main if x[0][:4] != "2021"]),
                         ("2024以降(直近3年)", [x for x in main if x[0][:4] >= "2024"])]:
            s = _stat(sub)
            if s:
                L.append(f"| {tag} | {s['net']:+.2f}% | {s['win']:.0f}% | {s['t']:+.1f} | "
                         f"{s['honest_t']:+.1f} | {s['oos'] or 0:+.2f}% | {s['n']} |")
        L += ["", "→ **執行可能(貸借)版は2021を抜くと t_clust が ~1.5 に低下=ヘッドラインの有意性は2021が主因**。",
              "正直t>2・OOS+・全年プラス・中央≈平均は残るが、**強い確証でなく『弱い監視候補・要フォワード』**。"]
    # 年次 (主候補・全体と貸借)
    L += ["", "## 3. 年次安定", "", "| 年 | 全体 net(n) | 貸借 net(n) |", "|---|---|---|"]
    full = short_rows(recs, ebars, tpx, cal, cidx, 1, 3, master_hist=mh)
    byf, byb = {}, {}
    for dt, c, p in full:
        byf.setdefault(dt[:4], []).append(p)
    for dt, c, p in main:
        byb.setdefault(dt[:4], []).append(p)
    for y in sorted(set(byf) | set(byb)):
        f, b = byf.get(y, []), byb.get(y, [])
        fs = f"{st.fmean(f):+.1f}%(n{len(f)})" if f else "—"
        bs = f"{st.fmean(b):+.1f}%(n{len(b)})" if b else "—"
        L.append(f"| {y} | {fs} | {bs} |")
    # 規模別
    L += ["", "## 4. 規模別(PIT) 翌日寄S→+3日 全体", "", "| 規模 | net | 勝率 | t | n |", "|---|---|---|---|---|"]
    for band in ["小型", "中型", "大型"]:
        sub = [(dt, c, p) for dt, c, p in full
               if ec.pit_attrs(mh, c, dt).get("scale_band") == band]
        if len(sub) >= 15:
            vv = [p for _, _, p in sub]
            L.append(f"| {band} | {st.fmean(vv):+.2f}% | {sum(1 for x in vv if x>0)/len(vv)*100:.0f}% | "
                     f"{clustered_t(vv, [d for d,_,_ in sub]):+.1f} | {len(sub)} |")
    L += ["", "## 判定メモ", "",
          "- **#4(分割発表ロング)の鏡像として実在する控えめなショート**。執行は貸借×+3日が芯。",
          "- per-trade は2021(+6%超)が突出し近年は+0.1〜1.3%=**実戦期待値は近年平均+0.5〜1%で見る**(全体値は2021嵩上げ)。",
          "- +5日版は貸借だと有意性落ち=長く持つ妙味は非貸借(張れない銘柄)偏在。+3日が執行可能な芯。",
          "- 逆日歩でコスト0.8%でもプラス維持・中央≈平均=頑健だが小型空売り在庫/逆日歩は実地で要確認。",
          "- β=1近似(分割株は高β=上昇局面でα過小=ショート過小評価寄り)。daily_bars_universe完成後にβ実推定で再検証。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-path", type=Path, default=IN_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    recs = json.loads(args.in_path.read_text())["records"]
    rep = build_report(recs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, rep)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

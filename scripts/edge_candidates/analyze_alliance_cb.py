"""仕様B: 業務提携×CB(希薄化)発行 ショートエッジ 本検証。

「資本業務提携」かつ「CB/新株予約権/第三者割当など希薄化を伴う資金調達」を同日/近接(±1営業日)
で発表した銘柄群に、反応日以降のショートαがあるかを検証 (#5 業務提携×小型ショートの希薄化上乗せ版)。

希薄化タイプ (title 基準): CB(転換社債/新株予約権付社債) / ワラント(新株予約権) / 第三者割当。
出口=反応日(開示翌取引日)寄り起点。d0=寄→引(raw) / +1/+3/+5日引(TOPIX β=1 超過α)。ショート0.15%控除。
3段ガード: BH-FDR ＋ 日付クラスタ頑健t ＋ 非重複の正直t ＋ walk-forward OOS ＋ PITユニバース。
**空売り可否 (信用区分 貸借=制度信用で空売り可) を記録し、実効EV(貸借のみ)で執行可能性を関門化**。
層化: 規模/市場(PIT) / 非プライム小型(#5再現) / 希薄化タイプ / 空売り可否。

出力: reports/alliance_cb_short.md
使い方: python -m scripts.edge_candidates.analyze_alliance_cb
"""
from __future__ import annotations

import argparse
import datetime
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_text
from scripts.edge_candidates import event_combo_lib as ec

OUT_PATH = ec.REPO_ROOT / "reports" / "alliance_cb_short.md"
_ALLIANCE_KW = ("資本業務提携", "資本提携")
# 希薄化タイプ判定 (優先順)
_DILUTION = [
    ("CB", ("転換社債", "新株予約権付社債", "ユーロ円建")),
    ("ワラント", ("新株予約権", "行使価額")),
    ("第三者割当", ("第三者割当",)),
]
_NEAR_DAYS = 1                # 提携と希薄化の近接許容 (営業日近似=暦±1)


def _dilution_type(title: str) -> str | None:
    for name, kws in _DILUTION:
        if any(k in title for k in kws):
            return name
    return None


def extract_events() -> list[dict[str, Any]]:
    """資本業務提携×希薄化(±1日) イベントを抽出 (PIT属性付与済)。"""
    rows = ec.load_tdnet_rows()
    mh = ec.load_master_history()
    # 提携 (code,date) と 希薄化 (code -> {date: type})
    alliance: dict[tuple[str, str], str] = {}
    dilution: dict[str, dict[str, str]] = defaultdict(dict)
    for r in rows:
        c, d, t = r["code"], r["date"], r["title"]
        if not c or not d:
            continue
        if any(k in t for k in _ALLIANCE_KW):
            alliance.setdefault((c, d), r["time"])
        dt = _dilution_type(t)
        if dt:
            dilution[c].setdefault(d, dt)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for (code, date), atime in sorted(alliance.items()):
        dd = datetime.date.fromisoformat(date)
        # ±1営業日近似で希薄化を探す
        found = None
        anchor = date
        for off in range(-_NEAR_DAYS, _NEAR_DAYS + 1):
            cand = (dd + datetime.timedelta(days=off)).isoformat()
            if cand in dilution.get(code, {}):
                found = dilution[code][cand]
                anchor = max(anchor, cand)        # 反応は後発の開示翌日起点
                break
        if not found:
            continue
        if (code, anchor) in seen:
            continue
        seen.add((code, anchor))
        pit = ec.pit_attrs(mh, code, anchor)
        nonprime_small = (pit.get("mkt") not in ("プライム", "東証一部")) and (pit.get("scale_band") == "小型")
        shortable = pit.get("mrgn") == "貸借"
        out.append({"code": code, "event_date": anchor,
                    "attrs": {"disc_time": atime, "dilution": found,
                              "nonprime_small": nonprime_small, "shortable": shortable, **pit}})
    return out


def _cells_for(events: list[dict]) -> dict[str, dict]:
    """ショート出口グリッドの stats を返し、FDR を出口横断で適用。"""
    out: dict[str, dict] = {}
    flat = []
    for label, metric, hold in ec.EXITS_SHORT:
        s = ec.directional_stats(events, metric, "short", ec.SHORT_COST, hold_days=hold)
        if s:
            out[label] = s
            flat.append(s)
    ec.apply_fdr(flat)
    return out


def _table(cells: dict[str, dict]) -> list[str]:
    L = ["| 出口 | n | 独立n | net EV(S) | t_clust | 正直t | 勝率 | OOS | FDR | 判定 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for label, _m, _h in ec.EXITS_SHORT:
        s = cells.get(label)
        if not s:
            continue
        mark = "★" if s.get("fdr_significant") else ""
        oos = s["oos"] if s["oos"] is not None else 0.0
        L.append(f"| {label} | {s['n']} | {s['n_indep']} | {s['net_ev']:+.2f}% | {s['t_clust']:+.2f} | "
                 f"{s['honest_t']:+.2f} | {s['win']:.0f}% | {oos:+.2f}% | {mark} | {ec.verdict(s)} |")
    return L


def build_report(events: list[dict]) -> str:
    """仕様B 検証レポートを Markdown で返す。"""
    dates = [e["event_date"] for e in events]
    n_short = sum(1 for e in events if e["attrs"].get("shortable"))
    by_dil = defaultdict(int)
    for e in events:
        by_dil[e["attrs"]["dilution"]] += 1
    L = [f"# 仕様B: 業務提携×CB(希薄化) ショート 本検証 ({datetime.date.today()})", "",
         f"対象 **{len(events)}件** ({min(dates)}〜{max(dates)})。資本業務提携 ＋ 希薄化(CB/新株予約権/"
         f"第三者割当) を同日/±1日で発表。うち空売り可(貸借) **{n_short}件**。", "",
         "出口=反応日(後発開示の翌取引日)寄り起点。d0=寄→引(raw) / +N日=TOPIX β=1 超過α。ショート往復0.15%控除。",
         "3段ガード: BH-FDR ＋ 日付クラスタ頑健t ＋ **非重複の正直t** ＋ walk-forward OOS ＋ PITユニバース。",
         "★通過=net>0.5% & t_clust>2 & 正直t>2 & FDR生存 & OOS>0。", "",
         "問い: #5(業務提携×小型ショート)に希薄化が上乗せ効果を生むか。**空売り在庫(貸借)が執行の関門**。", "",
         "## 0. 希薄化タイプ内訳", "", "| タイプ | n |", "|---|---|"]
    for k, v in sorted(by_dil.items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v} |")
    L += ["", "## 1. 全体", ""]
    L += _table(_cells_for(events))
    L += ["", "## 2. 空売り可(貸借)のみ = 実効EV (執行可能性の関門)", ""]
    L += _table(_cells_for([e for e in events if e["attrs"].get("shortable")]))
    L += ["", "## 3. 非プライム小型 (#5 再現確認)", ""]
    sub = [e for e in events if e["attrs"].get("nonprime_small")]
    L += [f"(n={len(sub)})", ""] + (_table(_cells_for(sub)) if sub else ["- (該当なし)"])
    L += ["", "### 非プライム小型 × 空売り可(貸借)", ""]
    sub2 = [e for e in sub if e["attrs"].get("shortable")]
    L += [f"(n={len(sub2)})", ""] + (_table(_cells_for(sub2)) if len(sub2) >= 5 else ["- (n<5 省略)"])
    L += ["", "## 4. 希薄化タイプ別", ""]
    for dil in [k for k, _ in _DILUTION]:
        sub = [e for e in events if e["attrs"]["dilution"] == dil]
        if len(sub) < 10:
            continue
        L += [f"### {dil} (n={len(sub)})", ""]
        L += _table(_cells_for(sub))
        L += [""]
    L += ["## 判定メモ", "",
          "- 潜在希薄化率(転換価格×発行株数)は開示PDF本文依存で公式API取得不可=本検証では未層化(留保)。",
          "- 割当先(ファンド vs 事業会社)も title から機械分類困難。資本業務提携の割当先は提携先(事業会社)が"
          "多く売り圧は限定的、独立MSワラント(証券会社)は売り圧強の想定だが本データでは分離せず=留保。",
          "- d0(寄→引)はraw、+N日はTOPIX超過α(ショートは符号反転後に控除)。約定: 後発開示の翌取引日寄りで空売り。",
          "- **空売り可(貸借)に絞った実効EVが net 正＋t>2＋FDR生存＋正直t>2 で揃って初めて執行可能な候補**。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    print("[alliance_cb] イベント抽出中...")
    events = extract_events()
    print(f"[alliance_cb] 資本業務提携×希薄化 {len(events)}件。リターン付与中...")
    ebars = ec.load_event_bars()
    got = ec.enrich_returns(events, ebars)
    print(f"[alliance_cb] d0_ret付与 {got}/{len(events)}。レポート生成中...")
    rep = build_report(events)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, rep)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

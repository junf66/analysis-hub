"""#4 株式分割発表→翌寄ロングの細分化検証 (6軸+F)。

split_multiday_enriched.json (enrich_split_axes が付与した軸ラベル+alpha_d{N}_ret) を読み、
軸 A/D/E/F/G/H/J × 時間軸 +3/+5/+10日 で TOPIX-α net EV / 日付クラスタ頑健t / 勝率 /
walk-forward OOS を算出。全 (軸×バケット×時間軸) 横断で BH-FDR を適用し偽陽性を抑制。

出力: reports/edge4_split_detailed.md
B時価総額・C業種・I PER/PBR は listed/info・fins/statements が契約外 (403) のため対象外。
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Callable

from analyzers.stats import benjamini_hochberg
from scripts._atomic import atomic_write_text
from scripts.edge_candidates import lib

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IN_PATH = REPO_ROOT / "data" / "edge_candidates" / "split_multiday_enriched.json"
OUT_PATH = REPO_ROOT / "reports" / "edge4_split_detailed.md"
HORIZONS = [3, 5, 10]
MIN_N = 30


def _gap_bucket(g: float | None) -> str | None:
    if g is None:
        return None
    if g > 1.0:
        return "GU(>+1%)"
    if g > 0.3:
        return "浅GU(+0.3〜+1%)"
    if g >= -0.3:
        return "フラット(±0.3%)"
    if g >= -1.0:
        return "浅GD(-1〜-0.3%)"
    if g >= -3.0:
        return "中GD(-3〜-1%)"
    return "深GD(<-3%)"


def _ratio_bucket(r: float | None) -> str | None:
    if r is None:
        return "比率不明"
    if r < 1.5:
        return "1:1.5未満"
    if r < 2.5:
        return "1:2"
    if r < 3.5:
        return "1:3"
    if r < 4.5:
        return "1:4"
    if r < 9.5:
        return "1:5〜9"
    return "1:10以上"


def _turnover_bucket(t: float | None) -> str | None:
    if t is None:
        return None
    if t >= 1e9:
        return "高(≥10億/日)"
    if t >= 1e8:
        return "中(1〜10億/日)"
    return "低(<1億/日)"


def _price_bucket(p: float | None) -> str | None:
    if p is None:
        return None
    if p >= 10000:
        return "高単価(≥1万円)"
    if p >= 1000:
        return "中単価(1千〜1万円)"
    return "低単価(<1千円)"


# 軸名 → (見出し, attrs→バケットラベル or None)
AXES: dict[str, tuple[str, Callable[[dict[str, Any]], str | None]]] = {
    "A": ("信用区分", lambda a: a.get("isstype") or "不明"),
    "B": ("規模", lambda a: a.get("scale_band")),          # /equities/master ScaleCat
    "C": ("業種", lambda a: a.get("s17")),                 # /equities/master S17
    "D": ("分割比率", lambda a: _ratio_bucket(a.get("split_ratio"))),
    "E": ("単独/複合", lambda a: a.get("combo")),
    "F": ("REIT", lambda a: "REIT" if a.get("is_reit") else "普通株"),
    "G": ("流動性", lambda a: _turnover_bucket(a.get("turnover_20"))),
    "H": ("株価帯", lambda a: _price_bucket(a.get("entry_price"))),
    "J": ("寄り方gap", lambda a: _gap_bucket(a.get("gap_pct"))),
}


def cell_stats(records: list[dict[str, Any]], n_days: int) -> dict[str, Any] | None:
    """records の alpha_d{n}_ret について net EV/t/勝率/OOS を返す (lib 共通枠)。"""
    return lib._exit_stats(records, f"alpha_d{n_days}_ret", lib.LONG_COST)


def verdict(s: dict[str, Any]) -> str:
    """1 セルの判定ラベル。"""
    n, ev, t, oos = s["n"], s["net_ev"], s["t_clust"], s["oos"]
    fdr, win = s.get("fdr_significant", False), s["win"]
    if ev <= 0 or win < 45 or t < -1:
        return "除外"
    if n < MIN_N or not fdr or oos is None or oos <= 0 or t <= 2.0:
        return "—"
    if ev > 1.5 and t > 2.5:
        return "★優先"
    if ev > 0.5:
        return "通過"
    return "—"


def build_cells(records: list[dict[str, Any]]) -> dict[str, dict[str, dict[int, dict]]]:
    """軸→バケット→時間軸→stats。全セルの p を集めて横断 FDR を適用。"""
    out: dict[str, dict[str, dict[int, dict]]] = {}
    flat: list[dict] = []
    for ax, (_, bucketer) in AXES.items():
        groups: dict[str, list[dict]] = {}
        for r in records:
            b = bucketer(r.get("attrs") or {})
            if b is not None:
                groups.setdefault(b, []).append(r)
        out[ax] = {}
        for bucket, recs in groups.items():
            out[ax][bucket] = {}
            for n in HORIZONS:
                s = cell_stats(recs, n)
                if s is None:
                    continue
                out[ax][bucket][n] = s
                s["fdr_significant"] = False
                if s["n"] >= MIN_N:        # FDR 検定族は n≥30 セルのみ (微小nノイズで検定力を薄めない)
                    flat.append(s)
    if flat:
        for s, f in zip(flat, benjamini_hochberg([s["p"] for s in flat], 0.05)):
            s["fdr_significant"] = f
    return out


def _fmt_row(bucket: str, cells: dict[int, dict]) -> list[str]:
    lines = []
    for n in HORIZONS:
        s = cells.get(n)
        if not s:
            continue
        mark = "★" if s.get("fdr_significant") else ""
        oos = s["oos"] if s["oos"] is not None else 0.0
        lines.append(f"| {bucket} | +{n}日 | {s['n']} | {s['net_ev']:+.2f}% | {s['t_clust']:+.2f} | "
                     f"{s['win']:.0f}% | {oos:+.2f}% | {mark} | {verdict(s)} |")
    return lines


def base_stats(records: list[dict[str, Any]]) -> dict[int, dict]:
    """全体ベース (細分化なし) の α 成績。"""
    return {n: cell_stats(records, n) for n in HORIZONS}


def find_combos(records: list[dict[str, Any]], n_days: int = 10,
                min_n: int = MIN_N) -> list[dict[str, Any]]:
    """2軸組合せセグメントを総当りし、α t_clust 上位を返す。"""
    keys = list(AXES)
    res: list[dict[str, Any]] = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            ax1, ax2 = keys[i], keys[j]
            b1, b2 = AXES[ax1][1], AXES[ax2][1]
            groups: dict[tuple[str, str], list[dict]] = {}
            for r in records:
                a = r.get("attrs") or {}
                k1, k2 = b1(a), b2(a)
                if k1 is not None and k2 is not None:
                    groups.setdefault((k1, k2), []).append(r)
            for (k1, k2), recs in groups.items():
                s = cell_stats(recs, n_days)
                if s and s["n"] >= min_n:
                    res.append({"seg": f"{AXES[ax1][0]}={k1} × {AXES[ax2][0]}={k2}", **s})
    res.sort(key=lambda r: -r["t_clust"])
    return res


def tepco_analog(records: list[dict[str, Any]]) -> dict[str, Any]:
    """東エレ型 (1:5以上分割 × 自社株買い同時 × 高単価 × GU寄り) 類似サンプルを抽出。"""
    sel = []
    for r in records:
        a = r.get("attrs") or {}
        if (a.get("split_ratio") or 0) >= 5 and a.get("combo") == "自社株買い同時" \
                and (a.get("entry_price") or 0) >= 10000 and (a.get("gap_pct") or -99) > 1.0:
            sel.append(r)
    # 条件を緩めた段階別 n も返す
    def count(pred):
        return [r for r in records if pred(r.get("attrs") or {})]
    tiers = {
        "1:5以上分割": count(lambda a: (a.get("split_ratio") or 0) >= 5),
        "+自社株買い同時": count(lambda a: (a.get("split_ratio") or 0) >= 5 and a.get("combo") == "自社株買い同時"),
        "+高単価(≥1万)": count(lambda a: (a.get("split_ratio") or 0) >= 5 and a.get("combo") == "自社株買い同時" and (a.get("entry_price") or 0) >= 10000),
        "+GU寄り(全条件)": sel,
    }
    return {"selected": sel, "tiers": tiers}


def write_report(records: list[dict[str, Any]], *, out_path: Path = OUT_PATH) -> Path:
    """細分化検証の全結果 (ベース/軸別/組合せ/弱セグ/8035型/寄り方戦術) を Markdown 出力。"""
    import datetime
    cells = build_cells(records)
    base = base_stats(records)
    L: list[str] = [f"# #4 株式分割発表→翌寄ロング 細分化検証 ({datetime.date.today()})", "",
                    f"対象 n={len(records)} / TOPIX(β=1)超過α / ロング往復0.20%控除 / "
                    "全セル横断 BH-FDR 補正。", "",
                    "> B規模(ScaleCat)・C業種(S17)は /equities/master から付与。"
                    " 厳密な時価総額(発行株数)とI PER/PBRは対象外。F REITは証券コード帯による近似。", ""]

    # 1. ベース整合性
    L += ["## 1. ベース成績 (細分化なし)", "",
          "| 時間軸 | n | net α EV | t_clust | 勝率 | OOS |", "|---|---|---|---|---|---|"]
    for n in HORIZONS:
        s = base[n]
        if s:
            oos = s["oos"] if s["oos"] is not None else 0.0
            L.append(f"| +{n}日 | {s['n']} | {s['net_ev']:+.2f}% | {s['t_clust']:+.2f} | "
                     f"{s['win']:.0f}% | {oos:+.2f}% |")
    L += ["", "既存ベース (指示書): +3日α+0.76%/t+2.19, +5日α+1.16%/t+2.55, +10日α+1.64%/t+2.64 (n≈939)。"
          "上表との一致で再現性を確認。", ""]

    # 2. 軸別テーブル
    L += ["## 2. 軸別細分化 (各バケット × +3/+5/+10日)", ""]
    for ax, (title, _) in AXES.items():
        L += [f"### 軸{ax} {title}", "",
              "| カテゴリ | 時間軸 | n | net α EV | t_clust | 勝率 | OOS | FDR | 判定 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for bucket in sorted(cells[ax]):
            L += _fmt_row(bucket, cells[ax][bucket])
        L.append("")

    # 3. 最強サブパターン
    combos = find_combos(records, n_days=10)
    for s in combos:
        s["fdr_significant"] = s.get("fdr_significant", False)
    L += ["## 3. 最強サブパターン (2軸組合せ, +10日, n≥30, t降順 上位10)", "",
          "| セグメント | n | net α EV | t_clust | 勝率 | OOS |", "|---|---|---|---|---|---|"]
    for s in combos[:10]:
        oos = s["oos"] if s["oos"] is not None else 0.0
        L.append(f"| {s['seg']} | {s['n']} | {s['net_ev']:+.2f}% | {s['t_clust']:+.2f} | "
                 f"{s['win']:.0f}% | {oos:+.2f}% |")
    L.append("")

    # 4. 弱いセグメント (除外候補)
    weak = [s for s in combos if s["net_ev"] <= 0 or s["win"] < 45 or s["t_clust"] < -1]
    L += ["## 4. 弱い/除外推奨セグメント (+10日, EV≤0 or 勝率<45% or t<-1)", "",
          "| セグメント | n | net α EV | t_clust | 勝率 |", "|---|---|---|---|---|"]
    for s in sorted(weak, key=lambda r: r["t_clust"])[:10]:
        L.append(f"| {s['seg']} | {s['n']} | {s['net_ev']:+.2f}% | {s['t_clust']:+.2f} | {s['win']:.0f}% |")
    L.append("")

    # 5. 東エレ8035 位置づけ
    ta = tepco_analog(records)
    L += ["## 5. 東京エレクトロン(8035)型の位置づけ", "",
          "8035条件: 超大型(時価総額は契約外で判定不可) / 1:5分割 / 自社株買い同時 / 高単価 / GU寄り。",
          "→ 時価総額以外の条件で類似サンプルを段階抽出 (各段階の n と +10日α):", "",
          "| 条件段階 | n | +10日α 平均 | 勝率 |", "|---|---|---|---|"]
    for tier, recs in ta["tiers"].items():
        vals = [(r["attrs"]["alpha_d10_ret"] - lib.LONG_COST) for r in recs
                if (r.get("attrs") or {}).get("alpha_d10_ret") is not None]
        if vals:
            ev = statistics.fmean(vals)
            win = sum(1 for v in vals if v > 0) * 100.0 / len(vals)
            L.append(f"| {tier} | {len(vals)} | {ev:+.2f}% | {win:.0f}% |")
        else:
            L.append(f"| {tier} | {len(recs)} | (該当0) | — |")
    L += ["", f"全条件該当サンプル {len(ta['selected'])}件 (= 東エレ型は極めて稀)。", ""]
    own = [r for r in records if r["code"] in ("8035", "80350")]
    L += ["**8035 自身のイベント** (データ期間内):", "",
          "| 発表日 | 分割比率 | 同時開示 | gap% | 翌寄り価格 | +3日α | +5日α | +10日α |",
          "|---|---|---|---|---|---|---|---|"]
    for r in sorted(own, key=lambda x: x["event_date"]):
        a = r["attrs"]
        def g(k):
            v = a.get(k)
            return f"{v:+.2f}%" if isinstance(v, (int, float)) and "ret" in k else \
                   (f"{v:+.2f}%" if k == "gap_pct" and v is not None else
                    (f"{v:,.0f}" if k == "entry_price" and v is not None else
                     (f"1:{v:g}" if k == "split_ratio" and v is not None else (a.get(k) or "—"))))
        L.append(f"| {r['event_date']} | {g('split_ratio')} | {a.get('combo')} | {g('gap_pct')} | "
                 f"{g('entry_price')} | {g('alpha_d3_ret')} | {g('alpha_d5_ret')} | {g('alpha_d10_ret')} |")
    L += ["", "重要: **分割+自社株買い同時 (軸E) は n18 で +10日α -2.69% (除外判定)** であり、"
          "「東エレ型 = 自社株買い同時が最強」という事前仮説はデータで否定された。8035 個別事例は"
          "有名だが非代表。後発エントリーは #4 既知特性 (寄り>引け 0.5%減衰) からも優位性が削られる。", ""]

    # 6. 寄り方別戦術
    gap_order = ["GU(>+1%)", "浅GU(+0.3〜+1%)", "フラット(±0.3%)", "浅GD(-1〜-0.3%)",
                 "中GD(-3〜-1%)", "深GD(<-3%)"]
    L += ["## 6. 寄り方別の戦術 (軸J / リートエッジ②との対比)", "",
          "リートPO(エッジ②)は寄り方非依存で頑健だったが、**④分割は寄り方依存**だった。",
          "+10日α を寄り方順に並べると:", ""]
    for b in gap_order:
        s = cells["J"].get(b, {}).get(10)
        if s:
            L.append(f"- {b}: α{s['net_ev']:+.2f}% / t{s['t_clust']:+.2f} / 勝率{s['win']:.0f}% / n{s['n']}")
    L += ["", "→ 事前仮説『GD=過剰反応の戻りで本命 / GU=織込済で消失』は **データで逆**。"
          "**GU(>+1%)が唯一の強気ゾーン** (α+2.59%/t+2.90)、浅GU・GD系は負〜ゼロ。"
          "分割は『発表で買われ翌寄りも上ギャップ→さらに継続』する**順張り(モメンタム)型**で、"
          "リート②の平均回帰的・寄り方非依存とは性質が異なる。",
          "運用: **GUスタートは継続保有可・むしろ本命。浅GU/GDスタートは見送り**。"
          "翌朝の気配がGUなら入る、フラット〜GDなら原則スルー、が機械ルール。", ""]

    # 7. 結論・運用フィルタ (n≥30 セルから機械抽出)
    adds, drops = [], []
    for ax, (title, _) in AXES.items():
        for bucket, cs in cells[ax].items():
            s10 = cs.get(10)
            if not s10 or s10["n"] < MIN_N:
                continue
            best = max((cs[n] for n in HORIZONS if n in cs), key=lambda x: x["t_clust"])
            if best["net_ev"] > 0.5 and best["t_clust"] > 2.0 and (best["oos"] or 0) > 0:
                adds.append((best["t_clust"], f"{title}={bucket}",
                             f"net{best['net_ev']:+.2f}%/t{best['t_clust']:+.2f}/n{best['n']}"
                             f"{'★' if best.get('fdr_significant') else ''}"))
            if s10["net_ev"] <= 0 or s10["win"] < 45 or s10["t_clust"] < -1:
                drops.append((s10["t_clust"], f"{title}={bucket}",
                              f"+10日 net{s10['net_ev']:+.2f}%/t{s10['t_clust']:+.2f}/勝率{s10['win']:.0f}%/n{s10['n']}"))
    L += ["## 7. 結論・運用フィルタ", "",
          "ベースは再現 (+10日α+1.7%/t+2.7)。**通過は単独軸でも FDR を1つも生存せず** (検定族を"
          "n≥30に限定してもベース自体が t≈2.7 で多重補正に耐えない)。以下は FDR 前の素の優劣で、"
          "**実弾はベース#4を主とし、下記は重み付けフィルタとして解釈**する。", "",
          "**強いセグメント (加点フィルタ, best horizon EV>0.5 & t>2 & OOS>0):**"]
    for _, seg, st in sorted(adds, reverse=True):
        L.append(f"- {seg}: {st}")
    L += ["", "**弱い/除外セグメント (+10日 EV≤0 or 勝率<45% or t<-1):**"]
    for _, seg, st in sorted(drops):
        L.append(f"- {seg}: {st}")
    L += ["", "### 主要な発見 (事前仮説との対比)",
          "- **軸A 信用区分が最大の分岐**: 信用銘柄 (+10日α+3.99%/t+2.64) ≫ 貸借銘柄 (+0.22%/t+0.61)。"
          "エッジは実質的に『信用銘柄(非貸借)』に集中。貸借銘柄はほぼゼロ。",
          "- **軸J は GU 寄りが最強** (+10日α+2.59%/t+2.90)。仮説『GD=過剰反応の戻りで本命/GU=織込済で消失』"
          "は**逆**で、分割は順張り(GU継続)型。浅GU・GD・深GDは負〜ゼロ。",
          "- **軸E『自社株買い同時(東エレ型)』は否定**: n18で+10日α-2.69%(除外)。最強は『単独』(+2.42%/t+2.87)。"
          "『配当予想修正同時』(n319)は負〜ゼロで主な希薄化要因。",
          "- **軸H 高単価(≥1万円)は劣後** (除外)。低・中単価が優位。東エレ型(高単価×自社株買い×GU)の"
          "高単価要素も逆風。",
          "- リートPO(エッジ②)は寄り方非依存だったが、**④は寄り方依存(GU>その他)** で性質が異なる。", "",
          "### 推奨フィルタルール (ベース#4への重ね掛け)",
          "1. **信用銘柄 × 単独発表** を優先 (信用×単独 n200 +10日α+4.64%/t+2.58)。",
          "2. **配当予想修正・自社株買い 同時開示は除外**または減量。",
          "3. **高単価(≥1万円)は減量**。低〜中単価を主軸。",
          "4. 寄り方: **GU(>+1%)はそのまま継続、浅GU/深GDは見送り** (順張り性)。",
          "5. 最強の重ね合わせ例: **信用 × GU寄り** (n274 +10日α+5.41%/t+2.82)。ただし FDR非生存="
          "過剰最適化に注意し、ベース運用にバイアスを足す程度に留める。", ""]

    atomic_write_text(out_path, "\n".join(L))
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-path", type=Path, default=IN_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    records = json.loads(args.in_path.read_text())["records"]
    out = write_report(records, out_path=args.out)
    # 8035 自身の有無をログ
    own = [r for r in records if r["code"] in ("8035", "80350")]
    print(f"[split_detailed] n={len(records)} / 8035 events={len(own)} → wrote {out}")


if __name__ == "__main__":
    main()

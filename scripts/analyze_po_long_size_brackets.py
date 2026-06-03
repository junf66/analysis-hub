"""PO翌日GD LONG を「時価総額(円)別」と「TOPIX規模区分別」の両軸で検証する。

目的: 「大型/中型の閾値は何円か」を定量化する。
結論の骨子: 採用エッジが使う 大型/中型/小型 は **TOPIX ScaleCat(指数構成区分)** であり、
円の固定閾値ではない。円(億円)で刻むと t>2 の帯は無く、TOPIX中型(Mid400)のみが生存する
(=エッジは"規模の円閾値"ではなく"指数区分メンバーシップ"で決まる)。

入力: data/po_records.json (market_cap[億円]/gap_pct/stage/po_type)
      data/edge_candidates/po_enriched.json (翌日引けret/scale_band)
      data/edge_candidates/equities_master.json (ScaleCat→scale_band の母集団分布)
出力: reports/po_long_size_brackets.md
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PO_PATH = REPO_ROOT / "data" / "po_records.json"
ENRICHED_PATH = REPO_ROOT / "data" / "edge_candidates" / "po_enriched.json"
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
REPORT_PATH = REPO_ROOT / "reports" / "po_long_size_brackets.md"

COST_PCT = 0.20  # long 往復
GD_THRESHOLD = -0.5

# 時価総額(億円)ブラケット。1兆 = 10,000億。
MC_BRACKETS: list[tuple[float, float, str]] = [
    (0, 300, "<300億"),
    (300, 500, "300-500億"),
    (500, 1000, "500-1,000億"),
    (1000, 3000, "1,000-3,000億"),
    (3000, 10000, "3,000億-1兆"),
    (10000, float("inf"), "≥1兆"),
]
SCALE_BANDS = ["小型", "中型", "大型"]

# 出口時刻 → リターンフィールド。9:05〜11:30は分足由来(2024-05以降, n小)、引けは日足で全期間。
# 時刻別は po_records.attrs、引けは po_enriched にある。
EXIT_FIELDS: list[tuple[str, str]] = [
    ("9:05", "next_day_905_ret"),
    ("9:10", "next_day_910_ret"),
    ("9:15", "next_day_915_ret"),
    ("9:30", "next_day_930_ret"),
    ("10:00", "next_day_1000_ret"),
    ("11:30", "next_day_morning_ret"),
    ("引け", "next_day_open_to_close_ret"),
]


def load_records() -> list[dict[str, Any]]:
    """po_records を返す。"""
    data = json.loads(PO_PATH.read_text())
    return data.get("records", []) if isinstance(data, dict) else data


def load_enriched() -> dict[str, dict[str, Any]]:
    """id → {next_day_open_to_close_ret, scale_band, ...}。"""
    if not ENRICHED_PATH.exists():
        return {}
    return json.loads(ENRICHED_PATH.read_text()).get("by_id", {})


def load_master_records() -> list[dict[str, Any]]:
    """equities_master の records を返す。"""
    return json.loads(MASTER_PATH.read_text()).get("records", [])


def stat(rets: list[float]) -> dict[str, float]:
    """n/EV/t/win を計算。"""
    n = len(rets)
    if n == 0:
        return {"n": 0, "ev": 0.0, "t": 0.0, "win": 0.0}
    ev = statistics.fmean(rets)
    sd = statistics.stdev(rets) if n > 1 else 0.0
    t = ev / (sd / (n ** 0.5)) if sd > 0 else 0.0
    win = sum(1 for x in rets if x > 0) / n * 100
    return {"n": n, "ev": ev, "t": t, "win": win}


def bracket_label(market_cap: float) -> str | None:
    """時価総額(億円)を MC_BRACKETS のラベルに割り当てる。"""
    for lo, hi, label in MC_BRACKETS:
        if lo <= market_cap < hi:
            return label
    return None


def collect_po_long(records: list[dict[str, Any]],
                    enriched: dict[str, dict[str, Any]]
                    ) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    """announce 普通株 翌日GD の翌寄り→翌引け LONG net を、時価総額別と規模区分別に集める。"""
    by_mc: dict[str, list[float]] = {b[2]: [] for b in MC_BRACKETS}
    by_band: dict[str, list[float]] = {b: [] for b in SCALE_BANDS}
    for r in records:
        if r.get("stage") != "announce" or r.get("po_type") != "普通":
            continue
        a = r.get("attrs") or {}
        gap = a.get("gap_pct")
        if gap is None or float(gap) > GD_THRESHOLD:
            continue
        e = enriched.get(r.get("id", ""))
        if not e:
            continue
        oc = e.get("next_day_open_to_close_ret")
        if oc is None:
            continue
        net = float(oc) - COST_PCT
        mc = r.get("market_cap")
        if mc:
            label = bracket_label(float(mc))
            if label:
                by_mc[label].append(net)
        band = e.get("scale_band")
        if band in by_band:
            by_band[band].append(net)
    return by_mc, by_band


def collect_size_by_exit(records: list[dict[str, Any]],
                         enriched: dict[str, dict[str, Any]]
                         ) -> dict[str, dict[str, list[float]]]:
    """GD announce 普通株の long net を 規模band × 出口時刻 で集める。

    時刻別 ret は attrs、引けは enriched から取り、各々往復0.20%控除。
    """
    out: dict[str, dict[str, list[float]]] = {
        b: {label: [] for label, _ in EXIT_FIELDS} for b in SCALE_BANDS
    }
    for r in records:
        if r.get("stage") != "announce" or r.get("po_type") != "普通":
            continue
        a = r.get("attrs") or {}
        gap = a.get("gap_pct")
        if gap is None or float(gap) > GD_THRESHOLD:
            continue
        e = enriched.get(r.get("id", ""))
        if not e:
            continue
        band = e.get("scale_band")
        if band not in out:
            continue
        for label, field in EXIT_FIELDS:
            val = e.get(field) if field == "next_day_open_to_close_ret" else a.get(field)
            if val is not None:
                out[band][label].append(float(val) - COST_PCT)
    return out


def best_exit(by_exit: dict[str, list[float]], min_n: int) -> tuple[str, dict[str, float]] | None:
    """n≥min_n を満たす出口の中で最大 EV の (出口, stat) を返す。無ければ None。"""
    cands = [(label, stat(rets)) for label, rets in by_exit.items() if len(rets) >= min_n]
    if not cands:
        return None
    return max(cands, key=lambda x: x[1]["ev"])


def scale_band_mc_ranges(records: list[dict[str, Any]],
                         master: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """PO universe での scale_band 別 時価総額(億円) 分位。閾値の重複を示す。"""
    code_band = {m["Code"]: m.get("scale_band") for m in master}
    vals: dict[str, list[float]] = {b: [] for b in SCALE_BANDS}
    for r in records:
        code = r.get("code", "")
        code5 = code + "0" if len(code) == 4 else code
        mc = r.get("market_cap")
        band = code_band.get(code5)
        if mc and band in vals:
            vals[band].append(float(mc))
    out: dict[str, dict[str, float]] = {}
    for b in SCALE_BANDS:
        v = sorted(vals[b])
        if not v:
            continue
        out[b] = {
            "n": len(v),
            "min": v[0],
            "p25": v[len(v) // 4],
            "median": statistics.median(v),
            "p75": v[3 * len(v) // 4],
            "max": v[-1],
        }
    return out


def build_report(records: list[dict[str, Any]],
                 enriched: dict[str, dict[str, Any]],
                 master: list[dict[str, Any]]) -> str:
    """規模別検証レポートを生成。"""
    L: list[str] = []
    L.append("# PO翌日GD LONG 規模別検証 ── 「大型/中型の閾値」 (2026-06-03)")
    L.append("")
    L.append("対象: PO発表(announce)×普通株×翌日GD(寄り≤-0.5%) / 翌寄り買い→翌引け売り / long往復0.20% net。")
    L.append("")
    L.append("## 結論: 円の固定閾値は存在しない（規模区分=TOPIX指数メンバーシップ）")
    L.append("")
    L.append("- 採用エッジが言う **大型/中型/小型 は TOPIX ScaleCat（指数構成区分）** であり、")
    L.append("  「○○億円以上」という固定閾値ではない。J-Quants `equities_master.ScaleCat` をそのまま採用:")
    L.append("  - **大型** = TOPIX Core30 + Large70（流動性調整時価総額の上位約100銘柄）")
    L.append("  - **中型** = TOPIX Mid400（次の400銘柄）")
    L.append("  - **小型** = TOPIX Small1/2 + 非構成（残り全部）")
    L.append("- 年1回の定期入替で決まる相対ランクのため、**円換算レンジは大きく重複**する（下表）。")
    L.append("- よって「閾値=○○億円」と問われたら答えは**『円閾値では切れない。TOPIX区分で切る』**。")
    L.append("")

    L.append("## ① TOPIX規模区分 別 EV（出口=翌引け 一律。採用エッジ①Bの母体）")
    L.append("")
    L.append("⚠️ この表は全規模に**一律「翌引け」出口**を当てている。中型(①B)の正しい出口は引けなので妥当だが、")
    L.append("大型は引けが不利な出口（大型は午前で手仕舞う型）。規模ごとの最適出口は次節③を参照。")
    L.append("")
    L.append("| 規模区分(TOPIX) | n | long net EV(引け) | t | 勝率 | 判定 |")
    L.append("|---|---|---|---|---|---|")
    _, by_band = collect_po_long(records, enriched)
    band_verdict = {
        "小型": "負（PO翌日ロングは効かない）",
        "中型": "✅ **①B 本体**（引け FDR✅/OOS+1.52%）",
        "大型": "引けは不利な出口。最適出口でも母数極小（①A保留）",
    }
    for b in SCALE_BANDS:
        s = stat(by_band[b])
        L.append(f"| {b} | {s['n']} | {s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% | {band_verdict[b]} |")
    L.append("")

    L.append("## ② 規模 × 出口時刻（GD限定）と 規模別の最適出口")
    L.append("")
    L.append("「サイズで最適出口が違う」ことの検証。GD(寄り≤-0.5%)限定・long往復0.20% net。")
    L.append("9:05〜11:30は分足由来(2024-05以降, n小)、引けは日足で全期間。")
    L.append("")
    by_se = collect_size_by_exit(records, enriched)
    L.append("| 規模＼出口 | " + " | ".join(label for label, _ in EXIT_FIELDS) + " |")
    L.append("|" + "---|" * (len(EXIT_FIELDS) + 1))
    for b in SCALE_BANDS:
        cells = []
        for label, _ in EXIT_FIELDS:
            s = stat(by_se[b][label])
            cells.append(f"{s['ev']:+.2f}%(n{s['n']})" if s["n"] else "—")
        L.append(f"| {b} | " + " | ".join(cells) + " |")
    L.append("")
    L.append("**規模別の最適出口**（n≥10 の出口の中で最大EV。大型は n が小さいため n≥3 で参考表示）:")
    L.append("")
    L.append("| 規模 | 最適出口 | EV | t | n | 勝率 | コメント |")
    L.append("|---|---|---|---|---|---|---|")
    opt_comment = {
        "大型": "9:30以降は逆行。午前で手仕舞う型だが分足母数極小で確証なし（①A保留）",
        "中型": "午前から強く引けまで持続。**引けがFDR★通過**＝①B本体",
        "小型": "どの出口も負〜フラット。エッジなし",
    }
    for b in SCALE_BANDS:
        be = best_exit(by_se[b], min_n=10) or best_exit(by_se[b], min_n=3)
        if be is None:
            L.append(f"| {b} | — | — | — | — | — | {opt_comment[b]} |")
            continue
        label, s = be
        L.append(f"| {b} | {label} | {s['ev']:+.2f}% | {s['t']:+.2f} | {s['n']} | "
                 f"{s['win']:.0f}% | {opt_comment[b]} |")
    L.append("")
    L.append("→ **最適出口で評価しても、頑健に立つのは中型(引け)だけ**。大型は最良の午前出口でも n4〜6 で")
    L.append("  確証が立たず、引けでは逆行。小型はどの出口も負。詳細マトリクス: `reports/po_scale_timing.md`。")
    L.append("")

    L.append("## ③ 時価総額(億円) 別 EV（出口=翌引け 一律。同じ母集団を円で刻み直す）")
    L.append("")
    L.append("| 時価総額帯 | n | long net EV | t | 勝率 |")
    L.append("|---|---|---|---|---|")
    by_mc, _ = collect_po_long(records, enriched)
    best_t = 0.0
    for _, _, label in MC_BRACKETS:
        s = stat(by_mc[label])
        if abs(s["t"]) > abs(best_t):
            best_t = s["t"]
        L.append(f"| {label} | {s['n']} | {s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% |")
    L.append("")
    L.append(f"→ **円で刻むと t>2 の帯が一つも無い**（最大でも |t|={abs(best_t):.2f}）。")
    L.append("  TOPIX中型(+1.14%/t+3.33) が円換算で広く散らばり、同じ円帯の小型・大型に希釈されるため。")
    L.append("  = エッジは『規模の円閾値』ではなく『TOPIX中型というメンバーシップ』が担っている。")
    L.append("")

    L.append("## ④ なぜ円で切れないか: TOPIX区分の円レンジ重複（PO universe 実測）")
    L.append("")
    L.append("| 区分 | n | min | 25% | 中央値 | 75% | max | (単位: 億円) |")
    L.append("|---|---|---|---|---|---|---|---|")
    ranges = scale_band_mc_ranges(records, master)
    for b in SCALE_BANDS:
        r = ranges.get(b)
        if r:
            L.append(f"| {b} | {r['n']} | {r['min']:.0f} | {r['p25']:.0f} | "
                     f"{r['median']:.0f} | {r['p75']:.0f} | {r['max']:.0f} | |")
    L.append("")
    L.append("- **小型** が最大1兆超まで、**中型** が808億〜2.2兆、**大型** が2,543億〜9.4兆と、")
    L.append("  帯が大きく重なる。例えば「5,000億」の銘柄は小型・中型・大型のどれにもあり得る。")
    L.append("- ∴ CLAUDE.md/正本の旧表現『中型≈300億-1兆』は**ラフな近似で実体とズレる**（中型中央値は約4,200億）。")
    L.append("  正確には『中型=TOPIX Mid400』であって円レンジでは定義できない。")
    return "\n".join(L)


if __name__ == "__main__":
    records = load_records()
    enriched = load_enriched()
    master = load_master_records()
    report = build_report(records, enriched, master)
    REPORT_PATH.write_text(report)
    print(f"wrote {REPORT_PATH}")

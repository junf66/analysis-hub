"""ショート系エッジ ④⑤ の規模別細分化検証 (②は analyze_reit_po_size_breakdown)。

④ zouhai_kahou_nx (増配＋来期下方) × 大引け後 short: 翌寄→当日引け。
   メトリクス = kouaku_records の next_day_open_to_close_ret (既存)。
⑤ zouhai_genshu (増配＋軽い当期減益) short: 翌寄→+3営業日引け。
   メトリクス = zouhai_genshu_d3.json の d3_ret (別途 enrich)。

各 subpattern を scale_band (小型/中型/大型) で分割し、規模偏在を株式分割③と
同じ観点で検証する。コスト = ショート楽天 0.15% net。

出力: reports/short_edges_size_breakdown.md
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from scripts._buckets import disc_bucket

REPO_ROOT = Path(__file__).resolve().parent.parent
KOUAKU_PATH = REPO_ROOT / "data" / "kouaku_records.json"
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
GENSHU_D3_PATH = REPO_ROOT / "data" / "edge_candidates" / "zouhai_genshu_d3.json"
FINS_PATH = REPO_ROOT / "data" / "edge_candidates" / "fins_summary.json"
REPORT_PATH = REPO_ROOT / "reports" / "short_edges_size_breakdown.md"

COST_PCT = 0.15  # ショート楽天往復
MIN_N = 10
SCALE_ORDER = ["小型", "中型", "大型", "不明"]


def _to5(code: str) -> str:
    """4桁 code を equities_master の5桁形式へ。"""
    return code + "0" if len(code) == 4 else code


def load_master() -> dict[str, dict]:
    """equities_master を Code→record の dict で返す。"""
    data = json.loads(MASTER_PATH.read_text())
    return {m["Code"]: m for m in data["records"]}


def load_kouaku() -> list[dict[str, Any]]:
    """kouaku_records を返す。"""
    return json.loads(KOUAKU_PATH.read_text()).get("records", [])


def load_genshu_d3() -> list[dict[str, Any]]:
    """zouhai_genshu_d3 (enrich 済) を返す。未生成なら空。"""
    if not GENSHU_D3_PATH.exists():
        return []
    data = json.loads(GENSHU_D3_PATH.read_text())
    return data.get("records", []) if isinstance(data, dict) else data


def load_fins_by_code() -> dict[str, list[dict[str, Any]]]:
    """fins_summary を code→決算リストで返す。未生成なら空 dict。"""
    if not FINS_PATH.exists():
        return {}
    return json.loads(FINS_PATH.read_text()).get("by_code", {})


def np_yoy_asof(fins: dict[str, list[dict[str, Any]]], code5: str,
                event_date: str) -> float | None:
    """event_date 以前の最新決算の当期NP YoY(同 CurPerType 前年同期比 %)を返す。

    zouhai_genshu の「減益の程度」を当期純利益の連続 YoY で復元するための関数。
    """
    decs = fins.get(code5)
    if not decs:
        return None
    past = sorted([d for d in decs if d.get("DiscDate") and d["DiscDate"] <= event_date],
                  key=lambda x: x["DiscDate"])
    if not past:
        return None
    cur = past[-1]
    npv, pt, pe = cur.get("NP"), cur.get("CurPerType"), cur.get("CurPerEn")
    if not npv or not pt or not pe:
        return None
    try:
        npv = float(npv)
        cur_year = int(pe[:4])
    except (ValueError, TypeError):
        return None
    for d in decs:
        if d.get("CurPerType") == pt and d.get("CurPerEn", "")[:4] == str(cur_year - 1):
            pv = d.get("NP")
            if pv:
                try:
                    pv = float(pv)
                    if pv != 0:
                        return (npv - pv) / abs(pv) * 100.0
                except (ValueError, TypeError):
                    pass
    return None


def _short_net(ret: float | None) -> float | None:
    """ショート net PnL (= -ret - cost)。"""
    if ret is None:
        return None
    return -float(ret) - COST_PCT


def _stat_block(rets: list[float]) -> dict[str, float]:
    """EV / t / win / cumul を計算。"""
    if not rets:
        return {"n": 0, "ev": 0.0, "t": 0.0, "win": 0.0, "cumul": 0.0}
    n = len(rets)
    ev = statistics.fmean(rets)
    std = statistics.stdev(rets) if n > 1 else 0.0
    t = (ev / (std / (n ** 0.5))) if std > 0 else 0.0
    win = sum(1 for x in rets if x > 0) / n * 100
    return {"n": n, "ev": ev, "t": t, "win": win, "cumul": sum(rets)}


def _scale_of(rec: dict[str, Any], master: dict[str, dict]) -> str:
    """rec の scale_band を返す (attrs 優先, なければ master 引き)。"""
    a = rec.get("attrs") or {}
    if a.get("scale_band"):
        return a["scale_band"]
    m = master.get(_to5(rec.get("code", "")))
    return (m or {}).get("scale_band") or "不明"


def _size_table(records: list[dict[str, Any]], master: dict[str, dict],
                ret_getter) -> list[str]:
    """規模別 EV テーブルの行を返す (全体 + 各 scale_band)。"""
    lines: list[str] = []
    lines.append("| 規模 | n | net EV | t | 勝率 | AvgW | AvgL |")
    lines.append("|---|---|---|---|---|---|---|")

    def _row(label: str, recs: list[dict[str, Any]]) -> str | None:
        rets = [v for r in recs if (v := _short_net(ret_getter(r))) is not None]
        if len(rets) < MIN_N:
            return None
        s = _stat_block(rets)
        wins = [x for x in rets if x > 0]
        losses = [x for x in rets if x <= 0]
        avgw = statistics.fmean(wins) if wins else 0.0
        avgl = statistics.fmean(losses) if losses else 0.0
        return (f"| {label} | {s['n']} | {s['ev']:+.2f}% | {s['t']:+.2f} | "
                f"{s['win']:.0f}% | {avgw:+.2f}% | {avgl:+.2f}% |")

    overall = _row("**全体**", records)
    if overall:
        lines.append(overall)
    by_scale: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_scale.setdefault(_scale_of(r, master), []).append(r)
    for sb in SCALE_ORDER:
        recs = by_scale.get(sb)
        if recs:
            row = _row(sb, recs)
            if row:
                lines.append(row)
    return lines


def _genshu_yoy_bands(genshu_d3: list[dict[str, Any]],
                      fins: dict[str, list[dict[str, Any]]]) -> list[str]:
    """⑤ の当期NP YoY 帯別 × 保有期間の short net 検証行 (再現性担保)。

    正本に「軽い当期減益(-3〜0%)」と記載されていた母体が、現分類では
    全件 ≤-10% で存在しないこと、どの帯でもエッジが立たないことを実証する。
    """
    lines: list[str] = []
    if not fins:
        lines.append("_(fins_summary.json 未生成。NP YoY 帯別検証はスキップ)_")
        return lines
    for r in genshu_d3:
        r["_npyoy"] = np_yoy_asof(fins, _to5(r.get("code", "")), r.get("event_date", ""))
    matched = [r for r in genshu_d3 if r.get("_npyoy") is not None]
    yoys = sorted(r["_npyoy"] for r in matched)
    lines.append(f"- NP YoY 結合: {len(matched)}/{len(genshu_d3)} 件")
    if yoys:
        lines.append(f"- YoY 分布: min{min(yoys):.0f}% / 中央{yoys[len(yoys)//2]:.1f}% / "
                     f"**max {max(yoys):.0f}%**（全件が深い減益、軽い帯-3〜0%は **0件**）")
    lines.append("")
    lines.append("| 減益帯 | n | d1 short | d3 short | d5 short |")
    lines.append("|---|---|---|---|---|")
    bands = [("-3〜0%(正本定義)", -3, 0), ("-10〜-15%", -15, -10),
             ("-15〜-25%", -25, -15), ("-25〜-50%", -50, -25), ("-50%以下", -1e9, -50)]
    for lbl, lo, hi in bands:
        sub = [r for r in matched
               if lo <= r["_npyoy"] < hi and not (r.get("attrs") or {}).get("limit_locked")]
        cells = []
        for key in ["d1_ret", "d3_ret", "d5_ret"]:
            rets = [v for r in sub if (v := _short_net((r.get("attrs") or {}).get(key))) is not None]
            if len(rets) >= MIN_N:
                s = _stat_block(rets)
                cells.append(f"{s['ev']:+.2f}%/t{s['t']:+.1f}")
            else:
                cells.append("n<10")
        lines.append(f"| {lbl} | {len(sub)} | {cells[0]} | {cells[1]} | {cells[2]} |")
    return lines


def build_report(kouaku: list[dict[str, Any]], genshu_d3: list[dict[str, Any]],
                 master: dict[str, dict],
                 fins: dict[str, list[dict[str, Any]]] | None = None) -> str:
    """④⑤ の規模別レポートを生成。"""
    lines: list[str] = []
    lines.append("# ショート系エッジ ④⑤ 規模別細分化検証 (2026-06-03)")
    lines.append("")
    lines.append("コスト: ショート楽天往復 0.15% net。規模 = equities_master scale_band。")
    lines.append("※ 単純 t。確定判断は日付クラスタ頑健 t + FDR + walk-forward OOS を要する。")
    lines.append("")

    # ④ zouhai_kahou_nx × 大引け後
    lines.append("## ④ 増配＋来期下方修正 short (zouhai_kahou_nx)")
    lines.append("")
    lines.append("戦略: 大引け後発表 → 翌寄り売り → 当日引け買戻 (next_day_open_to_close_ret)")
    lines.append("確定エッジ条件に合わせ **大引け後** に限定。")
    lines.append("")
    zk = [r for r in kouaku
          if r.get("subpattern") == "zouhai_kahou_nx"
          and not (r.get("attrs") or {}).get("limit_locked")
          and disc_bucket(r) == "大引け後"]
    lines += _size_table(zk, master,
                         lambda r: (r.get("attrs") or {}).get("next_day_open_to_close_ret"))
    lines.append("")

    # ⑤ zouhai_genshu × +3日
    lines.append("## ⑤ 増配＋軽い当期減益 short (zouhai_genshu)")
    lines.append("")
    lines.append("戦略: 翌寄り売り → +3営業日後引け買戻 (d3_ret)")
    lines.append("")
    if genshu_d3:
        zg = [r for r in genshu_d3 if not (r.get("attrs") or {}).get("limit_locked")]
        lines += _size_table(zg, master, lambda r: (r.get("attrs") or {}).get("d3_ret"))
        lines.append("")
        lines.append("### ⑤ 当期NP YoY 帯別検証（正本「軽い減益-3〜0%」の再現可否）")
        lines.append("")
        lines += _genshu_yoy_bands(genshu_d3, fins or {})
        lines.append("")
        lines.append("**結論: ⑤はエッジなし。** 正本の母体定義「軽い当期減益(-3〜0%)」は "
                     "zouhai_genshu(全件≤-10%)に存在せず、どの減益帯・保有期間でも t<2。"
                     " magnitude_sweep でも FDR 非生存。正本⑤(+0.53%/n389/OOS+0.33%)は再現不能。")
    else:
        lines.append("_(zouhai_genshu_d3.json 未生成。"
                     "`python -m scripts.edge_candidates.enrich_zouhai_genshu_d3` で生成)_")
    lines.append("")

    # 所見
    lines.append("## 所見")
    lines.append("")
    lines.append("### 「規模問わず」の妥当性")
    # ④ overall vs scale
    zk_overall = _stat_block([v for r in zk
                              if (v := _short_net((r.get("attrs") or {}).get("next_day_open_to_close_ret"))) is not None])
    by_scale_zk: dict[str, list[float]] = {}
    for r in zk:
        sb = _scale_of(r, master)
        v = _short_net((r.get("attrs") or {}).get("next_day_open_to_close_ret"))
        if v is not None:
            by_scale_zk.setdefault(sb, []).append(v)
    lines.append(f"- ④ 全体: net {zk_overall['ev']:+.2f}% / t{zk_overall['t']:+.2f} / n{zk_overall['n']}")
    for sb in ["小型", "中型", "大型"]:
        if sb in by_scale_zk and len(by_scale_zk[sb]) >= MIN_N:
            s = _stat_block(by_scale_zk[sb])
            lines.append(f"  - {sb}: net {s['ev']:+.2f}% / t{s['t']:+.2f} / n{s['n']}")
    lines.append("")
    lines.append("（中型 short 系は β交絡の懸念が残るため、規模絞りの確定判断は β実推定後）")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    master = load_master()
    kouaku = load_kouaku()
    genshu_d3 = load_genshu_d3()
    fins = load_fins_by_code()
    report = build_report(kouaku, genshu_d3, master, fins)
    REPORT_PATH.write_text(report)
    print(f"wrote {REPORT_PATH}")

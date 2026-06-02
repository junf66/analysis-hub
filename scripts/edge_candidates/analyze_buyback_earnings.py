"""自社株買い×同日決算を「減益/増益の程度」別に検証する (キッコーマン型の本格テスト)。

kouaku 分類器が決算 NP YoY を ±10% 閾値でタグ化するため、軽い増減益(±10%以内)+自社株買いが
未分析だった死角を、連続値 np_yoy で細分割して EV を測る。
出口: d0=当日寄→引(引け, raw) / +1/+3/+5日 (TOPIX超過α)。約定可能(大引け後/引け間際/寄前)cut つき。
ロング往復0.20%控除 + 日付クラスタ頑健t + 全セル横断 BH-FDR + walk-forward OOS。

出力: reports/buyback_earnings.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from analyzers.stats import benjamini_hochberg
from scripts._atomic import atomic_write_text
from scripts._buckets import disc_bucket_from_time
from scripts.edge_candidates import lib

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IN_PATH = REPO_ROOT / "data" / "edge_candidates" / "buyback_earnings.json"
OUT_PATH = REPO_ROOT / "reports" / "buyback_earnings.md"
TRADABLE = {"大引け後", "引け間際", "寄前"}
MIN_N = 30
# (ラベル, metric, 説明)。d0 は intraday=raw、d1/d3/d5 は α。
EXITS = [("引け(寄→引)", "d0_ret"), ("+1日α", "alpha_d1_ret"),
         ("+3日α", "alpha_d3_ret"), ("+5日α", "alpha_d5_ret")]


def yoy_band(y: float | None) -> str | None:
    """決算 NP YoY% を程度バンドに分類する (None は対象外)。"""
    if y is None:
        return None
    if y <= -10:
        return "重減 ≤-10%"
    if y <= -5:
        return "中減 -10〜-5%"
    if y < 0:
        return "軽減 -5〜0%"      # ← キッコーマン型核心(5%以内減益)
    if y < 5:
        return "軽増 0〜+5%"
    if y < 10:
        return "中増 +5〜+10%"
    return "増益 ≥+10%"


_BAND_ORDER = ["重減 ≤-10%", "中減 -10〜-5%", "軽減 -5〜0%", "軽増 0〜+5%", "中増 +5〜+10%", "増益 ≥+10%"]


def _filter(records: list[dict[str, Any]], tradable_only: bool) -> list[dict[str, Any]]:
    if not tradable_only:
        return records
    return [r for r in records
            if disc_bucket_from_time((r.get("attrs") or {}).get("disc_time")) in TRADABLE]


def build_cells(records: list[dict[str, Any]]) -> dict[str, dict[str, dict]]:
    """band → exit_label → stats。全セル横断で BH-FDR。"""
    out: dict[str, dict[str, dict]] = {}
    flat = []
    groups: dict[str, list[dict]] = {}
    for r in records:
        b = yoy_band((r.get("attrs") or {}).get("np_yoy"))
        if b:
            groups.setdefault(b, []).append(r)
    for band, recs in groups.items():
        out[band] = {}
        for label, metric in EXITS:
            s = lib._exit_stats(recs, metric, lib.LONG_COST)
            if s is None:
                continue
            s["fdr_significant"] = False
            out[band][label] = s
            if s["n"] >= MIN_N:
                flat.append(s)
    if flat:
        for s, f in zip(flat, benjamini_hochberg([s["p"] for s in flat], 0.05)):
            s["fdr_significant"] = f
    return out


def _verdict(s: dict) -> str:
    if s["n"] < MIN_N:
        return "—(n<30)"
    if s["net_ev"] > 0.5 and s["t_clust"] > 2.0 and s.get("fdr_significant") and (s["oos"] or 0) > 0:
        return "★通過"
    if s["net_ev"] > 0 and s["t_clust"] > 2.0:
        return "△(FDR前のみ)"
    if s["net_ev"] <= 0 or s["t_clust"] < -1:
        return "✕"
    return "—"


def _table(cells: dict[str, dict[str, dict]]) -> list[str]:
    L = ["| NP YoY帯 | 出口 | n | net EV | t_clust | 勝率 | OOS | FDR | 判定 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for band in _BAND_ORDER:
        for label, _ in EXITS:
            s = cells.get(band, {}).get(label)
            if not s:
                continue
            mark = "★" if s.get("fdr_significant") else ""
            oos = s["oos"] if s["oos"] is not None else 0.0
            L.append(f"| {band} | {label} | {s['n']} | {s['net_ev']:+.2f}% | {s['t_clust']:+.2f} | "
                     f"{s['win']:.0f}% | {oos:+.2f}% | {mark} | {_verdict(s)} |")
    return L


_DECLINE_CUTS = [(-3.0, "3%以内減益(-3〜0%)"), (-5.0, "5%以内減益(-5〜0%)"),
                 (-10.0, "10%以内減益(-10〜0%)")]


def build_cumulative(records: list[dict[str, Any]]) -> list[str]:
    """「X%以内減益」の累積cut (ユーザーの考える閾値軸) で EV を出す表。"""
    L = ["| 減益閾値 | 出口 | n | net EV | t_clust | 勝率 | OOS |", "|---|---|---|---|---|---|---|"]
    for thr, label in _DECLINE_CUTS:
        sub = [r for r in records
               if (y := (r.get("attrs") or {}).get("np_yoy")) is not None and thr <= y < 0]
        for ex_label, metric in EXITS:
            s = lib._exit_stats(sub, metric, lib.LONG_COST)
            if not s:
                continue
            oos = s["oos"] if s["oos"] is not None else 0.0
            L.append(f"| {label} | {ex_label} | {s['n']} | {s['net_ev']:+.2f}% | {s['t_clust']:+.2f} | "
                     f"{s['win']:.0f}% | {oos:+.2f}% |")
    return L


def write_report(records: list[dict[str, Any]], *, out_path: Path = OUT_PATH) -> Path:
    """程度別EVレポートを Markdown 出力。"""
    import datetime
    n_yoy = sum(1 for r in records if (r.get("attrs") or {}).get("np_yoy") is not None)
    L = [f"# 自社株買い×同日決算 程度別EV検証 (キッコーマン型) ({datetime.date.today()})", "",
         f"対象 {len(records)}件 (np_yoy付与 {n_yoy})。ロング往復0.20%控除 / d0=引け(raw) ・"
         "+N日=TOPIX超過α / 日付クラスタ頑健t / 全セル横断BH-FDR / walk-forward OOS。", "",
         "背景: 分類器は決算NP YoY ±10%閾値でタグ化するため、軽い増減益(±10%以内)+自社株買いは"
         "kouakuから脱落していた(=未分析の死角)。本表で連続値np_yoyにより程度の軸を復活させる。", "",
         "## 1. 全件 (開示時刻問わず)", ""]
    L += _table(build_cells(records))
    L += ["", "## 2. 約定可能のみ (大引け後/引け間際/寄前 = 翌寄りエントリー可)", ""]
    L += _table(build_cells(_filter(records, True)))
    L += ["", "## 3. 「X%以内減益」累積cut (約定可能のみ)", ""]
    L += build_cumulative(_filter(records, True))
    L += ["", "## 判定メモ",
          "- 「軽減 -5〜0%」が**キッコーマン型(5%以内の軽い減益+自社株買い)**。従来未分析の死角。",
          "- d0(引け)はraw、+N日はα。★=FDR生存かつOOSプラスかつ t>2 かつ net>0.5%。",
          "- 既知: 重減<-10%(=jisha_genshu)は別途『約定可能条件でロング期待薄』と判定済。本表で程度勾配を再確認。"]
    atomic_write_text(out_path, "\n".join(L))
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-path", type=Path, default=IN_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    records = json.loads(args.in_path.read_text())["records"]
    out = write_report(records, out_path=args.out)
    kik = [r for r in records if r["code"] in ("2801", "28010")]
    print(f"[buyback_earnings] n={len(records)} / Kikkoman events={len(kik)} → wrote {out}")


if __name__ == "__main__":
    main()

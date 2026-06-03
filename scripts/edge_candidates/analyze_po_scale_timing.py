"""PO発表翌日ロングを「規模 × 出口時刻」で厳格検証する (申し送り#4)。

po_records(announce) に po_enriched(規模band/信用区分/翌日引け) を結合し、
普通株を 規模(大型/中型/小型) × 出口 {9:05,9:10,9:15,9:30,10:00,11:30,引け} で
EV/クラスタt/勝率/OOS を算出。全セル横断 BH-FDR。GD(翌日gap≤-0.5%)cut つき。
ロング往復0.20%控除。同一日複数案件の非独立は event_date クラスタtで補正。

出力: reports/po_scale_timing.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from analyzers.stats import benjamini_hochberg
from scripts._atomic import atomic_write_text
from scripts.edge_candidates import lib

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PO_PATH = REPO_ROOT / "data" / "po_records.json"
ENR_PATH = REPO_ROOT / "data" / "edge_candidates" / "po_enriched.json"
OUT_PATH = REPO_ROOT / "reports" / "po_scale_timing.md"
MIN_N = 30
EXITS = [("9:05", "next_day_905_ret"), ("9:10", "next_day_910_ret"), ("9:15", "next_day_915_ret"),
         ("9:30", "next_day_930_ret"), ("10:00", "next_day_1000_ret"),
         ("11:30", "next_day_morning_ret"), ("引け", "next_day_open_to_close_ret")]
_SCALE_ORDER = ["大型", "中型", "小型"]


def merge(po_recs: list[dict[str, Any]], enr: dict[str, dict]) -> list[dict[str, Any]]:
    """announce 普通株に enriched 属性(規模/引け)を結合して返す。"""
    out = []
    for r in po_recs:
        if r.get("stage") != "announce" or r.get("po_type") != "普通":
            continue
        e = enr.get(r["id"]) or {}
        a = r.setdefault("attrs", {})
        if e.get("next_day_open_to_close_ret") is not None:
            a["next_day_open_to_close_ret"] = e["next_day_open_to_close_ret"]
        r["scale_band"] = e.get("scale_band")
        out.append(r)
    return out


def build(records: list[dict[str, Any]]) -> dict[str, dict[str, dict]]:
    """規模band → 出口 → stats。全セル横断 BH-FDR。"""
    out: dict[str, dict[str, dict]] = {}
    flat = []
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(r.get("scale_band") or "不明", []).append(r)
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
    L = ["| 規模 | 出口 | n | net EV | t_clust | 勝率 | OOS | FDR | 判定 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for band in _SCALE_ORDER + [b for b in cells if b not in _SCALE_ORDER]:
        for label, _ in EXITS:
            s = cells.get(band, {}).get(label)
            if not s:
                continue
            mark = "★" if s.get("fdr_significant") else ""
            oos = s["oos"] if s["oos"] is not None else 0.0
            L.append(f"| {band} | {label} | {s['n']} | {s['net_ev']:+.2f}% | {s['t_clust']:+.2f} | "
                     f"{s['win']:.0f}% | {oos:+.2f}% | {mark} | {_verdict(s)} |")
    return L


def write_report(records: list[dict[str, Any]], *, out_path: Path = OUT_PATH) -> Path:
    """PO 規模×時刻 検証レポートを出力。"""
    import datetime
    gd = [r for r in records if (r.get("attrs") or {}).get("gap_pct") is not None
          and r["attrs"]["gap_pct"] <= -0.5]
    L = [f"# PO発表翌日ロング 規模×出口時刻 検証 ({datetime.date.today()})", "",
         f"announce 普通株 {len(records)}件 (うち翌日GD≤-0.5% {len(gd)}件)。ロング往復0.20%控除 / "
         "日付クラスタ頑健t / 全セル横断BH-FDR / walk-forward OOS。", "",
         "規模 = /equities/master ScaleCat (大型=Core30+Large70 / 中型=Mid400 / 小型=その他)。",
         "注: 9:05〜11:30 は分足由来で 2024-05 以降のみ(n小)。引けは日足で全期間。", "",
         "## 1. 全 announce 普通株 (規模 × 出口)", ""]
    L += _table(build(records))
    L += ["", "## 2. 翌日GD(gap≤-0.5%) 限定 (発表翌日エッジの本命条件)", ""]
    L += _table(build(gd))
    L += ["", "## メモ",
          "- 別セッション所見『大型(≥300億)・GD・9:10 で +0.66%/71%』を規模band(ScaleCat)で再現検証。",
          "- ★=FDR生存&OOSプラス&t>2&net>0.5%。△=FDR前のみ有意(過剰最適化注意)。",
          "- 9:30以降のEV急減・大型1兆超(Core30)の弱さ等、所見との整合を確認する。"]
    atomic_write_text(out_path, "\n".join(L))
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--po", type=Path, default=PO_PATH)
    ap.add_argument("--enr", type=Path, default=ENR_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    po = json.loads(args.po.read_text())
    recs = po.get("records", po if isinstance(po, list) else [])
    enr = json.loads(args.enr.read_text())["by_id"]
    merged = merge(recs, enr)
    out = write_report(merged, out_path=args.out)
    print(f"[po_scale_timing] announce普通 {len(merged)}件 → wrote {out}")


if __name__ == "__main__":
    main()

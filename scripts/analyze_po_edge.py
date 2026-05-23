"""data/po_records.json (共通スキーマ展開済) から PO エッジ統計とレポート生成。

po-tracker セッションで検証済の既知 3 エッジ
  1. 発表翌日エッジ (普通株 announce → next_day_910_ret long)        EV +0.66%
  2. 受渡日 GD エッジ (普通株 deliver, gap_pct<=-0.5 → open→close long) EV +0.80%
  3. リートエッジ (REIT decide → -ret_close short)                     EV +1.12%
を共通スキーマ経由で再現できることを目的とする。

partition 軸:
  event_type (po_announce/po_decide/po_deliver) × po_type (普通/リート)
  × lending_type (貸借/信用/売禁等) × DiscTime 相当 (PO は基本 announce のみ TDnet 経由なので簡略)

EV 評価から除外:
  - legacy_record
  - concurrent_earnings
  - status != complete (announce のみ "nextday" も許容)

出力:
  reports/po_analysis.md
  reports/po_by_stage/{stage}.md
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from analyzers.po_edges import EdgeStat, _stats  # noqa: E402  (内部関数を流用)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_ROOT / "data" / "po_records.json"
REPORT_DIR = REPO_ROOT / "reports"
STAGE_REPORT_DIR = REPORT_DIR / "po_by_stage"

GD_THRESHOLD_PCT = -0.5

# stage ごとに測る metric リスト
_METRIC_FIELDS_BY_STAGE: dict[str, list[tuple[str, str]]] = {
    "announce": [
        ("gap_pct", "GAP (前日終→翌寄り)"),
        ("next_day_905_ret", "翌寄り→09:05"),
        ("next_day_910_ret", "翌寄り→09:10"),
        ("next_day_915_ret", "翌寄り→09:15"),
        ("next_day_930_ret", "翌寄り→09:30"),
        ("next_day_1000_ret", "翌寄り→10:00"),
        ("next_day_morning_ret", "翌寄り→前場引"),
        ("next_day_open_to_high_ret", "翌寄り→翌高"),
    ],
    "decide": [
        ("ret_open", "next_open→決定日寄り"),
        ("ret_close", "next_open→決定日引け"),
    ],
    "deliver": [
        ("gap_pct", "受渡日 GAP (前日終→寄り)"),
        ("next_day_open_to_close_ret", "受渡日 寄り→引け"),
    ],
}


def _is_eligible_for_ev(rec: dict[str, Any]) -> bool:
    """EV 評価対象か。legacy / 決算同時 / 株式分割窓 は除外。

    status は announce では nextday も許容、それ以外は complete のみ。
    """
    if rec.get("legacy_record"):
        return False
    if rec.get("concurrent_earnings"):
        return False
    if rec.get("split_within_po_window"):
        return False
    status = rec.get("status")
    if rec.get("stage") == "announce":
        return status in ("complete", "nextday")
    return status == "complete"


def _collect_metric(records: Sequence[dict[str, Any]], field: str, *, negate: bool = False) -> list[float]:
    out: list[float] = []
    for r in records:
        v = (r.get("attrs") or {}).get(field)
        if v is None:
            continue
        f = float(v)
        out.append(-f if negate else f)
    return out


def _stat_lines_for_stage(records: list[dict[str, Any]], stage: str, label: str) -> list[str]:
    fields = _METRIC_FIELDS_BY_STAGE.get(stage, [])
    lines = [f"### {label}  n={len(records)}", "", "```"]
    for key, name in fields:
        vals = _collect_metric(records, key)
        lines.append(_stats(name, vals).format())
    lines.append("```")
    return lines


# ---- 既知 3 エッジ (再現検証) ---------------------------------------------

def known_edge_announce(records: Sequence[dict[str, Any]]) -> dict[str, EdgeStat]:
    """発表翌日エッジ: 普通株 announce、翌寄りロング → next_day_XXX_ret。"""
    target = [r for r in records if r.get("stage") == "announce" and r.get("po_type") == "普通" and _is_eligible_for_ev(r)]
    out: dict[str, EdgeStat] = {}
    for key, name in _METRIC_FIELDS_BY_STAGE["announce"]:
        if key == "gap_pct":
            continue
        vals = _collect_metric(target, key)
        out[key] = _stats(f"発表翌日(普通) 翌寄り→{name.split('→',1)[-1]}", vals)
    return out


def known_edge_delivery_gd(records: Sequence[dict[str, Any]]) -> EdgeStat:
    """受渡日 GD エッジ: 普通株 deliver, gap_pct<=-0.5, 寄り→引けロング。"""
    target = [
        r for r in records
        if r.get("stage") == "deliver"
        and r.get("po_type") == "普通"
        and _is_eligible_for_ev(r)
        and (r.get("attrs") or {}).get("gap_pct") is not None
        and float((r.get("attrs") or {}).get("gap_pct")) <= GD_THRESHOLD_PCT
    ]
    vals = _collect_metric(target, "next_day_open_to_close_ret")
    return _stats(f"受渡日GD(普通) gap<={GD_THRESHOLD_PCT}% 寄り→引け", vals)


def known_edge_reit_short(records: Sequence[dict[str, Any]]) -> EdgeStat:
    """リートエッジ: REIT decide, next_open ショート → 決定日引け買戻 (= -ret_close)。"""
    target = [r for r in records if r.get("stage") == "decide" and r.get("po_type") == "リート" and _is_eligible_for_ev(r)]
    vals = _collect_metric(target, "ret_close", negate=True)
    return _stats("リート next_open→決定日引け ショート", vals)


# ---- partition 集計 -----------------------------------------------------

def _partition_records(records: Sequence[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if not _is_eligible_for_ev(r):
            continue
        key = (r.get("stage", "?"), r.get("po_type") or "?", r.get("lending_type") or "?")
        out[key].append(r)
    return out


def build_main_report(payload: dict[str, Any]) -> str:
    """全 PO events から既知 3 エッジ + ステージ別統計 + partition クロスを 1 つの md にまとめる。"""
    records = payload.get("records", [])
    eligible = [r for r in records if _is_eligible_for_ev(r)]
    excluded = len(records) - len(eligible)

    lines: list[str] = []
    lines.append("# PO エッジ検証 (po-tracker)")
    lines.append("")
    lines.append(
        f"events 全 {len(records)} 件中 EV 評価対象 **{len(eligible)} 件** "
        f"(legacy/決算同時/分割窓/status不適格を {excluded} 件除外)"
    )
    lines.append("")
    lines.append(f"  stage_counts: {payload.get('stage_counts', {})}")
    lines.append(f"  type_counts:  {payload.get('type_counts', {})}")
    lines.append("")

    # 既知 3 エッジ再現
    lines.append("## 既知 3 エッジ再現")
    lines.append("")
    lines.append("po-tracker セッション時点の参照 EV: 発表翌日 +0.66% / 受渡日GD +0.80% / リート ショート +1.12%")
    lines.append("")
    lines.append("```")
    for stat in known_edge_announce(records).values():
        lines.append(stat.format())
    lines.append(known_edge_delivery_gd(records).format())
    lines.append(known_edge_reit_short(records).format())
    lines.append("```")
    lines.append("")

    # ステージ別統計
    lines.append("## ステージ別")
    lines.append("")
    by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in eligible:
        by_stage[r.get("stage", "?")].append(r)
    for stage in ("announce", "decide", "deliver"):
        recs = by_stage.get(stage, [])
        if not recs:
            continue
        lines.extend(_stat_lines_for_stage(recs, stage, f"stage={stage}"))
        lines.append("")

    # partition 集計
    lines.append("## stage × po_type × lending_type")
    lines.append("")
    lines.append("| stage | po_type | lending | n | 主要 metric (mean, t) |")
    lines.append("|---|---|---|---|---|")

    def _cell(recs: list[dict[str, Any]], stage: str) -> str:
        # ステージ別の主要 metric を 1 つ選んで EV+t を返す
        fields_first = {
            "announce": "next_day_910_ret",
            "decide": "ret_close",
            "deliver": "next_day_open_to_close_ret",
        }
        f = fields_first.get(stage)
        if not f:
            return f"n={len(recs)}"
        vals = _collect_metric(recs, f)
        if len(vals) < 2:
            return f"n={len(vals)}"
        s = _stats(f, vals)
        return f"{f}: {s.mean_pct:+.2f}% (t={s.t_stat:+.2f}, n={s.n})"

    partitioned = _partition_records(records)
    for (stage, ptype, lending), recs in sorted(partitioned.items()):
        if len(recs) < 5:
            continue
        lines.append(f"| {stage} | {ptype} | {lending} | {len(recs)} | {_cell(recs, stage)} |")
    lines.append("")

    return "\n".join(lines)


def build_stage_report(stage: str, records: list[dict[str, Any]]) -> str:
    """指定ステージ (announce/decide/deliver) の events 群を po_type 別に分けて md 化。"""
    eligible = [r for r in records if _is_eligible_for_ev(r)]
    lines: list[str] = []
    lines.append(f"# PO stage = {stage}")
    lines.append("")
    lines.append(f"records: {len(records)}, EV 評価対象: {len(eligible)}")
    lines.append("")
    lines.extend(_stat_lines_for_stage(eligible, stage, f"全件 (eligible)"))
    lines.append("")
    # po_type 別
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in eligible:
        by_type[r.get("po_type") or "?"].append(r)
    for t, recs in sorted(by_type.items()):
        lines.extend(_stat_lines_for_stage(recs, stage, f"po_type={t}"))
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH, help="po_records.json のパス")
    args = ap.parse_args()

    payload = json.loads(args.path.read_text())
    records = payload.get("records", [])

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    STAGE_REPORT_DIR.mkdir(parents=True, exist_ok=True)

    main_md = REPORT_DIR / "po_analysis.md"
    main_md.write_text(build_main_report(payload))
    print(f"wrote {main_md}")

    by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_stage[r.get("stage", "?")].append(r)
    for stage, recs in by_stage.items():
        path = STAGE_REPORT_DIR / f"{stage}.md"
        path.write_text(build_stage_report(stage, recs))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()

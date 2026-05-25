"""data/holdings_records.json (共通スキーマ展開済) から大量保有エッジ統計とレポート生成。

仮説: 大量保有報告書の提出 (5%超など) に対し、銘柄が翌営業日に反応する。
保有目的 (純投資/取引関係/重要提案/M&A関連 ...) や保有者区分 (外資ファンド/
国内ファンド/アクティビスト/事業会社 ...) で反応が異なるかを検証する。

partition 軸:
  purpose_category_jp (保有目的) × holder_category_jp (保有者区分)
  + 単軸 (purpose / holder / gap_label) ブレークダウン

価格タイミング: holdings-tracker 側定義に従う (提出日を起点とした寄り→引け等)。
EV 評価から除外: low_ratio_suspect (保有割合の信頼性が低いレコード)。

出力:
  reports/holdings_analysis.md
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from analyzers.po_edges import EdgeStat, _stats  # noqa: E402  (内部関数を流用)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_ROOT / "data" / "holdings_records.json"
REPORT_DIR = REPO_ROOT / "reports"
REPORT_PATH = REPORT_DIR / "holdings_analysis.md"

PRIMARY_METRIC = "next_day_open_to_close_ret"
MIN_CELL_N = 5

_METRIC_FIELDS: list[tuple[str, str]] = [
    ("gap_pct", "GAP (前日終→寄り)"),
    ("next_day_905_ret", "寄り→09:05"),
    ("next_day_910_ret", "寄り→09:10"),
    ("next_day_915_ret", "寄り→09:15"),
    ("next_day_930_ret", "寄り→09:30"),
    ("next_day_1000_ret", "寄り→10:00"),
    ("next_day_morning_ret", "寄り→前場引"),
    ("next_day_open_to_close_ret", "寄り→引け"),
    ("next_day_open_to_high_ret", "寄り→高値"),
    ("d5_ret", "5 営業日リターン"),
    ("d10_ret", "10 営業日リターン"),
]


def is_eligible_for_ev(rec: dict[str, Any]) -> bool:
    """EV 評価対象か。保有割合の信頼性が低いレコードは除外。"""
    return not rec.get("low_ratio_suspect")


def _collect_metric(records: Sequence[dict[str, Any]], field: str, *, negate: bool = False) -> list[float]:
    out: list[float] = []
    for r in records:
        v = (r.get("attrs") or {}).get(field)
        if v is None:
            continue
        f = float(v)
        out.append(-f if negate else f)
    return out


def _stat_lines(records: Sequence[dict[str, Any]], label: str) -> list[str]:
    lines = [f"### {label}  n={len(records)}", "", "```"]
    for key, name in _METRIC_FIELDS:
        vals = _collect_metric(records, key)
        lines.append(_stats(name, vals).format())
    lines.append("```")
    return lines


def _by(records: Sequence[dict[str, Any]], dim: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        out[r.get(dim) or "?"].append(r)
    return out


def cross_cells(records: Sequence[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """purpose_category_jp × holder_category_jp の eligible セル。"""
    out: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if not is_eligible_for_ev(r):
            continue
        key = (r.get("purpose_category_jp") or "?", r.get("holder_category_jp") or "?")
        out[key].append(r)
    return out


def build_report(payload: dict[str, Any]) -> str:
    """大量保有 events から単軸・クロス集計を 1 つの md にまとめる。"""
    records = payload.get("records", [])
    eligible = [r for r in records if is_eligible_for_ev(r)]
    excluded = len(records) - len(eligible)

    lines: list[str] = []
    lines.append("# 大量保有エッジ検証 (holdings)")
    lines.append("")
    lines.append(
        f"events 全 {len(records)} 件中 EV 評価対象 **{len(eligible)} 件** "
        f"(low_ratio_suspect を {excluded} 件除外)"
    )
    lines.append("")
    lines.append(f"  purpose_counts: {payload.get('purpose_counts', {})}")
    lines.append(f"  holder_counts:  {payload.get('holder_counts', {})}")
    lines.append("")
    lines.append("価格タイミングは holdings-tracker 側定義 (提出日起点の寄り→引け等)。")
    lines.append("")

    lines.append("## 全体")
    lines.append("")
    lines.extend(_stat_lines(eligible, "全件 (eligible)"))
    lines.append("")

    for dim, title in (("purpose_category_jp", "保有目的"), ("holder_category_jp", "保有者区分"), ("gap_label", "GAP ラベル")):
        lines.append(f"## {title}別 ({dim})")
        lines.append("")
        for key, recs in sorted(_by(eligible, dim).items(), key=lambda kv: -len(kv[1])):
            if len(recs) < MIN_CELL_N:
                continue
            lines.extend(_stat_lines(recs, f"{title}={key}"))
            lines.append("")

    # クロス集計 (purpose × holder)
    lines.append("## 保有目的 × 保有者区分 クロス集計")
    lines.append("")
    lines.append(f"n>={MIN_CELL_N} のセルのみ。metric = {PRIMARY_METRIC} (寄り→引け)。")
    lines.append("")
    lines.append("| purpose | holder | n | 寄り→引け EV (t) | GAP EV (t) |")
    lines.append("|---|---|---|---|---|")

    def _cell(recs: list[dict[str, Any]], field: str) -> str:
        vals = _collect_metric(recs, field)
        if len(vals) < 2:
            return f"n={len(vals)}"
        s = _stats(field, vals)
        return f"{s.mean_pct:+.2f}% (t={s.t_stat:+.2f})"

    cross = cross_cells(records)
    for (purpose, holder), recs in sorted(cross.items(), key=lambda kv: -len(kv[1])):
        if len(recs) < MIN_CELL_N:
            continue
        lines.append(
            f"| {purpose} | {holder} | {len(recs)} | "
            f"{_cell(recs, PRIMARY_METRIC)} | {_cell(recs, 'gap_pct')} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH, help="holdings_records.json のパス")
    ap.add_argument("--out", type=Path, default=REPORT_PATH, help="出力 md ファイル")
    args = ap.parse_args()

    payload = json.loads(args.path.read_text())
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_report(payload))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

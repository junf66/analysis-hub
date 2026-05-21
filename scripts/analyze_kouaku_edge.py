"""kouaku_records.json (price 付与済) からエッジ統計とレポートを生成。

出力:
  reports/kouaku_analysis.md
  reports/kouaku_by_subpattern/{subpattern}.md
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from analyzers.po_edges import EdgeStat, _stats  # noqa: E402  (内部関数を流用)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_ROOT / "data" / "kouaku_records.json"
REPORT_DIR = REPO_ROOT / "reports"
SUB_REPORT_DIR = REPORT_DIR / "kouaku_by_subpattern"


_METRIC_FIELDS = [
    ("gap_pct", "GAP (寄付前→翌寄り)"),
    ("next_day_905_ret", "翌寄り→09:05"),
    ("next_day_910_ret", "翌寄り→09:10"),
    ("next_day_915_ret", "翌寄り→09:15"),
    ("next_day_930_ret", "翌寄り→09:30"),
    ("next_day_1000_ret", "翌寄り→10:00"),
    ("next_day_morning_ret", "翌寄り→前場引"),
    ("next_day_open_to_close_ret", "翌寄り→翌引け"),
    ("next_day_open_to_high_ret", "翌寄り→翌高値"),
    ("next_day_open_to_low_ret", "翌寄り→翌安値"),
    ("next_day_full_ret", "前日終→翌引け"),
]


def _collect_metrics(records: list[dict[str, Any]], *, exclude_locked: bool = False) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {k: [] for k, _ in _METRIC_FIELDS}
    for r in records:
        attrs = r.get("attrs") or {}
        if exclude_locked and attrs.get("limit_locked"):
            continue
        for k, _ in _METRIC_FIELDS:
            v = attrs.get(k)
            if v is not None:
                out[k].append(float(v))
    return out


def _stat_lines(records: list[dict[str, Any]], label: str, *, exclude_locked: bool = True) -> list[str]:
    metrics = _collect_metrics(records, exclude_locked=exclude_locked)
    locked = sum(1 for r in records if (r.get("attrs") or {}).get("limit_locked"))
    suffix = f" (limit-lock 除外 {locked} 件)" if exclude_locked and locked else ""
    lines = [f"### {label}  n_records={len(records)}{suffix}", "", "```"]
    for key, name in _METRIC_FIELDS:
        s = _stats(name, metrics[key])
        lines.append(s.format())
    lines.append("```")
    return lines


def _record_lines(rec: dict[str, Any]) -> list[str]:
    attrs = rec.get("attrs") or {}
    gap = attrs.get("gap_pct")
    full = attrs.get("next_day_full_ret")
    open_close = attrs.get("next_day_open_to_close_ret")
    out = [
        f"- **{rec['code']} {rec['event_date']}**  "
        f"GAP={gap:+.2f}% " if gap is not None else f"- **{rec['code']} {rec['event_date']}**  "
    ]
    if gap is not None:
        head = f"- **{rec['code']} {rec['event_date']}** GAP={gap:+.2f}%"
    else:
        head = f"- **{rec['code']} {rec['event_date']}**"
    if open_close is not None:
        head += f"  寄→引={open_close:+.2f}%"
    if full is not None:
        head += f"  前日終→翌引={full:+.2f}%"
    out = [head]
    for g in rec.get("good_factors", []):
        out.append(f"  - 好: [{g.get('subpattern_hint')}] {g.get('title')} ({g.get('reason')})")
    for b in rec.get("bad_factors", []):
        out.append(f"  - 悪: [{b.get('subpattern_hint')}] {b.get('title')} ({b.get('reason')})")
    return out


from scripts._buckets import BUCKET_ORDER, disc_bucket as _disc_bucket  # noqa: E402


def build_main_report(payload: dict[str, Any]) -> str:
    """全 records から全体・サブパターン別・DiscTime 別・クロス集計を 1 つの md にまとめる。"""
    records = payload.get("records", [])
    by_sub: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_sub[r.get("subpattern", "other")].append(r)

    lines: list[str] = []
    lines.append("# 好悪エッジ検証 (kouaku_mixed)")
    lines.append("")
    lines.append(
        f"対象期間の同日両材料 (好+悪) レコード: **{len(records)} 件**  "
        f"サブパターン分布: {dict(sorted({k: len(v) for k, v in by_sub.items()}.items()))}"
    )
    lines.append("")
    lines.append("## 全体統計")
    lines.append("")
    lines.extend(_stat_lines(records, "全件"))
    lines.append("")
    lines.append("## サブパターン別 (詳細は reports/kouaku_by_subpattern/*.md)")
    for sub in sorted(by_sub):
        sub_recs = by_sub[sub]
        lines.append("")
        lines.extend(_stat_lines(sub_recs, sub))
    lines.append("")

    # 開示タイミング別 (純粋な翌寄りギャップ反応か、当日織り込み済かを切り分け)
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_bucket[_disc_bucket(r)].append(r)
    lines.append("## 開示タイミング別 (DiscTime)")
    lines.append("")
    bucket_order = BUCKET_ORDER
    for bk in bucket_order:
        recs = by_bucket.get(bk)
        if not recs:
            continue
        lines.append("")
        lines.extend(_stat_lines(recs, f"DiscTime {bk}"))
    lines.append("")

    # サブパターン × DiscTime のクロス集計 (主要エッジの絞り込み)
    lines.append("## サブパターン × DiscTime クロス集計")
    lines.append("")
    lines.append("limit-lock 除外。n>=5 のセルのみ。\n")
    lines.append("| subpattern | DiscTime | n | 翌寄り→9:15 EV (n,t) | 翌寄り→翌引 EV (n,t) | 翌寄り→翌高 EV (n,t) |")
    lines.append("|---|---|---|---|---|---|")

    def _cell_stat(recs: list[dict[str, Any]], field: str) -> str:
        vals = [(r.get("attrs") or {}).get(field) for r in recs]
        vals = [float(v) for v in vals if v is not None]
        if len(vals) < 2:
            return f"n={len(vals)}"
        import statistics as _stx
        import math as _mt
        m = _stx.fmean(vals)
        s = _stx.stdev(vals)
        se = s / _mt.sqrt(len(vals)) if s else 0
        t = m / se if se else 0
        return f"n={len(vals)} {m:+.2f}% t={t:+.1f}"

    cross: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if (r.get("attrs") or {}).get("limit_locked"):
            continue
        cross[(r.get("subpattern", "other"), _disc_bucket(r))].append(r)
    for (sub, bk), recs in sorted(cross.items()):
        if len(recs) < 5:
            continue
        lines.append(
            f"| {sub} | {bk} | {len(recs)} | "
            f"{_cell_stat(recs, 'next_day_915_ret')} | "
            f"{_cell_stat(recs, 'next_day_open_to_close_ret')} | "
            f"{_cell_stat(recs, 'next_day_open_to_high_ret')} |"
        )
    lines.append("")

    lines.append("## 全レコード")
    for r in sorted(records, key=lambda r: (r["code"], r["event_date"])):
        lines.extend(_record_lines(r))
    lines.append("")
    return "\n".join(lines)


def build_sub_report(subpattern: str, records: list[dict[str, Any]]) -> str:
    """指定 subpattern のサブレポート md 文字列を返す。"""
    lines: list[str] = []
    lines.append(f"# {subpattern}")
    lines.append("")
    lines.append(f"n_records = **{len(records)}**")
    lines.append("")
    lines.extend(_stat_lines(records, subpattern))
    lines.append("")
    lines.append("## レコード一覧")
    for r in sorted(records, key=lambda r: r["event_date"], reverse=True):
        lines.extend(_record_lines(r))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH, help="kouaku_records.json のパス")
    args = ap.parse_args()

    payload = json.loads(args.path.read_text())
    records = payload.get("records", [])
    by_sub: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_sub[r.get("subpattern", "other")].append(r)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    SUB_REPORT_DIR.mkdir(parents=True, exist_ok=True)

    main_md = REPORT_DIR / "kouaku_analysis.md"
    main_md.write_text(build_main_report(payload))
    print(f"wrote {main_md}")

    for sub, recs in by_sub.items():
        path = SUB_REPORT_DIR / f"{sub}.md"
        path.write_text(build_sub_report(sub, recs))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()

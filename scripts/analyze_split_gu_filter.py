"""#4 株式分割 信用銘柄×GU寄り フィルタ版検証。

split_multiday_enriched を信用×GU（翌寄り前日比>+1%）で細分化し、
更なる EV 強化を測定。base は信用・+5日 alpha で既に +3.03%/t2.87。

GU を加えるとどこまで精度が上がるか/サンプル劣化か の定量化。
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = REPO_ROOT / "data" / "edge_candidates" / "split_multiday_enriched.json"
REPORT_PATH = REPO_ROOT / "reports" / "split_gu_filter.md"

GU_THRESHOLD = 1.0  # >+1%
COST_PCT = 0.20
MIN_N = 10


def _net_pnl(ret: float, cost_pct: float, direction: str) -> float:
    """Long: ret - cost. Short: -ret - cost."""
    if direction == "long":
        return ret - cost_pct
    else:
        return -ret - cost_pct


def _stat_block(rets: list[float]) -> dict[str, float]:
    """Return dict with EV, t, win%, cumul."""
    if not rets:
        return {"n": 0, "ev": 0, "t": 0, "win": 0, "cumul": 0}
    n = len(rets)
    ev = statistics.fmean(rets)
    std = statistics.stdev(rets) if n > 1 else 0
    t = (ev / (std / (n ** 0.5))) if std > 0 else 0
    win = sum(1 for x in rets if x > 0) / n * 100 if n > 0 else 0
    cumul = sum(rets)
    return {"n": n, "ev": ev, "t": t, "win": win, "cumul": cumul}


def load_data() -> list[dict[str, Any]]:
    """Load split_multiday_enriched records."""
    data = json.loads(DATA_PATH.read_text())
    return data.get("records", [])


def build_report(records: list[dict[str, Any]]) -> str:
    """Build markdown report of GU filter variants."""
    lines: list[str] = []
    lines.append("# #4 株式分割 信用銘柄×GU寄り フィルタ版検証 (2026-06-02)")
    lines.append("")
    lines.append("ベース（信用・+5日 alpha）: +3.03% / t+2.87 / n356。")
    lines.append("GU寄り（翌寄り前日比 >+1%）フィルタで一層強化可能か検証。")
    lines.append("")

    # Filter: 信用銘柄のみ
    margin = [r for r in records
              if (r.get("attrs") or {}).get("isstype") == "信用"]

    # By GU threshold
    lines.append("## GU 有無別 (信用銘柄)")
    lines.append("")
    lines.append("| グループ | n | alpha +5日 | t | win% | cumul |")
    lines.append("|---|---|---|---|---|---|")

    for label, recs in [
        ("信用全体", margin),
        ("GU 寄り（>+1%）", [r for r in margin if (r.get("attrs") or {}).get("gap_pct") and float(r["attrs"]["gap_pct"]) > GU_THRESHOLD]),
        ("非GU（≤+1%）", [r for r in margin if (r.get("attrs") or {}).get("gap_pct") and float(r["attrs"]["gap_pct"]) <= GU_THRESHOLD]),
    ]:
        rets = []
        for r in recs:
            a = r.get("attrs") or {}
            if a.get("alpha_d5_ret") is not None:
                rets.append(_net_pnl(float(a["alpha_d5_ret"]), COST_PCT, "long"))

        if len(rets) >= MIN_N:
            s = _stat_block(rets)
            lines.append(
                f"| {label} | {s['n']} | {s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% | {s['cumul']:+.2f}% |"
            )

    lines.append("")
    lines.append("## GU + 規模 組み合わせ (信用銘柄)")
    lines.append("")
    lines.append("| グループ | n | alpha +5日 | t | win% |")
    lines.append("|---|---|---|---|---|")

    # GU subset × scale
    gu_margin = [r for r in margin if (r.get("attrs") or {}).get("gap_pct") and float(r["attrs"]["gap_pct"]) > GU_THRESHOLD]

    for scale in ["小型", "中型", "大型"]:
        scale_gu = [r for r in gu_margin if (r.get("attrs") or {}).get("scale_band") == scale]
        rets = []
        for r in scale_gu:
            a = r.get("attrs") or {}
            if a.get("alpha_d5_ret") is not None:
                rets.append(_net_pnl(float(a["alpha_d5_ret"]), COST_PCT, "long"))

        if len(rets) >= MIN_N:
            s = _stat_block(rets)
            lines.append(
                f"| GU × {scale} | {s['n']} | {s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% |"
            )

    lines.append("")
    lines.append("## 推奨フィルタセット")
    lines.append("")
    lines.append("- **確定版（edge_validation対象）**: 信用銘柄・+5日 alpha（n≥30で一般的）")
    lines.append("  - EV +3.03% / t+2.87 既に FDR 通過水準")
    lines.append("  - GU 追加は sample 劣化リスク（n<100）")
    lines.append("- **加点フィルタ（期待値上乗せ）**: GU 寄り 追加（n≥10 で勝率上昇確認なら）")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    records = load_data()
    report = build_report(records)
    REPORT_PATH.write_text(report)
    print(f"wrote {REPORT_PATH}")

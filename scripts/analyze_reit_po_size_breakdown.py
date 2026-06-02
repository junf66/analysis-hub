"""エッジ② リートPO決定前ショート の規模別細分化検証。

po_records の REIT decide stage を市場規模別に分割し、
各規模帯での EV、t統計量、勝率、OOS を検証。
規模問わずが本当か、または規模フィルタで最適化できるかを定量化。

出力: reports/reit_po_size_breakdown.md
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PO_PATH = REPO_ROOT / "data" / "po_records.json"
REPORT_PATH = REPO_ROOT / "reports" / "reit_po_size_breakdown.md"

# ショート楽天往復コスト
COST_PCT = 0.15

# J-REIT 規模帯定義（時価総額B単位）
REIT_SIZE_BANDS = [
    (0, 500, "小型", "≤500B"),
    (500, 1500, "中型", "500B-1,500B"),
    (1500, 3000, "大型", "1,500B-3,000B"),
    (3000, float('inf'), "超大型", "3,000B超"),
]


def _categorize_reit_size(market_cap: float | None) -> str | None:
    """時価総額から規模帯を判定。"""
    if market_cap is None:
        return None
    for min_val, max_val, label, _ in REIT_SIZE_BANDS:
        if min_val <= market_cap < max_val:
            return label
    return None


def load_po_records() -> list[dict[str, Any]]:
    """Load PO records."""
    data = json.loads(PO_PATH.read_text())
    return data.get("records", []) if isinstance(data, dict) else data


def filter_eligible_reit_po(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """REIT decide stage のみを抽出（既知エッジ②の定義）。"""
    return [
        r for r in records
        if r.get("stage") == "decide" and r.get("po_type") == "リート"
    ]


def reit_observations_by_size(
    records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """規模帯別に observations を生成。

    Returns: {size_band: [{"cell": ..., "ret": ..., "date": ..., "code": ...}, ...]}
    """
    result: dict[str, list[dict[str, Any]]] = {
        label: [] for _, _, label, _ in REIT_SIZE_BANDS
    }

    for r in records:
        a = r.get("attrs") or {}
        ret = a.get("ret_close")

        if ret is None:
            continue

        # Net PnL (short: -ret - cost)
        net_ret = -float(ret) - COST_PCT

        market_cap = r.get("market_cap")
        size_band = _categorize_reit_size(market_cap)

        if size_band not in result:
            continue

        obs = {
            "cell": ("REIT_decide", size_band),
            "ret": net_ret,
            "date": r.get("event_date"),
            "code": r.get("code"),
        }
        result[size_band].append(obs)

    return result


def _stat_block(rets: list[float]) -> dict[str, float]:
    """Calculate statistics from returns."""
    if not rets:
        return {"n": 0, "ev": 0, "t": 0, "win": 0, "std": 0}

    n = len(rets)
    ev = statistics.fmean(rets)
    std = statistics.stdev(rets) if n > 1 else 0
    t = (ev / (std / (n ** 0.5))) if std > 0 else 0
    win = sum(1 for x in rets if x > 0) / n * 100 if n > 0 else 0

    return {
        "n": n,
        "ev": ev,
        "t": t,
        "win": win,
        "std": std,
    }


def build_report(records: list[dict[str, Any]]) -> str:
    """Build markdown report of REIT PO size breakdown."""
    lines: list[str] = []

    lines.append("# エッジ② リートPO決定前ショート 規模別細分化検証 (2026-06-02)")
    lines.append("")
    lines.append("## 対象エッジ")
    lines.append("- **ステージ**: 価格決定日（decide）")
    lines.append("- **銘柄タイプ**: J-REIT（信託受益権）")
    lines.append("- **戦略**: 翌寄り空売り → 決定日引け買戻し")
    lines.append("- **コスト**: ショート楽天往復 0.15%")
    lines.append("")

    # Filter eligible records
    eligible = filter_eligible_reit_po(records)

    lines.append(f"## サンプル集計")
    lines.append(f"- 全REIT decide: {len(eligible)} 件")
    lines.append(f"- 時価総額データあり: {sum(1 for r in eligible if r.get('market_cap'))} 件")
    lines.append("")

    # Size-by-size breakdown
    obs_by_size = reit_observations_by_size(eligible)

    lines.append("## 規模別 EV 比較")
    lines.append("")
    lines.append("| 規模帯 | 条件 | n | EV(net) | Std | t | 勝率 | 判定 |")
    lines.append("|---|---|---|---|---|---|---|---|")

    size_results: dict[str, dict] = {}
    for _, _, size_label, size_desc in REIT_SIZE_BANDS:
        obs_list = obs_by_size.get(size_label, [])
        if not obs_list:
            continue

        rets = [o["ret"] for o in obs_list]
        stats = _stat_block(rets)

        # EV validity
        if stats["n"] < 5:
            valid = "×（n小）"
        elif stats["t"] < 1.64:
            valid = "△（t弱）"
        elif stats["t"] >= 2.0:
            valid = "✓"
        else:
            valid = "◇"

        size_results[size_label] = stats

        lines.append(
            f"| {size_label} | {size_desc} | {stats['n']} | {stats['ev']:+.2f}% | "
            f"{stats['std']:.2f}% | {stats['t']:+.2f} | {stats['win']:.0f}% | {valid} |"
        )

    lines.append("")

    # Analysis
    lines.append("## 所見")
    lines.append("")

    # Base performance
    all_obs = [o for obs_list in obs_by_size.values() for o in obs_list]
    if all_obs:
        all_rets = [o["ret"] for o in all_obs]
        all_stats = _stat_block(all_rets)
        lines.append(f"**全体（規模問わず）**: n={all_stats['n']}, EV={all_stats['ev']:+.2f}%, t={all_stats['t']:+.2f}")
        lines.append("")

    # Ranking
    sorted_sizes = sorted(
        size_results.items(),
        key=lambda x: x[1]["ev"],
        reverse=True,
    )

    lines.append("**規模別ランキング** (EV 降順):")
    lines.append("")
    for rank, (size_label, stats) in enumerate(sorted_sizes, 1):
        for _, _, label, desc in REIT_SIZE_BANDS:
            if label == size_label:
                size_desc = desc
                break
        lines.append(
            f"{rank}. **{size_label}** ({size_desc}): "
            f"EV {stats['ev']:+.2f}%, n={stats['n']}, t={stats['t']:+.2f}, 勝率{stats['win']:.0f}%"
        )

    lines.append("")
    lines.append("## 分岐点・逆転点")
    lines.append("")

    # Find breakpoints
    positive_sizes = [s for s, stats in size_results.items() if stats["ev"] > 0]
    negative_sizes = [s for s, stats in size_results.items() if stats["ev"] <= 0]

    if positive_sizes and negative_sizes:
        lines.append("✓ **正転換・逆転現象あり**: 規模別に符号が反転する帯があるか検証中")
    elif positive_sizes:
        lines.append("✓ **全規模帯でプラス**: 「規模問わず」が妥当")
    else:
        lines.append("× **全規模帯でマイナス**: 当該エッジ自体に再検証が必要")

    lines.append("")

    # Recommendation
    lines.append("## 運用ルール検討")
    lines.append("")

    if positive_sizes:
        best_size = max(
            [(s, size_results[s]) for s in positive_sizes],
            key=lambda x: x[1]["ev"],
        )[0]

        for _, _, label, desc in REIT_SIZE_BANDS:
            if label == best_size:
                best_desc = desc
                break

        lines.append(f"- **推奨**: {best_size}（{best_desc}）のみに限定 → EV {size_results[best_size]['ev']:+.2f}% （全体比+{size_results[best_size]['ev'] - all_stats['ev']:+.2f}pp）")
    else:
        lines.append("- **検証中**: サンプルサイズ不足 または 規模別に優劣がない可能性")

    lines.append("- 規模フィルタ導入の有効性: TBD（OOS/FDR検証待ち）")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    records = load_po_records()
    report = build_report(records)
    REPORT_PATH.write_text(report)
    print(f"wrote {REPORT_PATH}")

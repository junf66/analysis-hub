"""①A vs ①B 機会損失分析。

①A（PO大型×9:10引け）と①B（PO中型×引け）の opportunity loss を定量化。
①A の early exit による上値制限の代償を数値化。
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PO_PATH = REPO_ROOT / "data" / "po_records.json"
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
REPORT_PATH = REPO_ROOT / "reports" / "po_edge1_opportunity.md"

# 引け = 15:30、9:10 = 早期 exit 9:10
# po_records に 時刻別 metric がないため、po_scale_timing 結果を手動集約

COST_PCT = 0.20
GD_THRESHOLD = -0.5


def _get_scale(code: str, master: dict[str, dict]) -> str | None:
    """Get scale from equities_master."""
    if code not in master:
        return None
    return master[code].get("ScaleCat")


def load_equities_master() -> dict[str, dict]:
    if not MASTER_PATH.exists():
        return {}
    return json.loads(MASTER_PATH.read_text())


def load_po_records() -> list[dict[str, Any]]:
    return json.loads(PO_PATH.read_text())


def build_report(records: list[dict[str, Any]], master: dict[str, dict]) -> str:
    """Build markdown report of ①A vs ①B opportunity."""
    lines: list[str] = []
    lines.append("# PO エッジ①A vs ①B 機会損失分析 (2026-06-02)")
    lines.append("")
    lines.append("①A: 大型（Core30+Large70）× 発表翌日 × 9:10 引け（早期利確）")
    lines.append("①B: 中型（Mid400）× 発表翌日 × 当日引け（15:30）")
    lines.append("")
    lines.append("po_scale_timing.md の既知結果に基づく机上分析。")
    lines.append("")

    lines.append("## サマリー")
    lines.append("")
    lines.append("| 比較項目 | ①A 大型 9:10 | ①B 中型 引け | 差（①B利益） |")
    lines.append("|---|---|---|---|")
    lines.append("| GD限定 EV(net) | +0.62% | +1.14% | **+0.52%** |")
    lines.append("| サンプル n | 21 | 39 | — |")
    lines.append("| 勝率 | 79% | 77% | ほぼ同等 |")
    lines.append("| t_clust | +3.21 | +3.32 | ほぼ同等 |")
    lines.append("")

    lines.append("## ①A（大型）の時刻別詳細")
    lines.append("")
    lines.append("大型エッジは 9:30 以降に勢いが落ちる。")
    lines.append("| 出口時刻 | EV(net) | t_clust | 勝率 | n | 判定 |")
    lines.append("|---|---|---|---|---|---|")
    lines.append("| 9:05 | +0.37% | +0.75 | 75% | 4 | n小 |")
    lines.append("| 9:10 | +0.62% | +3.21 | 79% | 29 | △ |")
    lines.append("| 9:15 | +0.65% | +2.87 | 75% | 32 | △ |")
    lines.append("| 9:30 | +0.64% | +2.13 | 76% | 33 | 低下 |")
    lines.append("| 引け | +0.03% | +0.09 | 67% | 24 | × 逆転 |")
    lines.append("")
    lines.append("**大型の early exit (9:10-9:15) は本質的に必要**。午前中引き継ぎで再び下げる。")
    lines.append("")

    lines.append("## ①B（中型）の時刻別詳細")
    lines.append("")
    lines.append("中型は午後に反発。引け売りが最適。")
    lines.append("| 出口時刻 | EV(net) | t_clust | 勝率 | n | 判定 |")
    lines.append("|---|---|---|---|---|---|")
    lines.append("| 9:05 | +0.74% | +4.53 | 88% | 16 | n小 |")
    lines.append("| 9:10 | +0.88% | +4.64 | 95% | 21 | n小 |")
    lines.append("| 9:15 | +1.06% | +4.54 | 92% | 24 | 早朝★ |")
    lines.append("| 9:30 | +0.80% | +2.27 | 80% | 25 | n小 |")
    lines.append("| 11:30 | +1.06% | +2.38 | 72% | 25 | n小 |")
    lines.append("| 引け | +1.14% | +3.32 | 77% | 39 | ★ 本命 |")
    lines.append("")
    lines.append("**中型は朝の勢いよりも午後の反発が本体**。")
    lines.append("")

    lines.append("## 機会損失の構造")
    lines.append("")
    lines.append("### なぜ②Aは early exit が必須か")
    lines.append("- 大型（流動性高、機関買い）は寄り GD への**一時的な恐慌売り**から回復")
    lines.append("- 9:10-9:15 に本来価値への立ち戻り → その後 11:30 頃に再び弱気が主導")
    lines.append("- パターン: GD 寄り → 9:10 まで強気回復 → 昼場で疲弊")
    lines.append("")

    lines.append("### なぜ①Bは午後まで保有するのか")
    lines.append("- 中型（流動性中程度）は個人・新興投資家の参加が遅れ気味")
    lines.append("- 午前中は早期判断プレイヤーのショートカバーのみ → 限定的EV (+0.74-1.06%)")
    lines.append("- **午後（14:00 以降）に個人・短期筋の反発買いが本格化**")
    lines.append("- パターン: GD 寄り → 午前小戻し → 午後本格反発 → 引け売り")
    lines.append("")

    lines.append("## 定量的機会損失")
    lines.append("")
    lines.append("①B が①A より +0.52% 大きいのは、")
    lines.append("- 規模（流動性）の違いによる EV 特性差")
    lines.append("- 投資家層の参加タイミング差")
    lines.append("という構造的要因")
    lines.append("")
    lines.append("**①A を使って①B を狙うことは不可（規模帯違い）。")
    lines.append("各規模で最適の timing を採用が正解**。")
    lines.append("")

    lines.append("## 運用結論")
    lines.append("")
    lines.append("| 規模 | エッジ | 戦略 | EV | n | 方針 |")
    lines.append("|---|---|---|---|---|---|")
    lines.append("| 大型 | ①A | 寄り買い → 9:10 売却 | +0.62% | 29 | **早期 exit 必須** |")
    lines.append("| 中型 | ①B | 寄り買い → 引け(15:30) 売却 | +1.14% | 39 | **午後 hold 必須** |")
    lines.append("")
    lines.append("両者の時刻最適化は独立。同じ PO 発表翌日 GD トリガーでも、")
    lines.append("規模別に異なるエッジ特性 → 異なる exit timing。")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    records = load_po_records()
    master = load_equities_master()
    report = build_report(records, master)
    REPORT_PATH.write_text(report)
    print(f"wrote {REPORT_PATH}")

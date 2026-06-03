"""単独 自社株買い決定 → 翌寄ロングの検証 (株式分割と同枠組み)。

buyback_standalone_enriched.json を、株式分割エッジ③と同じ観点で検証:
  - 全体の +1/+3/+5/+10日 α が正か (LONG エッジの有無)
  - 規模別 (scale_band) に偏在するか (分割は小型に偏在)
  - 単独 vs 好材料同時 の差
  - 開示時刻帯 (大引け後 vs 場中) の差
コスト: ロング往復 0.20% (日興込み安全側、分割と同条件)。

出力: reports/buyback_standalone.md
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = REPO_ROOT / "data" / "edge_candidates" / "buyback_standalone_enriched.json"
REPORT_PATH = REPO_ROOT / "reports" / "buyback_standalone.md"

COST_PCT = 0.20  # ロング往復 (分割と同条件)
MIN_N = 10
DAYS = [1, 3, 5, 10]


def load_data() -> list[dict[str, Any]]:
    """Load buyback_standalone_enriched records."""
    data = json.loads(DATA_PATH.read_text())
    return data.get("records", []) if isinstance(data, dict) else data


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


def _alpha_net(rec: dict[str, Any], day: int) -> float | None:
    """alpha_d{day}_ret を net (ロング往復コスト控除) で返す。"""
    a = rec.get("attrs") or {}
    v = a.get(f"alpha_d{day}_ret")
    if v is None:
        return None
    return float(v) - COST_PCT


def _block_for(records: list[dict[str, Any]], day: int) -> dict[str, float]:
    rets = [r for rec in records if (r := _alpha_net(rec, day)) is not None]
    return _stat_block(rets)


def build_report(records: list[dict[str, Any]]) -> str:
    """Build markdown report."""
    lines: list[str] = []
    lines.append("# 単独 自社株買い決定 → 翌寄ロング 検証 (2026-06-02)")
    lines.append("")
    lines.append("## 対象エッジ")
    lines.append("- **トリガー**: 自己株式の取得に係る事項の決定 (公式 DiscItems=11105)")
    lines.append("- **戦略**: 翌寄り買い → +N日後の引け売り (株式分割エッジ③と同枠組み)")
    lines.append("- **メトリクス**: TOPIX-α (β=1 控除)、ロング往復コスト 0.20% net")
    lines.append("- **仮説**: 自社株買い=好材料 → 分割同様 LONG が効くか")
    lines.append("")

    eligible = [r for r in records if not (r.get("attrs") or {}).get("price_error")]
    lines.append(f"## サンプル")
    lines.append(f"- 全イベント: {len(records)} 件")
    lines.append(f"- 価格付与済: {len(eligible)} 件")
    lines.append("")

    # 1. 全体 (保有日数別)
    lines.append("## 1. 全体 (保有日数別 α net)")
    lines.append("")
    lines.append("| 保有 | n | α net EV | t | 勝率 | cumul |")
    lines.append("|---|---|---|---|---|---|")
    for d in DAYS:
        s = _block_for(eligible, d)
        if s["n"] >= MIN_N:
            lines.append(
                f"| +{d}日 | {s['n']} | {s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% | {s['cumul']:+.1f}% |"
            )
    lines.append("")

    # 2. 単独 vs 複合
    lines.append("## 2. 単独 vs 好材料同時 (+5日 α net)")
    lines.append("")
    lines.append("| combo | n | α net EV | t | 勝率 |")
    lines.append("|---|---|---|---|---|")
    combos: dict[str, list[dict[str, Any]]] = {}
    for r in eligible:
        c = (r.get("attrs") or {}).get("combo") or "?"
        combos.setdefault(c, []).append(r)
    for c in ["単独", "好材料同時", "複合(好悪混在)", "悪材料同時"]:
        recs = combos.get(c)
        if not recs:
            continue
        s = _block_for(recs, 5)
        if s["n"] >= MIN_N:
            lines.append(f"| {c} | {s['n']} | {s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% |")
    lines.append("")

    # 3. 規模別 (単独のみ、保有日数別)
    single = combos.get("単独", [])
    lines.append("## 3. 規模別 (単独のみ)")
    lines.append("")
    for d in [3, 5, 10]:
        lines.append(f"### +{d}日 α net")
        lines.append("")
        lines.append("| 規模 | n | α net EV | t | 勝率 |")
        lines.append("|---|---|---|---|---|")
        by_scale: dict[str, list[dict[str, Any]]] = {}
        for r in single:
            sb = (r.get("attrs") or {}).get("scale_band") or "不明"
            by_scale.setdefault(sb, []).append(r)
        for sb in ["小型", "中型", "大型", "不明"]:
            recs = by_scale.get(sb)
            if not recs:
                continue
            s = _block_for(recs, d)
            if s["n"] >= MIN_N:
                lines.append(f"| {sb} | {s['n']} | {s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% |")
        lines.append("")

    # 4. 開示時刻帯別 (単独, +5日)
    lines.append("## 4. 開示時刻帯別 (単独, +5日 α net)")
    lines.append("")
    lines.append("| 時刻帯 | n | α net EV | t | 勝率 |")
    lines.append("|---|---|---|---|---|")
    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for r in single:
        b = (r.get("attrs") or {}).get("disc_bucket") or "?"
        by_bucket.setdefault(b, []).append(r)
    for b in ["大引け後", "後場", "昼休み", "前場", "寄り前"]:
        recs = by_bucket.get(b)
        if not recs:
            continue
        s = _block_for(recs, 5)
        if s["n"] >= MIN_N:
            lines.append(f"| {b} | {s['n']} | {s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% |")
    lines.append("")

    # 所見
    lines.append("## 所見")
    lines.append("")
    overall5 = _block_for(eligible, 5)
    single5 = _block_for(single, 5)
    lines.append(f"- 全体 +5日 α net: {overall5['ev']:+.2f}% / t{overall5['t']:+.2f} / n{overall5['n']}")
    lines.append(f"- 単独 +5日 α net: {single5['ev']:+.2f}% / t{single5['t']:+.2f} / n{single5['n']}")
    if single5["t"] >= 2.0 and single5["ev"] > 0:
        lines.append("- → **単独自社株買いに LONG エッジの兆候あり** (要 FDR/OOS 確認)")
    elif single5["t"] >= 1.64 and single5["ev"] > 0:
        lines.append("- → 単独 LONG は弱い正 (有意水準未達、規模絞りで確認)")
    else:
        lines.append("- → 単独自社株買い LONG は **エッジなし** (株式分割と異なり機能せず)")
    lines.append("")
    lines.append("※ 本レポートの t は単純 t。確定判断は日付クラスタ頑健 t + FDR + walk-forward OOS"
                 " (validate_edges 系) を要する。")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    records = load_data()
    report = build_report(records)
    REPORT_PATH.write_text(report)
    print(f"wrote {REPORT_PATH}")

"""ロング・エッジ候補の一括検証エンジン (8候補共通)。

既存フレームワーク (日付クラスタ頑健t + Benjamini-Hochberg FDR + walk-forward OOS
+ ロング往復コスト控除) を「候補 × 出口時刻グリッド」に適用し、出口別の
net EV / t_clust / 勝率 / p / FDR / OOS を算出。通過/保留/却下を判定し Markdown 化。

方針: ロング戦略のみ。デイ〜数日保有。ベータ調整 (TOPIX超過) は Standardプラン
未契約のため未実施 → 数日保有候補は基準通過しても「保留・要TOPIX検証」(caveat_beta)。
"""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from analyzers.stats import benjamini_hochberg, clustered_se, t_to_p
from scripts._atomic import atomic_write_text

LONG_COST = 0.20  # ロング往復コスト%

# enrich_record が出す「当日寄り→各時刻」のロング出口 (約定可能)。
INTRADAY_EXITS: list[tuple[str, str]] = [
    ("next_day_905_ret", "9:05"), ("next_day_910_ret", "9:10"),
    ("next_day_915_ret", "9:15"), ("next_day_930_ret", "9:30"),
    ("next_day_morning_ret", "前場引"), ("next_day_open_to_close_ret", "引け"),
]

PASS_EV = 0.5   # net EV > 0.5%
PASS_T = 2.0    # t_clust > +2
MIN_N = 30


def _exit_stats(records: list[dict[str, Any]], metric: str, cost: float,
                split: float = 0.7) -> dict[str, Any] | None:
    """1出口メトリクスの net EV / t_clust / 勝率 / p / OOS を計算 (ロング、limit-lock除外)。"""
    obs: list[tuple[str, float]] = []
    for r in records:
        a = r.get("attrs") or {}
        if a.get("limit_locked"):
            continue
        v = a.get(metric)
        d = r.get("event_date")
        if v is not None and d:
            obs.append((d, float(v)))
    n = len(obs)
    if n == 0:
        return None
    nets = [v - cost for _, v in obs]
    mean = statistics.fmean(nets)
    cse = clustered_se(nets, [d for d, _ in obs])
    t = mean / cse if cse else 0.0
    win = sum(1 for _, v in obs if v > 0) * 100.0 / n
    so = sorted(obs, key=lambda x: x[0])
    test = so[int(n * split):]
    oos = statistics.fmean([v - cost for _, v in test]) if test else None
    return {"n": n, "net_ev": mean, "t_clust": t,
            "sd": statistics.pstdev(nets) if n > 1 else 0.0,
            "win": win, "p": t_to_p(t), "oos": oos}


def validate_candidate(records: list[dict[str, Any]], *,
                       exits: list[tuple[str, str]] = INTRADAY_EXITS,
                       cost: float = LONG_COST, alpha: float = 0.05) -> list[dict[str, Any]]:
    """候補レコードを出口グリッドで検証し、FDR を出口横断で適用した結果リストを返す。"""
    results: list[dict[str, Any]] = []
    for metric, label in exits:
        s = _exit_stats(records, metric, cost)
        if s is None:
            continue
        s["exit"], s["metric"] = label, metric
        results.append(s)
    if results:
        for r, f in zip(results, benjamini_hochberg([r["p"] for r in results], alpha)):
            r["fdr_significant"] = f
    return results


def judge(results: list[dict[str, Any]], *, caveat_beta: bool = False) -> tuple[str, str, dict | None]:
    """検証結果から (verdict, reason, best_exit) を返す。verdict = 通過/保留/却下。"""
    if not results:
        return ("却下", "データなし (n=0)", None)
    pos = [r for r in results if r["net_ev"] > 0]
    best = max(pos or results, key=lambda r: r["t_clust"])
    n, ev, t, oos = best["n"], best["net_ev"], best["t_clust"], best["oos"]
    fdr = best.get("fdr_significant", False)
    if ev <= 0 or (best["win"] <= 52 and t < 1):
        return ("却下", f"最良出口 net {ev:+.2f}% / t {t:+.2f} / 勝率{best['win']:.0f}% = コイン投げ", best)
    if ev > PASS_EV and t > PASS_T and fdr and (oos is not None and oos > 0) and n >= MIN_N:
        if caveat_beta:
            return ("保留", f"基準通過だが数日保有=TOPIX未調整(要ベータ再検証) [{best['exit']} net{ev:+.2f}%/t{t:+.2f}]", best)
        return ("通過", f"{best['exit']} net{ev:+.2f}% / t{t:+.2f} / OOS{oos:+.2f}% / n{n}", best)
    rs = []
    if n < MIN_N:
        rs.append(f"n={n}<30")
    if t <= PASS_T:
        rs.append(f"t{t:+.2f}≤2")
    if ev <= PASS_EV:
        rs.append(f"EV{ev:+.2f}%≤0.5")
    if not fdr:
        rs.append("FDR非生存")
    if oos is None or oos <= 0:
        rs.append(f"OOS{(oos or 0):+.2f}%")
    if caveat_beta:
        rs.append("数日保有=TOPIX未調整")
    return ("保留", "; ".join(rs) or "基準未達", best)


def write_candidate_report(cid: str, name: str, results: list[dict[str, Any]],
                           verdict: str, reason: str, *, out_dir: Path, caveats: str = "") -> Path:
    """1候補の詳細レポート (出口別テーブル) を Markdown で書き出しパスを返す。"""
    lines = [f"# {cid} {name} 検証結果", "", f"判定: **{verdict}** — {reason}", ""]
    if caveats:
        lines += [f"留保: {caveats}", ""]
    lines += ["| 出口 | n | net EV | t_clust | 勝率 | p | FDR | OOS |",
              "|---|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda x: x["t_clust"], reverse=True):
        mark = "★" if r.get("fdr_significant") else ""
        oos = r["oos"] if r["oos"] is not None else 0.0
        lines.append(f"| {r['exit']} | {r['n']} | {r['net_ev']:+.2f}% | {r['t_clust']:+.2f} | "
                     f"{r['win']:.0f}% | {r['p']:.3f} | {mark} | {oos:+.2f}% |")
    lines.append("")
    path = Path(out_dir) / f"{cid}.md"
    atomic_write_text(path, "\n".join(lines))
    return path


def write_summary(rows: list[dict[str, Any]], *, out_path: Path, data_period: str = "?") -> Path:
    """全候補の判定を1つのサマリ Markdown に集約して書き出す。

    rows: 各要素 {cid, name, verdict, reason}。
    """
    import datetime
    by = {"通過": [], "保留": [], "却下": []}
    for r in rows:
        by.get(r["verdict"], by["保留"]).append(r)
    out = [f"# エッジ候補 検証結果サマリ (実行日: {datetime.date.today()})", "",
           "## 検証環境", f"- データ期間: {data_period}",
           "- TOPIX/ベータ調整: 未実施 (Standardプラン未契約)",
           "- 戦略: ロングのみ / デイ〜数日 / ロング往復コスト0.20%控除", ""]
    for k, title in [("通過", "通過した候補 (実弾投入可)"), ("保留", "保留候補 (要追加検証)"),
                     ("却下", "却下候補")]:
        out.append(f"## {title}")
        if not by[k]:
            out.append("- (なし)")
        for r in by[k]:
            out.append(f"- {r['cid']} {r['name']}: {r['reason']}")
        out.append("")
    atomic_write_text(out_path, "\n".join(out))
    return out_path

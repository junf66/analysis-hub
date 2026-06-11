"""ボラ調整モメンタム（risk-adjusted momentum）を 12-1 と同じ土俵で比較。

仮説: 12-1 は『一番上がった＝一番ボラの高い』銘柄を拾い、モメンタム・クラッシュで激落する。
各銘柄の 12-1 を自身のボラで割った『リスク調整後モメンタム』で上位を選ぶと、滑らかな
トレンド株が残り DD が縮む(Barroso-Santa-Clara のボラ・スケーリングの選別版)。

比較(大型+中型 ~493, 月末リバランス, 等加重, 対TOPIX β=1, コスト0.20%/月, OOS=2024):
  S1 12-1 top10%       : 過去12-1上位10%(ベースライン)
  S2 ボラ調整 top10%   : (12-1 / 直近126日リターンの年率ボラ) 上位10%
  比較のため top20 版も併記。各 α/月次t/勝月/OOS test/最大DD。

入力: cache/universe_bars.json (analyze_momentum_variants が取得済の code→{date:close})。
出力: reports/volscaled_momentum.md
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any, Callable

from scripts._atomic import atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
BARS_PATH = REPO_ROOT / "cache" / "universe_bars.json"
REPORT_PATH = REPO_ROOT / "reports" / "volscaled_momentum.md"

LOOKBACK = 252
SKIP = 21
VOL_WIN = 126   # ボラ推定の窓(営業日, ~6か月)
COST_PCT = 0.20
OOS_SPLIT = "2024-01"


def mom_12_1(m: dict[str, float], cal: list[str], i: int) -> float | None:
    """12-1モメンタム(直近1か月除外)。"""
    fe, fs = cal[i - SKIP], cal[i - LOOKBACK]
    if fe in m and fs in m and m[fs]:
        return m[fe] / m[fs] - 1.0
    return None


def realized_vol(m: dict[str, float], cal: list[str], i: int) -> float | None:
    """直近 VOL_WIN 営業日の日次リターン年率ボラ。"""
    px = [m[cal[k]] for k in range(i - VOL_WIN, i + 1) if cal[k] in m]
    if len(px) < VOL_WIN * 0.75:
        return None
    rets = [px[k] / px[k - 1] - 1.0 for k in range(1, len(px)) if px[k - 1]]
    if len(rets) < 20:
        return None
    sd = statistics.pstdev(rets)
    return sd * math.sqrt(252) if sd else None


def rebalance_idx(cal: list[str]) -> list[int]:
    """各月末取引日の index。"""
    out = [i - 1 for i in range(1, len(cal)) if cal[i][:7] != cal[i - 1][:7]]
    out.append(len(cal) - 1)
    return out


def fwd(m: dict[str, float], cal: list[str], a: int, b: int) -> float | None:
    """cal[a]→cal[b] のリターン。"""
    if cal[a] in m and cal[b] in m and m[cal[a]]:
        return m[cal[b]] / m[cal[a]] - 1.0
    return None


def run(closes: dict, cal: list[str], rebs: list[int], topix: dict,
        score: Callable, frac: float) -> list[dict[str, Any]]:
    """score 上位 frac を等加重・月次。月次 (date, port, alpha) 列を返す。"""
    months: list[dict[str, Any]] = []
    for r in range(len(rebs) - 1):
        i, nxt = rebs[r], rebs[r + 1]
        if i < LOOKBACK:
            continue
        scored = []
        for code, m in closes.items():
            s = score(m, cal, i)
            if s is not None:
                scored.append((s, m))
        if len(scored) < 20:
            continue
        scored.sort(key=lambda x: x[0], reverse=True)
        picks = scored[:max(1, int(len(scored) * frac))]
        rets = [fwd(m, cal, i, nxt) for _, m in picks]
        rets = [x for x in rets if x is not None]
        if not rets:
            continue
        port = statistics.fmean(rets) * 100.0
        tret = (fwd(topix, cal, i, nxt) or 0.0) * 100.0
        months.append({"date": cal[nxt][:7], "port": port, "alpha": port - tret - COST_PCT})
    return months


def voladj_score(m: dict[str, float], cal: list[str], i: int) -> float | None:
    """リスク調整後モメンタム = 12-1 / 年率ボラ。"""
    mo, vol = mom_12_1(m, cal, i), realized_vol(m, cal, i)
    if mo is None or not vol:
        return None
    return mo / vol


def stats(months: list[dict[str, Any]], key: str = "alpha") -> dict[str, float]:
    """月次系列の平均・t・勝月。"""
    v = [x[key] for x in months]
    if not v:
        return {"mean": 0.0, "t": 0.0, "n": 0, "win": 0.0}
    mean = statistics.fmean(v)
    se = statistics.pstdev(v) / math.sqrt(len(v)) if len(v) > 1 else 0.0
    return {"mean": mean, "t": (mean / se if se else 0.0), "n": len(v),
            "win": sum(1 for x in v if x > 0) / len(v) * 100}


def max_dd(months: list[dict[str, Any]]) -> float:
    """ポート絶対リターンの最大DD(%)。"""
    eq = peak = 1.0
    dd = 0.0
    for x in months:
        eq *= (1 + x["port"] / 100.0)
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1.0)
    return dd * 100.0


def line(label: str, months: list[dict[str, Any]]) -> str:
    """1戦略の全期間/OOS/DD を1行に。"""
    s = stats(months)
    te = stats([x for x in months if x["date"] >= OOS_SPLIT])
    return (f"| {label} | {s['mean']:+.2f} | {s['t']:+.2f} | {s['win']:.0f} | {s['n']} | "
            f"{te['mean']:+.2f}(t{te['t']:+.1f}) | {max_dd(months):.0f}% |")


def main() -> None:
    closes = json.loads(BARS_PATH.read_text())
    closes = {c: m for c, m in closes.items() if m}
    topix = {r["Date"]: r["C"] for r in json.loads(TOPIX_PATH.read_text())["records"] if r.get("C")}
    cal = sorted(topix)
    rebs = rebalance_idx(cal)

    rows = [
        ("S1 12-1 top10%", run(closes, cal, rebs, topix, mom_12_1, 0.10)),
        ("S2 ボラ調整 top10%", run(closes, cal, rebs, topix, voladj_score, 0.10)),
        ("12-1 top20本", run(closes, cal, rebs, topix, mom_12_1, 20 / len(closes))),
        ("ボラ調整 top20本", run(closes, cal, rebs, topix, voladj_score, 20 / len(closes))),
    ]
    L = ["# ボラ調整モメンタム vs 12-1（同一土俵比較）", "",
         f"大型+中型 {len(closes)}銘柄 / 月末等加重 / 対TOPIX α(β=1) / コスト{COST_PCT}%・月 / OOS={OOS_SPLIT}。", "",
         "| 戦略 | α net%/月 | t | 勝月% | n月 | OOS test | 最大DD |",
         "|---|--:|--:|--:|--:|---|--:|"]
    L += [line(lab, mm) for lab, mm in rows]
    L += ["", "## 判定",
          "- ボラ調整が 12-1 を **α同等以上 かつ 最大DD縮小 かつ OOS生存** なら採用候補(クラッシュ耐性の改良)。",
          "- α が落ちて DD だけ縮むなら『質は上がるがリターンは落ちる』トレードオフ＝好みの問題。"]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(REPORT_PATH, "\n".join(L) + "\n")
    for lab, mm in rows:
        s = stats(mm)
        te = stats([x for x in mm if x["date"] >= OOS_SPLIT])
        print(f"  {lab:18s} α{s['mean']:+.2f}%/t{s['t']:+.2f}/勝{s['win']:.0f}%/n{s['n']}"
              f"  OOS{te['mean']:+.2f}(t{te['t']:+.1f}) DD{max_dd(mm):.0f}%")


if __name__ == "__main__":
    main()

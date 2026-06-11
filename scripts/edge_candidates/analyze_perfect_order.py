"""パンパカパン（パーフェクトオーダー押し目）順張りパターンの期待値検証。

相場師朗系の『パンパカパン』: 5日・25日・75日線が全て上向き＆順序通り(5>25>75)で、
5日線が25日線に近づく(押し目)が交わらずに再上昇する形。上昇トレンド継続のサインとされる。

定量化:
  perfect_order = SMA5>SMA25>SMA75
  rising        = SMA25 が 20営業日前より上 かつ SMA75 が 20営業日前より上
  押し目→再開   = 直近 PB_WIN 日で (SMA5/SMA25-1) が PB_NEAR 以下まで接近(押し目)し、かつ
                  期間中 5日線が25日線を下抜けない(交わらず)、本日 SMA5 が再拡大(直近2日で上向き)
  エントリ      = シグナル翌営業日の寄り、保有 H 営業日後の引けで売り(複数 H を比較)
評価: 対TOPIX α(β=1) / コスト long 0.20% / 発火日クラスタ頑健 t / OOS=2024。
     同一銘柄の連続シグナルは PB_WIN 日内で1回に集約(重複保有の水増し回避)。

入力: cache/universe_bars.json (code→{date:close})。出力: reports/perfect_order.md
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
BARS_PATH = REPO_ROOT / "cache" / "universe_bars.json"
REPORT_PATH = REPO_ROOT / "reports" / "perfect_order.md"

PB_WIN = 10        # 押し目を探す窓(営業日)
PB_NEAR = 0.02     # 5日線が25日線の +2% 以内まで接近=押し目
COST_PCT = 0.20
OOS_SPLIT = "2024-01"
HORIZONS = [5, 10, 20]


def sma(px: list[float], i: int, n: int) -> float | None:
    """px[i] を末尾とする n本 単純移動平均。"""
    if i + 1 < n:
        return None
    return statistics.fmean(px[i + 1 - n:i + 1])


def find_signals(dates: list[str], px: list[float]) -> list[int]:
    """パンパカパン押し目→再開シグナルの index 一覧(同一エピソードは集約)。"""
    sigs: list[int] = []
    last = -10**9
    for i in range(80, len(px) - 1):
        s5, s25, s75 = sma(px, i, 5), sma(px, i, 25), sma(px, i, 75)
        s25p, s75p = sma(px, i - 20, 25), sma(px, i - 20, 75)
        s5y, s5y2 = sma(px, i - 1, 5), sma(px, i - 2, 5)
        if None in (s5, s25, s75, s25p, s75p, s5y, s5y2):
            continue
        if not (s5 > s25 > s75 and s25 > s25p and s75 > s75p):   # perfect order & rising
            continue
        # 直近PB_WIN日で 5日線が25日線に接近(押し目)・かつ下抜けなし
        near = cross = False
        for k in range(i - PB_WIN, i + 1):
            a, b = sma(px, k, 5), sma(px, k, 25)
            if a is None or b is None:
                continue
            if a < b:
                cross = True
            if 0 <= a / b - 1 <= PB_NEAR:
                near = True
        if not near or cross:
            continue
        if not (s5 > s5y > s5y2):   # 本日 5日線が再拡大(上向き)
            continue
        if i - last < PB_WIN:       # 同一エピソードは集約
            continue
        sigs.append(i)
        last = i
    return sigs


def _clustered_t(byd: dict[str, list[float]]) -> tuple[float, float, int]:
    """発火日クラスタ頑健 t。"""
    allv = [v for vs in byd.values() for v in vs]
    n = len(allv)
    if n < 2:
        return (statistics.fmean(allv) if allv else 0.0), 0.0, n
    mean = statistics.fmean(allv)
    num = sum(sum(v - mean for v in vs) ** 2 for vs in byd.values())
    se = math.sqrt(num) / n
    return mean, (mean / se if se else 0.0), n


def main() -> None:
    closes = {c: m for c, m in json.loads(BARS_PATH.read_text()).items() if m}
    topix = {r["Date"]: r["C"] for r in json.loads(TOPIX_PATH.read_text())["records"] if r.get("C")}
    cal = sorted(topix)
    idx = {d: i for i, d in enumerate(cal)}

    # 各銘柄のシグナル収集（H別の α を貯める）
    per_h: dict[int, list[dict[str, Any]]] = {h: [] for h in HORIZONS}
    for code, m in closes.items():
        dates = [d for d in cal if d in m]
        px = [m[d] for d in dates]
        for si in find_signals(dates, px):
            d0 = dates[si]
            if d0 not in idx:
                continue
            gi = idx[d0]
            if gi + 1 >= len(cal):
                continue
            ed = cal[gi + 1]              # 翌営業日(エントリ)
            if ed not in m:
                continue
            entry = m[ed]
            for h in HORIZONS:
                if gi + 1 + h >= len(cal):
                    continue
                xd = cal[gi + 1 + h]
                if xd not in m:
                    continue
                ret = (m[xd] / entry - 1.0) * 100.0
                ta, tb = topix.get(ed), topix.get(xd)
                tx = (tb / ta - 1.0) * 100.0 if (ta and tb) else 0.0
                per_h[h].append({"date": d0, "alpha": ret - tx - COST_PCT})

    L = ["# パンパカパン（パーフェクトオーダー押し目）順張り検証", "",
         f"大型+中型 {len(closes)}銘柄。perfect order(5>25>75・上向き)＋押し目→再開。"
         f" シグナル翌寄り買い→H日後引け。α=対TOPIX(β=1)・コスト{COST_PCT}%。OOS={OOS_SPLIT}。", "",
         "| 保有H | α net%/trade | t_clust | 勝率% | n | OOS test |", "|--:|--:|--:|--:|--:|---|"]
    for h in HORIZONS:
        recs = per_h[h]
        byd: dict[str, list[float]] = {}
        for r in recs:
            byd.setdefault(r["date"], []).append(r["alpha"])
        m_, t_, n_ = _clustered_t(byd)
        win = (sum(1 for r in recs if r["alpha"] > 0) / len(recs) * 100) if recs else 0.0
        bte: dict[str, list[float]] = {}
        for r in recs:
            if r["date"] >= OOS_SPLIT:
                bte.setdefault(r["date"], []).append(r["alpha"])
        mte, tte, nte = _clustered_t(bte)
        L.append(f"| {h}日 | {m_:+.2f} | {t_:+.2f} | {win:.0f} | {n_} | {mte:+.2f}(t{tte:+.1f})/n{nte} |")
    L += ["", "## 判定",
          "- α net>0 かつ |t_clust|≥2、OOS も同符号で残れば順張りパターンとして有効。",
          "- raw が正でも対TOPIX α が消えれば『上昇相場でロングしただけ(β)』＝12-1で既に取れている。"]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(REPORT_PATH, "\n".join(L) + "\n")
    for h in HORIZONS:
        recs = per_h[h]
        byd: dict[str, list[float]] = {}
        for r in recs:
            byd.setdefault(r["date"], []).append(r["alpha"])
        m_, t_, n_ = _clustered_t(byd)
        win = (sum(1 for r in recs if r["alpha"] > 0) / len(recs) * 100) if recs else 0.0
        print(f"  H{h:2d}日 αnet{m_:+.2f}% t{t_:+.2f} win{win:.0f}% n{n_}")


if __name__ == "__main__":
    main()

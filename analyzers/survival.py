"""生存・ドローダウン分析 (Monte Carlo trade-bootstrap、stdlib のみ)。

validate_edges は各エッジの α/t/FDR/OOS は出すが、「実際に張ったとき
どれだけ食らうか」= ドローダウン分布・破産確率・最大連敗 を出さない。
ケリー基準/資金管理/ファットテールの観点 (tikeda クオンツ連載 ほか) では、
低勝率・高ペイオフ型 (⑩中型S高) や βフルの月次モメンタム (-30%DD) は、
平均αではなく『どれだけ生き残れるか』で実運用可否が決まる。

本モジュールは per-trade の net 損益列を入力に、IID ブートストラップで
合成エクイティ経路を多数生成し、以下の生存統計を返す:
  - 最大DD の中央値 / worst-5% (95 パーセンタイル)
  - P(最大DD≥30%)         : 心理的に耐えがたい水準への到達率
  - P(資金半減)           : equity が一度でも 0.5 を割る確率 (≒破産確率)
  - 最大連敗の中央値 / worst-5%
  - 終端 equity の中央値 / P(累積マイナス)

賭け比率 f (1トレードに資本の何割を張るか) を変えて非線形な破滅感応度を見る。
依存ライブラリなし (random / statistics のみ)。
"""
from __future__ import annotations

import random
import statistics
from typing import Sequence


def max_drawdown(equity: Sequence[float]) -> float:
    """エクイティ経路の最大ドローダウン (正の比率, 0.30 = -30%) を返す。"""
    peak = equity[0] if equity else 1.0
    mdd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        if peak > 0:
            dd = 1.0 - e / peak
            if dd > mdd:
                mdd = dd
    return mdd


def max_losing_streak(returns: Sequence[float]) -> int:
    """連続して負け (ret < 0) が続いた最長回数を返す。"""
    best = cur = 0
    for r in returns:
        if r < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _percentile(sorted_vals: list[float], q: float) -> float:
    """昇順済みリストの q (0..1) パーセンタイル (線形補間)。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    frac = pos - lo
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def bootstrap_survival(
    net_returns: Sequence[float],
    *,
    f: float = 1.0,
    horizon: int | None = None,
    n_paths: int = 20000,
    seed: int = 42,
) -> dict[str, float]:
    """per-trade net 損益(%)列から MC ブートストラップで生存統計を返す。

    net_returns: 1トレードの net 損益を **パーセント** で (例 +0.53, -2.1)。
    f          : 賭け比率。equity *= (1 + f * ret/100) で複利。1.0=資本全張り。
    horizon    : 1経路あたりのトレード数 (既定 = 母集団サイズ = 1巡分)。
    n_paths    : 経路数。各経路で母集団から復元抽出。

    返り値の DD/streak は経路横断の分布統計。p_dd30 / p_ruin_half は到達確率。
    """
    rets = [float(r) for r in net_returns if r is not None]
    if len(rets) < 2:
        return {"n_trades": len(rets)}
    h = horizon or len(rets)
    rng = random.Random(seed)

    mdds: list[float] = []
    streaks: list[int] = []
    ends: list[float] = []
    n_dd30 = n_ruin = n_loss = 0
    for _ in range(n_paths):
        path = [rets[rng.randrange(len(rets))] for _ in range(h)]
        eq = 1.0
        curve = [1.0]
        ruined = False
        for r in path:
            eq *= 1.0 + f * r / 100.0
            if eq <= 0.0:
                eq = 1e-9
            curve.append(eq)
            if eq <= 0.5:
                ruined = True
        mdd = max_drawdown(curve)
        mdds.append(mdd)
        streaks.append(max_losing_streak(path))
        ends.append(eq)
        if mdd >= 0.30:
            n_dd30 += 1
        if ruined:
            n_ruin += 1
        if eq < 1.0:
            n_loss += 1

    mdds.sort()
    streaks_sorted = sorted(streaks)
    ends.sort()
    return {
        "n_trades": len(rets),
        "horizon": h,
        "f": f,
        "per_trade_mean": statistics.fmean(rets),
        "per_trade_win": sum(1 for r in rets if r > 0) / len(rets),
        "mdd_median": _percentile(mdds, 0.50),
        "mdd_p95": _percentile(mdds, 0.95),
        "p_dd30": n_dd30 / n_paths,
        "p_ruin_half": n_ruin / n_paths,
        "streak_median": _percentile([float(s) for s in streaks_sorted], 0.50),
        "streak_p95": _percentile([float(s) for s in streaks_sorted], 0.95),
        "end_median": _percentile(ends, 0.50),
        "p_loss": n_loss / n_paths,
    }

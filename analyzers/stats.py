"""エッジ検証用の統計ヘルパ (stdlib のみ)。

過剰最適化・偽陽性を抑えるための道具:
  - t_to_p           : t 値 → 両側 p 値 (正規近似、n>=30 目安)
  - benjamini_hochberg: 多重検定の FDR 補正 (大量のセルを試す際の偽陽性抑制)
  - clustered_se     : クラスタ頑健標準誤差 (同日内の相関で t が水増しされるのを補正)
  - evaluate_cells   : セル群を full-sample + walk-forward(OOS) で評価し FDR を付与

依存ライブラリなし (math / statistics のみ)。
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Sequence


def t_to_p(t: float) -> float:
    """t 値 → 両側 p 値 (標準正規近似)。

    p = 2*(1 - Φ(|t|)), Φ(x)=0.5*(1+erf(x/√2))。
    n>=30 程度で妥当。小 n では反保守的になる点に注意 (n は別途併記すること)。
    """
    z = abs(t)
    cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return max(0.0, min(1.0, 2.0 * (1.0 - cdf)))


def benjamini_hochberg(pvalues: Sequence[float], alpha: float = 0.05) -> list[bool]:
    """Benjamini-Hochberg FDR 補正。入力順に揃えた survive 真偽リストを返す。

    m 個の検定で、p(k) <= (k/m)*alpha を満たす最大 rank k までを有意とする
    (FDR を alpha 以下に制御)。多数のセルを試したときの偽発見を抑える。
    """
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    max_rank = 0
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= (rank / m) * alpha:
            max_rank = rank
    survive = [False] * m
    for rank, idx in enumerate(order, start=1):
        if rank <= max_rank:
            survive[idx] = True
    return survive


def clustered_se(values: Sequence[float], clusters: Sequence[Any]) -> float:
    """平均推定量のクラスタ頑健標準誤差 (one-way clustering)。

    同一クラスタ (例: 同一営業日) 内のリターンは相関するため、素朴な SE は
    過小評価 = t 水増しになる。クラスタ和の残差で分散を組み直す:
      Var(μ) = c * Σ_g (Σ_{i∈g}(x_i-μ))² / N²,  c = G/(G-1) 有限クラスタ補正。
    """
    n = len(values)
    if n < 2:
        return 0.0
    mu = statistics.fmean(values)
    by_cluster: dict[Any, float] = defaultdict(float)
    for v, c in zip(values, clusters):
        by_cluster[c] += (v - mu)
    g = len(by_cluster)
    if g < 2:
        # クラスタが 1 個 = 全部相関し得る → 素朴 SE に退避 (情報不足)
        s = statistics.stdev(values)
        return s / math.sqrt(n) if s else 0.0
    meat = sum(s * s for s in by_cluster.values())
    correction = g / (g - 1)
    var = correction * meat / (n * n)
    return math.sqrt(var) if var > 0 else 0.0


def _direction(rets: Sequence[float]) -> str:
    return "short" if statistics.fmean(rets) < 0 else "long"


def _net(ret: float, direction: str, cost_pct: float) -> float:
    base = -ret if direction == "short" else ret
    return base - cost_pct


def evaluate_cells(
    observations: Sequence[dict[str, Any]],
    *,
    cost_pct: float = 0.20,
    alpha: float = 0.05,
    split_frac: float = 0.7,
    min_n: int = 5,
) -> list[dict[str, Any]]:
    """セル群を評価して結果リストを返す。

    observations: 各要素 {"cell": hashable, "ret": float, "date": ISO str, "code": str}。
    各セルで:
      - 方向 = 生 EV 符号。net = 約定方向損益 - cost。
      - t (素朴) と t_clustered (date クラスタ) を算出。p は t_clustered から。
      - walk-forward: 日付順に split_frac で train/test 分割。方向は train で決め
        (lookahead 回避)、test の net EV を OOS 成績として返す。
    全セルの p に BH-FDR を適用し fdr_significant を付与。
    """
    cells: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for o in observations:
        if o.get("ret") is not None:
            cells[o["cell"]].append(o)

    results: list[dict[str, Any]] = []
    for cell, obs in cells.items():
        if len(obs) < min_n:
            continue
        rets = [float(o["ret"]) for o in obs]
        direction = _direction(rets)
        nets = [_net(float(o["ret"]), direction, cost_pct) for o in obs]
        n = len(nets)
        mean = statistics.fmean(nets)
        sd = statistics.stdev(nets) if n > 1 else 0.0
        se = sd / math.sqrt(n) if sd else 0.0
        t = mean / se if se else 0.0
        cse = clustered_se(nets, [o.get("date") for o in obs])
        t_clu = mean / cse if cse else 0.0
        p = t_to_p(t_clu)

        # walk-forward (方向は train のみで決定)
        obs_sorted = sorted(obs, key=lambda o: o.get("date") or "")
        cut = int(len(obs_sorted) * split_frac)
        train, test = obs_sorted[:cut], obs_sorted[cut:]
        train_ev = test_ev = None
        train_n = len(train)
        test_n = len(test)
        robust = None
        if train and test:
            tr_dir = _direction([float(o["ret"]) for o in train])
            train_ev = statistics.fmean([_net(float(o["ret"]), tr_dir, cost_pct) for o in train])
            test_ev = statistics.fmean([_net(float(o["ret"]), tr_dir, cost_pct) for o in test])
            robust = test_ev > 0  # OOS でも net プラスなら頑健

        results.append({
            "cell": cell,
            "n": n,
            "direction": direction,
            "ev_net": mean,
            "t": t,
            "t_clustered": t_clu,
            "p": p,
            "train_n": train_n,
            "train_ev_net": train_ev,
            "test_n": test_n,
            "test_ev_net": test_ev,
            "robust_oos": robust,
        })

    sig = benjamini_hochberg([r["p"] for r in results], alpha)
    for r, s in zip(results, sig):
        r["fdr_significant"] = s
    results.sort(key=lambda r: r["p"])
    return results

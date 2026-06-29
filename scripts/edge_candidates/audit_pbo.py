"""PBO (Probability of Backtest Overfitting) / CSCV 監査。

「N個の戦略構成(セル/パラメータ)から"最良"を選ぶと、それは過学習か」を定量化する。
Bailey, Borwein, López de Prado, Zhu (2017) の Combinatorial Symmetric Cross-Validation(CSCV)。

手順: 時系列をS個の連続ブロックに分け、C(S,S/2)通りで半分をIS(訓練)・半分をOS(検証)に。
各組合せで IS最良戦略n* を選び、その n* の OS順位 ω∈(0,1) のロジット λ=ln(ω/(1-ω)) を集計。
**PBO = λ≤0(=IS最良がOS中央値以下)の割合**。0.5近辺=コイン投げ=完全過学習、低いほど頑健。

既存の FDR(validate_edges) / DSR(audit_deflated_sharpe) / 非重複・PIT(本セッション確立) に重ねる
"戦略選択そのものの過学習"の絶対補正。何セル叩いたかを CSCV で織り込む。

使い方:
  python -m scripts.edge_candidates.audit_pbo            # 自己検証デモ(過学習例/頑健例)
  # ライブラリ: from ... import combinatorial_pbo; combinatorial_pbo(cols, n_splits=16)
"""
from __future__ import annotations

import argparse
import math
import statistics as st
from itertools import combinations
from pathlib import Path

from scripts._atomic import atomic_write_text

REPO = Path(__file__).resolve().parent.parent.parent


def _sharpe(rets: list[float]) -> float:
    """per-period Sharpe(平均/標準偏差)。年率化はランク用途では不要。"""
    if len(rets) < 2:
        return 0.0
    sd = st.pstdev(rets)
    return st.fmean(rets) / sd if sd else 0.0


def combinatorial_pbo(cols: list[list[float]], n_splits: int = 16,
                      max_combos: int = 20000) -> dict:
    """CSCV で PBO を計算。

    cols: N個の戦略構成それぞれの per-period リターン列(全列とも長さ T で時間整合済)。
    n_splits S: 偶数。時系列を S 連続ブロックに分割し C(S,S/2) の IS/OS 分割で評価。
    返り値: pbo(0-1) / n_configs / n_periods / n_combos / median_logit /
            prob_oos_loss(IS最良のOSが負の割合) / perf_degradation(IS最良のOS-Sharpe中央値)。
    """
    n = len(cols)
    if n < 2:
        return {"pbo": float("nan"), "n_configs": n, "n_periods": 0, "n_combos": 0,
                "median_logit": float("nan"), "prob_oos_loss": float("nan"),
                "perf_degradation": float("nan")}
    t = len(cols[0])
    s = n_splits if n_splits % 2 == 0 else n_splits - 1
    s = max(4, min(s, t))                      # ブロック数はTを超えない
    bnd = [round(i * t / s) for i in range(s + 1)]
    groups = [list(range(bnd[i], bnd[i + 1])) for i in range(s)]
    combos = list(combinations(range(s), s // 2))
    if len(combos) > max_combos:               # 多すぎたら決定論的に間引く
        step = len(combos) // max_combos
        combos = combos[::step][:max_combos]
    logits, oos_best, n_loss = [], [], 0
    for is_g in combos:
        os_g = [g for g in range(s) if g not in is_g]
        is_rows = [r for g in is_g for r in groups[g]]
        os_rows = [r for g in os_g for r in groups[g]]
        is_sh = [_sharpe([cols[k][r] for r in is_rows]) for k in range(n)]
        n_star = max(range(n), key=lambda k: is_sh[k])
        os_sh = [_sharpe([cols[k][r] for r in os_rows]) for k in range(n)]
        rank = sorted(range(n), key=lambda k: os_sh[k]).index(n_star) + 1  # 1=最弱
        omega = min(max(rank / (n + 1), 1e-6), 1 - 1e-6)
        lam = math.log(omega / (1 - omega))
        logits.append(lam)
        oos_best.append(os_sh[n_star])
        if lam <= 0:
            n_loss += 1
    return {"pbo": n_loss / len(logits), "n_configs": n, "n_periods": t,
            "n_combos": len(combos), "median_logit": st.median(logits),
            "prob_oos_loss": sum(1 for x in oos_best if x < 0) / len(oos_best),
            "perf_degradation": st.median(oos_best)}


def pbo_from_cells(cells: dict[str, list[tuple]], n_splits: int = 16) -> dict:
    """{構成名: [(period_key, ret), ...]} から per-period 整合行列を作り PBO を返す。

    各構成のある期間のリターン = その期間のトレード平均(無ければ0)。全構成共通の period 軸で整合。
    event駆動エッジのセル群(subpattern×時刻 等)をそのまま渡せる。
    """
    periods = sorted({p for lst in cells.values() for p, _ in lst})
    pidx = {p: i for i, p in enumerate(periods)}
    cols = []
    for _name, lst in cells.items():
        col = [0.0] * len(periods)
        bucket: dict = {}
        for p, r in lst:
            bucket.setdefault(p, []).append(r)
        for p, rs in bucket.items():
            col[pidx[p]] = st.fmean(rs)
        cols.append(col)
    return combinatorial_pbo(cols, n_splits=n_splits)


def _demo_matrices() -> dict:
    """自己検証用: 純ノイズN戦略(過学習例・PBO~0.5期待) と 1本だけ真の信号(頑健例・PBO低期待)。"""
    import random
    random.seed(42)
    t, n = 240, 40
    noise = [[random.gauss(0, 1) for _ in range(t)] for _ in range(n)]
    signal = [c[:] for c in noise]
    signal[0] = [random.gauss(0.4, 1) for _ in range(t)]    # 1本に持続的な正のedge
    return {"純ノイズ40戦略(過学習例)": noise, "1本だけ真の信号(頑健例)": signal}


def build_report(cells: dict | None = None) -> str:
    """PBO 監査レポート(自己検証デモ＋任意の実セル)を md にまとめて返す。"""
    L = ["# PBO (Probability of Backtest Overfitting) 監査", "",
         "CSCV(Bailey-Borwein-López de Prado-Zhu 2017)。**PBO=IS最良戦略がOS中央値以下になる確率**。",
         "0.5近辺=コイン投げ=完全過学習 / <0.2目安=頑健。FDR・DSR・非重複・PITに重ねる絶対補正。", "",
         "## 自己検証デモ", "", "| ケース | PBO | OS劣化(中央Sharpe) | P(OS負) | n構成 |", "|---|---|---|---|---|"]
    for name, mat in _demo_matrices().items():
        r = combinatorial_pbo(mat, n_splits=14)
        L.append(f"| {name} | {r['pbo']:.2f} | {r['perf_degradation']:+.3f} | "
                 f"{r['prob_oos_loss']:.2f} | {r['n_configs']} |")
    L += ["", "→ 純ノイズはPBO~0.5(=最良を選んでもOSでコイン投げ)、真の信号入りは低PBO=ツールが過学習を弁別。", ""]
    if cells:
        r = pbo_from_cells(cells)
        L += ["## 実セル群の PBO", "",
              f"- n構成={r['n_configs']} / n期間={r['n_periods']} / 組合せ={r['n_combos']}",
              f"- **PBO={r['pbo']:.2f}** / OS劣化中央Sharpe={r['perf_degradation']:+.3f} / P(OS負)={r['prob_oos_loss']:.2f}",
              f"- 判定: {'⚠️過学習リスク高(PBO≥0.5)' if r['pbo'] >= 0.5 else '△要注意(0.2-0.5)' if r['pbo'] >= 0.2 else '✅頑健(PBO<0.2)'}", ""]
    L += ["## 使い方(実スイープのセル群を渡す)", "",
          "```python", "from scripts.edge_candidates.audit_pbo import pbo_from_cells",
          "# cells = {セル名: [(期間キー, リターン), ...]}  ←スイープの各構成のper-trade",
          "print(pbo_from_cells(cells, n_splits=16))", "```",
          "＝技術スイープ124セル/閾値変種/IPO層別 等を渡せば『最良セルを選ぶ過学習度』が一発で出る。"]
    return "\n".join(L) + "\n"


def main() -> None:
    """PBO 監査レポートを生成して reports/pbo_audit.md に書き出す。"""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "reports" / "pbo_audit.md")
    args = ap.parse_args()
    rep = build_report()
    print(rep)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, rep)
    print(f"[pbo] → {args.out}")


if __name__ == "__main__":
    main()

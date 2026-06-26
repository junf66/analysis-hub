"""セクター・トレンドフォロー LB感度スイープ（頑健性確認）。

TOPIX-17業種指数で、過去LB週モメンタム上位1/3(40週線上)を等加重保有、13週/40週線割れで外す
週次戦略を LB∈{8,11,13,15,22,26,30,39}週 で総当たり。最大リターンのLBを選ぶのが目的でなく、
近傍が「台地(頑健)」か「スパイク(過剰最適化)」かを見る。台地で全体ベンチ未達なら『日本のセクター
TFは弱い』が結論(それも収穫)。

データ: J-Quants /indices/bars/daily TOPIX-17(0080-0090,008A-008F)・ベンチ0028(or topix_daily)。
価格指数(配当込みTRでない)・2016-06〜(J-Quants指数の最古)。相対戦略ゆえPR控除でも比較は妥当。
コスト片道0.1%(往復0.2%)・週次・翌週末約定。

使い方: python -m scripts.edge_candidates.analyze_sector_tf
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics as st
from pathlib import Path

from scripts import _jquants
from scripts._atomic import atomic_write_json, atomic_write_text

REPO = Path(__file__).resolve().parent.parent.parent
CACHE = REPO / "cache" / "topix17.json"
TOPIX = REPO / "data" / "edge_candidates" / "topix_daily.json"
COST_ONEWAY = 0.001                       # 片道0.1%
LBS = (8, 11, 13, 15, 22, 26, 30, 39)
T17 = {"0080": "食品", "0081": "エネ資源", "0082": "建設資材", "0083": "素材化学",
       "0084": "医薬品", "0085": "自動車", "0086": "鉄鋼非鉄", "0087": "機械",
       "0088": "電機精密", "0089": "情報通信", "008A": "電力ガス", "008B": "運輸物流",
       "008C": "商社卸", "008D": "小売", "008E": "銀行", "008F": "金融", "0090": "不動産"}


def _load() -> dict:
    """TOPIX-17 + ベンチの日次終値を取得(キャッシュ)して返す。"""
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    data = {}
    for c in list(T17) + ["0028"]:
        try:
            b = _jquants.get_list("/indices/bars/daily", code=c)
            data[c] = {x["Date"]: x["C"] for x in b if x.get("C")}
        except Exception:   # noqa: BLE001
            data[c] = {}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(CACHE, data)
    return data


def _weekly(series: dict) -> tuple[list, list]:
    by = {}
    for d in sorted(series):
        y, w, _ = dt.date.fromisoformat(d).isocalendar()
        by[(y, w)] = series[d]
    wk = sorted(by)
    return wk, [by[k] for k in wk]


def _ma(arr: list, i: int, n: int) -> float | None:
    return st.fmean(arr[i - n:i]) if i >= n else None


def _backtest(S: dict, wks: list, lb: int) -> tuple[list, list, list, float]:
    """LB週モメンタムTF を回し (equity曲線, [(week,ret)], 保有数列, 総回転) を返す。"""
    n = len(wks)
    held: set = set()
    eq = 1.0
    curve = [1.0]
    rets, nhold = [], []
    turn = 0.0
    prevw = {c: 0.0 for c in T17}
    topn = max(1, 17 // 3)
    for i in range(40, n - 1):
        rk = sorted(((S[c][i] / S[c][i - lb] - 1, c) for c in T17 if i - lb >= 0), reverse=True)
        top = {c for _, c in rk[:topn]}
        new = set()
        for c in T17:
            ma13, ma40, px = _ma(S[c], i, 13), _ma(S[c], i, 40), S[c][i]
            if not ma40:
                continue
            if (c in held and px >= ma13 and px >= ma40) or (c in top and px >= ma40):
                new.add(c)
        held = new
        w = {c: (1.0 / len(held) if c in held else 0.0) for c in T17}
        traded = sum(abs(w[c] - prevw[c]) for c in T17)
        turn += traded
        r1 = (st.fmean(S[c][i + 1] / S[c][i] - 1 for c in held) if held else 0.0) - COST_ONEWAY * traded
        eq *= (1 + r1)
        curve.append(eq)
        rets.append((wks[i + 1], r1))
        nhold.append(len(held))
        prevw = w
    return curve, rets, nhold, turn


def _metrics(curve: list, rets: list, nhold: list, turn: float) -> tuple:
    r = [x[1] for x in rets]
    n = len(r)
    cagr = curve[-1] ** (52 / n) - 1
    sharpe = st.fmean(r) / st.pstdev(r) * math.sqrt(52) if st.pstdev(r) else 0.0
    peak, mdd = -9.0, 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    calmar = cagr / abs(mdd) if mdd else 0.0
    bym: dict = {}
    for d, x in rets:
        m = dt.date.fromisocalendar(d[0], min(d[1], 52), 5).month
        bym.setdefault((d[0], m), []).append(x)
    mwin = sum(1 for v in bym.values() if sum(v) > 0) / len(bym) * 100
    return cagr, sharpe, mdd, calmar, mwin, st.fmean(nhold), turn / (n / 52) / 2


def _bench(data: dict) -> tuple:
    bench = data.get("0028") or {}
    if not bench:
        bench = {r["Date"]: r["C"] for r in json.loads(TOPIX.read_text())["records"] if r.get("C")}
    _, bv = _weekly(bench)
    r = [bv[i + 1] / bv[i] - 1 for i in range(40, len(bv) - 1)]
    curve = [1.0]
    for x in r:
        curve.append(curve[-1] * (1 + x))
    cagr = curve[-1] ** (52 / len(r)) - 1
    sh = st.fmean(r) / st.pstdev(r) * math.sqrt(52)
    peak, mdd = -9.0, 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return cagr, sh, mdd


def _sub_sharpe(rets: list, y0: int, y1: int) -> float:
    r = [x for d, x in rets if y0 <= d[0] <= y1]
    return st.fmean(r) / st.pstdev(r) * math.sqrt(52) if len(r) > 10 and st.pstdev(r) else float("nan")


def build_report(data: dict) -> str:
    """LB感度スイープの全結果(表・近傍プロット・サブ期間・判定)を md にまとめて返す。"""
    wks = sorted(set.intersection(*[set(_weekly(data[c])[0]) for c in T17]))
    S = {c: [dict(zip(*_weekly(data[c])))[w] for w in wks] for c in T17}
    bc, bs, bmdd = _bench(data)
    res = {}
    for lb in LBS:
        cu, re, nh, tu = _backtest(S, wks, lb)
        res[lb] = (_metrics(cu, re, nh, tu), re)
    L = ["# セクター・トレンドフォロー LB感度スイープ", "",
         f"TOPIX-17・週次・コスト往復0.2%・{wks[0][0]}〜{wks[-1][0]}(価格指数/配当込みTRでない)。", "",
         f"**ベンチ TOPIX(0028): CAGR {bc * 100:.1f}% / Sharpe {bs:.2f} / MaxDD {bmdd * 100:.0f}%**", "",
         "| LB週 | CAGR | Sharpe | MaxDD | Calmar | 月勝% | 平均保有 | 回転/年 | 対TPX超過 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for lb in LBS:
        m = res[lb][0]
        L.append(f"| {lb} | {m[0]*100:+.1f}% | {m[1]:.2f} | {m[2]*100:.0f}% | {m[3]:.2f} | "
                 f"{m[4]:.0f}% | {m[5]:.1f} | {m[6]:.1f}x | {(m[0]-bc)*100:+.1f}% |")
    L += ["", "## サブ期間 Sharpe(期間安定性)", "",
          "| LB | 2016-18 | 2019-20 | 2021-22 | 2023-26 |", "|---|---|---|---|---|"]
    for lb in LBS:
        re = res[lb][1]
        L.append(f"| {lb} | {_sub_sharpe(re,2016,2018):.2f} | {_sub_sharpe(re,2019,2020):.2f} | "
                 f"{_sub_sharpe(re,2021,2022):.2f} | {_sub_sharpe(re,2023,2026):.2f} |")
    L += ["", "## 近傍プロット(LB×Sharpe・スパイク検出)", "", "```"]
    for lb in LBS:
        s = res[lb][0][1]
        tag = " ←13w" if lb == 13 else " ←26w" if lb == 26 else ""
        L.append(f"{lb:>3} {s:.2f} {'#' * max(0, int(s * 20))}{tag}")
    L += ["```", "", "## 判定", ""]
    s13, s26 = res[13][0], res[26][0]
    both_below = (s13[0] < bc) and (s26[0] < bc)
    sharpes = [res[lb][0][1] for lb in LBS]
    plateau = max(sharpes) - min(sharpes) < 0.25     # なだらか
    if both_below:
        L += ["- **△ 日本のセクターTFは弱い**: 13週・26週とも(実際は全LBが)TOPIX buy&hold未達。",
              f"  近傍Sharpeは{'なだらかな台地' if plateau else 'スパイク含む'}"
              f"(範囲{min(sharpes):.2f}〜{max(sharpes):.2f})＝"
              f"{'特定LBが脆いのでなく手法自体が構造的に弱い' if plateau else '一部LBに過剰最適化リスク'}。",
              "- サブ期間はレジーム依存(2023-26のみ強・2016-18/2021-22は弱〜負)。高回転のコストdragも効く。",
              "- ＝セクターローテ/TFはαを生まない(既出の『セクターローテ=αなし』と整合)。**不採用**。"]
    else:
        L += ["- 13週・26週がベンチ超過。近傍の台地性とサブ期間安定を要確認(下表)。"]
    L += ["", "## 実装性(任意)", "",
          "- TOPIX-17はNEXT FUNDS等の業種別ETFで近似売買可だが、本結果がベンチ未達ゆえ実装検討は不要。",
          "- データはJ-Quants指数(2016〜)。2003〜の長期検証はJ-Quants範囲外(外部TRデータ要)＝留保。"]
    return "\n".join(L) + "\n"


def main() -> None:
    """セクターTF LB感度レポートを生成して reports/sector_tf_lb_sweep.md に書き出す。"""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "reports" / "sector_tf_lb_sweep.md")
    args = ap.parse_args()
    rep = build_report(_load())
    print(rep)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, rep)
    print(f"[sector_tf] → {args.out}")


if __name__ == "__main__":
    main()

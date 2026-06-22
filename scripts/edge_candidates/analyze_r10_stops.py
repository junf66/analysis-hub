"""⑩Rに損切りストップを足すと改善するか（テール縮小目的・EV最大化の事後選択はしない）。

⑩R確定コホート(S高引け×スタ/グロ×貸借×中GU5-10%, a型 n≈318)を、エントリー日(=S高翌日)の
日足O/H/Cで再現。ショートのストップは「日中高値H≧ストップ水準なら水準+滑りで買戻、超えなければ引け」。
日足Hで判定可ゆえ全期間(2017-)で検証(分足不要)。

ストップ水準: (a)建値O+X% X∈{5,6,8,10,12,15} (b)前日終値(=S高価格)+Y% Y∈{8,10,12,15}
            (c)エントリー日の値幅上限(再S高ライン)−2tick。滑り0.15%(保守)。
指標: net EV/勝率/t_clust/DSR/最大DD(実現+MC)/P(DD≥30%)/worst/踏み上げ回避数/whipsaw損。
事前宣言: テール(最大DD/worst)を縮める目的。EVをほぼ保ったまま最もDD/worstを縮める水準を選ぶ。
出力: 標準出力(比較表)。
"""
from __future__ import annotations
import bisect
import json
import math
import random
import statistics
from pathlib import Path
from analyzers.stats import clustered_se, sharpe_moments, expected_max_sharpe, deflated_sharpe
from scripts.edge_candidates.verify_edges_standalone import _load, edge_rows

REPO = Path(__file__).resolve().parent.parent.parent
CACHE = REPO / "cache" / "sh_abc_bars.json"
SHORT_COST = 0.15
SLIP = 0.15           # ストップ約定の滑り(保守) %
TICK = 0.005          # (c)用の概算: 上限の0.5%下を再S高ラインの目安に


def _width(p: float) -> float:
    """値幅制限(概算・円)。"""
    for hi, w in [(100,30),(200,50),(500,80),(700,100),(1000,150),(1500,300),(2000,400),
                  (3000,500),(5000,700),(7000,1000),(10000,1500),(15000,3000),(20000,4000),
                  (30000,5000),(50000,7000),(70000,10000),(100000,15000)]:
        if p < hi:
            return w
    return 15000


def _net_with_stop(o, h, c, stop_L):
    """ショート net%: H≧stop_Lなら stop_L*(1+滑り)で買戻、否なら c で。Noneなら無効。"""
    if not (o and c):
        return None
    if stop_L is not None and h and h >= stop_L:
        cover = stop_L * (1 + SLIP / 100)
        return -(cover / o - 1) * 100 - SHORT_COST
    return -(c / o - 1) * 100 - SHORT_COST


def _maxdd(nets):
    """実現系列(日付順)の累積(加算)最大DD%。"""
    eq = 0.0; peak = 0.0; dd = 0.0
    for x in nets:
        eq += x; peak = max(peak, eq); dd = min(dd, eq - peak)
    return -dd


def _mc_dd(nets, paths=4000, seed=42):
    """trade-bootstrap で 最大DD分布 → 中央/worst5%/P(DD≥30%)。"""
    rng = random.Random(seed); n = len(nets); dds = []
    for _ in range(paths):
        s = [nets[rng.randrange(n)] for _ in range(n)]
        dds.append(_maxdd(s))
    dds.sort()
    return (statistics.median(dds), dds[int(len(dds) * 0.95)],
            sum(1 for d in dds if d >= 30) / len(dds) * 100)


def main() -> None:
    D = _load()
    rows, _, _ = edge_rows("⑩R", D)
    cache = json.loads(CACHE.read_text())
    cal = sorted(D["tpx"])

    def prevd(d):
        i = bisect.bisect_left(cal, d); return cal[i - 1] if i > 0 else None

    def nextd(d):
        i = bisect.bisect_right(cal, d); return cal[i] if i < len(cal) else None

    # a型(S高引け)コホート + エントリー日OHLC
    seen = set(); trades = []
    for io, d0, code, *_ in rows:
        if (d0, code) in seen:
            continue
        seen.add((d0, code))
        b = cache.get(code, {}); pv = prevd(d0); ed = nextd(d0)
        if d0 not in b or pv not in b or ed not in b:
            continue
        _, h0, _, c0 = b[d0]; pc0 = b[pv][3]
        if not (h0 and c0 and pc0):
            continue
        lim0 = pc0 + _width(pc0)
        is_a = (c0 >= lim0 * 0.998) and not (h0 > lim0 * 1.002)   # S高引け(a) かつ非値幅拡大
        if not is_a:
            continue
        eo, eh, el, ec = b[ed]                # エントリー日 O/H/L/C
        if not (eo and eh and ec):
            continue
        prev_close = c0                       # =S高価格(エントリー前日終値)
        ent_lim = prev_close + _width(prev_close)   # エントリー日の値幅上限(再S高ライン概算)
        trades.append({"d0": d0, "month": d0[:7], "o": eo, "h": eh, "c": ec,
                       "prev_close": prev_close, "ent_lim": ent_lim})
    print(f"⑩R a型(S高引け)コホート n={len(trades)} (期間 {trades[0]['d0']}〜{trades[-1]['d0']})\n")

    # 水準定義
    settings = [("ストップ無し", None)]
    for X in (15, 12, 10, 8, 6, 5):
        settings.append((f"(a)建値+{X}%", ("o", X)))
    for Y in (15, 12, 10, 8):
        settings.append((f"(b)前日終値+{Y}%", ("pc", Y)))
    settings.append(("(c)再S高ライン-2tick", ("lim", None)))

    def stop_level(t, kind):
        if kind is None:
            return None
        typ, val = kind
        if typ == "o":
            return t["o"] * (1 + val / 100)
        if typ == "pc":
            return t["prev_close"] * (1 + val / 100)
        if typ == "lim":
            return t["ent_lim"] * (1 - 2 * TICK)
        return None

    base_nets = [_net_with_stop(t["o"], t["h"], t["c"], None) for t in trades]
    n_trials = len(settings)
    # 試行集合のSR分布(DSR用)
    srs = []
    for _, kind in settings:
        nets = [_net_with_stop(t["o"], t["h"], t["c"], stop_level(t, kind)) for t in trades]
        srs.append(sharpe_moments(nets)[0])
    sr0 = expected_max_sharpe(statistics.pstdev(srs), n_trials)

    print(f"{'設定':<20}{'EV':>7}{'勝率':>6}{'t':>6}{'DSR':>6}{'最大DD':>7}{'worst':>7}{'DD≥30%':>7}{'踏回避':>6}{'whip損':>8}")
    print("-" * 92)
    for lab, kind in settings:
        nets = []; months = []; avoided = 0; whip_n = 0; whip_loss = 0.0
        for t, bn in zip(trades, base_nets):
            L = stop_level(t, kind)
            net = _net_with_stop(t["o"], t["h"], t["c"], L)
            nets.append(net); months.append(t["month"])
            if kind is not None:
                if bn < -8 and net > bn:        # 大負けを縮めた=踏み上げ回避
                    avoided += 1
                if (t["h"] and L and t["h"] >= L) and bn > 0 and net < bn:  # 本来勝ち建玉を切った
                    whip_n += 1; whip_loss += (bn - net)
        ev = statistics.fmean(nets); se = clustered_se(nets, months); t_c = ev / se if se else 0
        sr, sk, ku, n = sharpe_moments(nets); dsr = deflated_sharpe(sr, n, sk, ku, sr0)
        mdd = _maxdd(nets); worst = min(nets)
        _, _, pdd30 = _mc_dd(nets)
        wl = f"-{whip_loss:.1f}({whip_n})" if kind is not None else "—"
        av = str(avoided) if kind is not None else "—"
        print(f"{lab:<20}{ev:>+7.2f}{sum(1 for x in nets if x>0)/len(nets)*100:>5.0f}%{t_c:>+6.1f}{dsr:>6.2f}{mdd:>7.1f}{worst:>+7.1f}{pdd30:>6.1f}%{av:>6}{wl:>8}")
    print("\n注: EV/worst/DD は対TOPIX未調整の生io基準・cost0.15・滑り0.15。MC=4000経路・f=1(等加算)。")
    print("事前宣言通り: EVをほぼ保ち最大DD/worst/P(DD≥30%)を最も縮める水準を採用候補とする(EV最大化の事後選択はしない)。")


if __name__ == "__main__":
    main()

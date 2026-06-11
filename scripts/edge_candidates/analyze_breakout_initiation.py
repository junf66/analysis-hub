"""直近高値ブレイク『初動』仮説 と 5/25ゴールデンクロス の期待値検証。

ネタ元(投資本):
  - 同じ青天井でも『直近高値ブレイク(初動)』は好き、『新高値更新しまくり』は嫌い。
  - 1番儲かるのは動きのなかった状態から"いままさに動き始めた初動"。
  - 5日MAと25日MAのゴールデンクロス、一旦ふるい落としてからの(押し目)上昇。

定量化(close のみ使用 / 大型+中型 ~493):
  直近高値ブレイク = close が直近 W=60営業日の終値高値を更新。
    × 初動(静かな土台): 直前30日に新高値なし(久々の更新) かつ 直前60日ボラが下位1/3。
    × 連続新高値(更新しまくり): 直前20日に新高値が3回以上。
  5/25GC = SMA5 が SMA25 を下→上にクロスした当日。
評価: シグナル翌寄り買い→H日後引け / 対TOPIX α(β=1) / コスト0.20% / 発火日クラスタt / OOS=2024。
     同一銘柄の重複は W 日内で1回に集約。

入力: cache/universe_bars.json。出力: reports/breakout_initiation.md
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
REPORT_PATH = REPO_ROOT / "reports" / "breakout_initiation.md"

W = 60             # 直近高値の窓(営業日)
COST_PCT = 0.20
OOS_SPLIT = "2024-01"
HORIZONS = [5, 10, 20]


def sma(px: list[float], i: int, n: int) -> float | None:
    """px[i] 末尾の n本 単純移動平均。"""
    if i + 1 < n:
        return None
    return statistics.fmean(px[i + 1 - n:i + 1])


def realized_vol(px: list[float], i: int, win: int = W) -> float | None:
    """直近 win 営業日の日次リターン標準偏差。"""
    if i < win:
        return None
    rets = [px[k] / px[k - 1] - 1.0 for k in range(i - win + 1, i + 1) if px[k - 1]]
    return statistics.pstdev(rets) if len(rets) >= 20 else None


def is_new_high(px: list[float], i: int) -> bool:
    """close[i] が直近 W 日の終値高値を更新したか。"""
    return i >= W and px[i] > max(px[i - W:i])


def collect(dates: list[str], px: list[float]) -> dict[str, list[tuple[int, float]]]:
    """各バケットの (index, 直前60日ボラ) を返す。"""
    out: dict[str, list[tuple[int, float]]] = {"breakout_all": [], "shodo": [],
                                               "serial": [], "gc525": []}
    last_bo = last_gc = -10**9
    for i in range(W + 5, len(px) - 1):
        # 直近高値ブレイク
        if is_new_high(px, i) and i - last_bo >= 10:
            vol = realized_vol(px, i - 1)
            recent_high_30 = any(is_new_high(px, k) for k in range(i - 30, i))
            serial_cnt = sum(1 for k in range(i - 20, i) if is_new_high(px, k))
            out["breakout_all"].append((i, vol if vol else 0.0))
            if vol is not None and not recent_high_30:
                out["shodo"].append((i, vol))    # 久々の更新=初動候補(ボラ層別は後段)
            if serial_cnt >= 3:
                out["serial"].append((i, vol if vol else 0.0))
            last_bo = i
        # 5/25 ゴールデンクロス
        s5, s25 = sma(px, i, 5), sma(px, i, 25)
        s5p, s25p = sma(px, i - 1, 5), sma(px, i - 1, 25)
        if None not in (s5, s25, s5p, s25p) and s5p <= s25p and s5 > s25 and i - last_gc >= 10:
            out["gc525"].append((i, 0.0))
            last_gc = i
    return out


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


def evaluate(sig: list[dict[str, Any]], h: int) -> dict[str, Any]:
    """1バケット×H の全体/OOS統計。"""
    byd: dict[str, list[float]] = {}
    for r in sig:
        if h in r["a"]:
            byd.setdefault(r["date"], []).append(r["a"][h])
    m, t, n = _clustered_t(byd)
    flat = [v for vs in byd.values() for v in vs]
    win = (sum(1 for v in flat if v > 0) / len(flat) * 100) if flat else 0.0
    bte = {d: vs for d, vs in byd.items() if d >= OOS_SPLIT}
    mte, tte, _ = _clustered_t(bte)
    return {"m": m, "t": t, "n": n, "win": win, "mte": mte, "tte": tte}


def main() -> None:
    closes = {c: m for c, m in json.loads(BARS_PATH.read_text()).items() if m}
    topix = {r["Date"]: r["C"] for r in json.loads(TOPIX_PATH.read_text())["records"] if r.get("C")}
    cal = sorted(topix)
    idx = {d: i for i, d in enumerate(cal)}

    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in
                                                ("breakout_all", "shodo_lowvol", "serial", "gc525")}
    vol_pool: list[float] = []
    raw_shodo: list[tuple[str, dict, int]] = []
    for code, m in closes.items():
        dates = [d for d in cal if d in m]
        px = [m[d] for d in dates]
        col = collect(dates, px)
        for key in ("breakout_all", "serial", "gc525"):
            for i, _ in col[key]:
                rec = _mk(dates, px, i, idx, cal, topix, m)
                if rec:
                    buckets[key].append(rec)
        for i, vol in col["shodo"]:
            vol_pool.append(vol)
            raw_shodo.append((code, m, i))
    # 初動のうち低ボラ(下位1/3)だけ採用
    if vol_pool:
        thr = sorted(vol_pool)[len(vol_pool) // 3]
        for code, m, i in raw_shodo:
            dates = [d for d in cal if d in m]
            px = [m[d] for d in dates]
            if realized_vol(px, i - 1) is not None and realized_vol(px, i - 1) <= thr:
                rec = _mk(dates, px, i, idx, cal, topix, m)
                if rec:
                    buckets["shodo_lowvol"].append(rec)

    labels = {"breakout_all": "直近高値ブレイク(全)", "shodo_lowvol": "初動(久々更新×低ボラ土台)",
              "serial": "新高値更新しまくり(連続)", "gc525": "5/25ゴールデンクロス"}
    L = ["# 直近高値ブレイク『初動』 & 5/25GC 検証", "",
         f"大型+中型 {len(closes)}銘柄・close基準。翌寄り買い→H日後引け。α=対TOPIX(β=1)・"
         f"コスト{COST_PCT}%。発火日クラスタt。OOS={OOS_SPLIT}。", ""]
    for key, lab in labels.items():
        L += [f"## {lab}", "", "| H | α net% | t_clust | 勝率% | n | OOS test |", "|--:|--:|--:|--:|--:|---|"]
        for h in HORIZONS:
            s = evaluate(buckets[key], h)
            L.append(f"| {h}日 | {s['m']:+.2f} | {s['t']:+.2f} | {s['win']:.0f} | {s['n']} | "
                     f"{s['mte']:+.2f}(t{s['tte']:+.1f}) |")
        L.append("")
    L += ["## 判定",
          "- 初動が直近高値ブレイク全体・連続更新を α/OOS で上回れば『初動に妙味』仮説を支持。",
          "- どれも α net≈0 やマイナスなら順張りブレイク＝β（12-1で既に取得済）。"]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(REPORT_PATH, "\n".join(L) + "\n")
    for key, lab in labels.items():
        s = evaluate(buckets[key], 10)
        print(f"  {lab:24s} H10 αnet{s['m']:+.2f}% t{s['t']:+.2f} win{s['win']:.0f}% n{s['n']}"
              f" OOS{s['mte']:+.2f}(t{s['tte']:+.1f})")


def _mk(dates: list[str], px: list[float], i: int, idx: dict, cal: list[str],
        topix: dict, m: dict) -> dict[str, Any] | None:
    """シグナル index から 翌寄り基準の各H α を作る。"""
    d0 = dates[i]
    if d0 not in idx or idx[d0] + 1 >= len(cal):
        return None
    gi = idx[d0]
    ed = cal[gi + 1]
    if ed not in m:
        return None
    entry = m[ed]
    a: dict[int, float] = {}
    for h in HORIZONS:
        if gi + 1 + h < len(cal) and cal[gi + 1 + h] in m:
            xd = cal[gi + 1 + h]
            ta, tb = topix.get(ed), topix.get(xd)
            tx = (tb / ta - 1.0) * 100.0 if (ta and tb) else 0.0
            a[h] = (m[xd] / entry - 1.0) * 100.0 - tx - COST_PCT
    return {"date": d0, "a": a} if a else None


if __name__ == "__main__":
    main()

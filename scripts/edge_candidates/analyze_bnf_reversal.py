"""BNF 逆張りスイング（浅い業種別マイナス乖離・大型株）検証。

既検証の『深い -25% 一律』版は raw は再現するが対TOPIX α が +2%(t1.18)で
有意未満＝暴落リバウンドのβと判明。本スクリプトは BNF が実際に大型株で使った
とされる『業種別の浅い乖離帯(5〜15%)＋0%利確』を別物として検証する。

ルール:
  対象 = 大型株(TOPIX Core30/Large70 ≒ scale_band '大型', 99銘柄)
  乖離 = (終値 - 25日単純移動平均) / 25日移動平均 * 100
  エントリ = 乖離 ≤ -(業種別しきい値)  かつ ポジション未保有
  エグジット = 乖離 ≥ 0%  または  保有 HOLD_MAX 営業日経過 (引け約定)
  リターン = エントリ翌営業日寄り→エグジット引け … ではなく
            記事準拠で『シグナル翌日の寄りで買い・利確日の引けで売り』(約定可能)
  α = 銘柄リターン − 同期間 TOPIX リターン(β=1控除)。コストは long 0.20% を1往復で控除。

過剰最適化ガード: エントリ日クラスタ頑健 t / walk-forward OOS(train≤2023, test2024-)。

出力: reports/bnf_reversal.md
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json, atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
CACHE_PATH = REPO_ROOT / "cache" / "bnf_big_bars.json"
REPORT_PATH = REPO_ROOT / "reports" / "bnf_reversal.md"

MA_N = 25            # 移動平均本数(BNF=25日)
HOLD_MAX = 60        # 最大保有(営業日)
LONG_COST_PCT = 0.20  # 片道でなく1トレード往復として控除
OOS_SPLIT = "2024-01-01"

# 業種別エントリしきい値(記事の帯の中央値, %)。未掲載業種は DEFAULT。
SECTOR_THRESH = {
    "医薬品": 7.5,
    "電気機器": 12.5,
    "情報･通信業": 12.5,
    "食料品": 8.5,
    "化学": 8.5,
    "証券･商品先物取引業": 7.5,
}
DEFAULT_THRESH = 10.0


def _deviation_series(closes: list[float]) -> list[float | None]:
    """各 index の 25日SMA 乖離率(%)。MA未確定区間は None。"""
    out: list[float | None] = []
    for i in range(len(closes)):
        if i + 1 < MA_N:
            out.append(None)
            continue
        ma = statistics.fmean(closes[i + 1 - MA_N:i + 1])
        out.append((closes[i] / ma - 1.0) * 100.0 if ma else None)
    return out


def simulate(dates: list[str], opens: list[float], closes: list[float],
             topix: dict[str, float], thresh: float) -> list[dict[str, Any]]:
    """1銘柄のトレード列を返す(シグナル翌日寄り買い→利確日引け売り)。"""
    dev = _deviation_series(closes)
    trades: list[dict[str, Any]] = []
    i = MA_N
    n = len(dates)
    while i < n - 1:
        d = dev[i]
        if d is None or d > -thresh:
            i += 1
            continue
        # シグナル成立 → 翌営業日の寄りで買い
        entry_i = i + 1
        if entry_i >= n:
            break
        entry_d, entry_px = dates[entry_i], opens[entry_i]
        if not entry_px:
            i += 1
            continue
        # エグジット探索: 乖離 ≥ 0 か HOLD_MAX 経過 → その日の引け
        exit_i = None
        for j in range(entry_i, min(entry_i + HOLD_MAX, n)):
            if dev[j] is not None and dev[j] >= 0.0:
                exit_i = j
                break
        if exit_i is None:
            exit_i = min(entry_i + HOLD_MAX, n - 1)
        exit_d, exit_px = dates[exit_i], closes[exit_i]
        if not exit_px:
            i = exit_i + 1
            continue
        ret = (exit_px / entry_px - 1.0) * 100.0
        # TOPIX 同期間
        tin, tout = topix.get(entry_d), topix.get(exit_d)
        tret = (tout / tin - 1.0) * 100.0 if (tin and tout) else 0.0
        alpha = ret - tret - LONG_COST_PCT
        trades.append({"entry": entry_d, "exit": exit_d, "ret": ret, "alpha": alpha,
                       "sig_dev": d, "hold": exit_i - entry_i})
        i = exit_i + 1  # 同一銘柄は1ポジずつ
    return trades


def _clustered_t(trades: list[dict[str, Any]], key: str = "alpha") -> tuple[float, float, int]:
    """エントリ月でクラスタした頑健 t(同月内の相関を吸収)。"""
    if not trades:
        return 0.0, 0.0, 0
    vals = [t[key] for t in trades]
    mean = statistics.fmean(vals)
    # 月クラスタ
    clusters: dict[str, list[float]] = {}
    for t in trades:
        clusters.setdefault(t["entry"][:7], []).append(t[key])
    G = len(clusters)
    n = len(vals)
    # クラスタ頑健分散: sum_g (sum_i (x_i - mean))^2 / n^2  (近似)
    num = sum(sum(x - mean for x in g) ** 2 for g in clusters.values())
    se = math.sqrt(num) / n if n else 0.0
    tval = mean / se if se else 0.0
    return mean, tval, G


def _split(trades: list[dict[str, Any]]) -> tuple[list, list]:
    tr = [t for t in trades if t["entry"] < OOS_SPLIT]
    te = [t for t in trades if t["entry"] >= OOS_SPLIT]
    return tr, te


def fetch_bars(codes: list[str], frm: str, to: str) -> dict[str, list[dict[str, Any]]]:
    """各 code の調整後 日足 {D,O,C} を取得(cache 併用・resume 可)。"""
    from scripts import _jquants
    cache: dict[str, list[dict[str, Any]]] = {}
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text())
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    for i, code in enumerate(codes, 1):
        if code in cache:
            continue
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=code, **{"from": frm, "to": to})
            cache[code] = [{"D": b["Date"], "O": b.get("AdjO") or b.get("O"),
                            "C": b.get("AdjC") or b.get("C")} for b in bars]
        except _jquants.JQuantsError:
            cache[code] = []
        if i % 20 == 0:
            atomic_write_json(CACHE_PATH, cache)
            print(f"  fetched {i}/{len(codes)}")
    atomic_write_json(CACHE_PATH, cache)
    return cache


def build_report(rows: list[dict[str, Any]], all_tr: list[dict[str, Any]]) -> str:
    """全体・OOS・業種別の結果を Markdown レポートにする。"""
    L = ["# BNF 逆張りスイング(浅い業種別乖離・大型株) 検証", "",
         "ルール: 大型株, 25日SMA乖離 ≤ -業種別しきい値(中央値) でシグナル → 翌寄り買い → "
         "乖離≥0% or 60営業日で引け売り。α=対TOPIX(β=1)・コスト long 0.20%/往復控除。", "",
         "## 業種別しきい値(記事の帯の中央値)", ""]
    for s, t in sorted(SECTOR_THRESH.items(), key=lambda x: x[1]):
        L.append(f"- {s}: -{t:.1f}%")
    L.append(f"- その他: -{DEFAULT_THRESH:.1f}%")
    L += ["", "## 全体", ""]
    mean, tval, G = _clustered_t(all_tr)
    rawmean = statistics.fmean(t["ret"] for t in all_tr) if all_tr else 0.0
    win = sum(1 for t in all_tr if t["alpha"] > 0) / len(all_tr) * 100 if all_tr else 0.0
    L.append(f"- n={len(all_tr)} trades / {G}か月クラスタ")
    L.append(f"- raw平均(コスト前)= {rawmean:+.2f}%")
    L.append(f"- **α(対TOPIX, net)= {mean:+.2f}% / t_clust {tval:+.2f} / α勝率 {win:.0f}%**")
    tr, te = _split(all_tr)
    for label, sub in (("train(≤2023)", tr), ("test(2024-)", te)):
        if sub:
            m, t2, g = _clustered_t(sub)
            L.append(f"  - {label}: α {m:+.2f}% / t {t2:+.2f} / n{len(sub)}")
    L += ["", "## 業種別", "", "| 業種 | しきい値 | n | rawEV% | α(net)% | t_clust |",
          "|---|--:|--:|--:|--:|--:|"]
    for r in rows:
        L.append(f"| {r['sector']} | -{r['thresh']:.1f} | {r['n']} | {r['raw']:+.2f} | "
                 f"{r['alpha']:+.2f} | {r['t']:+.2f} |")
    L += ["", "## 判定", "",
          "α net t_clust が +2 を安定して超え、OOS test でも符号正・|t|>1.5 を保てば押し目買いα。",
          "そうでなければ深い-25%版と同じく『暴落リバウンドβ』の浅い焼き直し。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true", help="キャッシュのみ使用(取得しない)")
    ap.add_argument("--out", type=Path, default=REPORT_PATH, help="出力 md")
    args = ap.parse_args()

    recs = json.loads(MASTER_PATH.read_text())["records"]
    big = [r for r in recs if r.get("scale_band") == "大型"]
    sect = {r["Code"]: r.get("S33Nm") or "?" for r in big}
    topix = {r["Date"]: r["C"] for r in json.loads(TOPIX_PATH.read_text())["records"]
             if r.get("C")}
    cal = sorted(topix)
    frm, to = max(cal[0], "2016-06-13"), cal[-1]  # サブスク開始(2016-06-11)以降のみ取得可
    codes = [r["Code"] for r in big]
    bars = json.loads(CACHE_PATH.read_text()) if (args.no_fetch and CACHE_PATH.exists()) \
        else fetch_bars(codes, frm, to)

    all_tr: list[dict[str, Any]] = []
    by_sector: dict[str, list[dict[str, Any]]] = {}
    for code in codes:
        b = bars.get(code) or []
        b = [x for x in b if x.get("O") and x.get("C")]
        if len(b) < MA_N + 5:
            continue
        b.sort(key=lambda x: x["D"])
        dates = [x["D"] for x in b]
        opens = [x["O"] for x in b]
        closes = [x["C"] for x in b]
        thresh = SECTOR_THRESH.get(sect[code], DEFAULT_THRESH)
        tr = simulate(dates, opens, closes, topix, thresh)
        for t in tr:
            t["sector"] = sect[code]
        all_tr.extend(tr)
        by_sector.setdefault(sect[code], []).extend(tr)

    rows = []
    for s, tr in by_sector.items():
        m, tv, _ = _clustered_t(tr)
        rows.append({"sector": s, "thresh": SECTOR_THRESH.get(s, DEFAULT_THRESH),
                     "n": len(tr), "raw": statistics.fmean(t["ret"] for t in tr),
                     "alpha": m, "t": tv})
    rows.sort(key=lambda r: r["t"], reverse=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, build_report(rows, all_tr))
    mean, tval, G = _clustered_t(all_tr)
    print(f"[bnf_reversal] n={len(all_tr)} / α net {mean:+.2f}% / t_clust {tval:+.2f} / {G}clusters → {args.out}")
    tr, te = _split(all_tr)
    for label, sub in (("train", tr), ("test", te)):
        if sub:
            m, t2, _ = _clustered_t(sub)
            print(f"  {label}: α {m:+.2f}% t {t2:+.2f} n{len(sub)}")


if __name__ == "__main__":
    main()

"""モメンタム族の未検証バリアント比較（52週高値 / 絶対トレンド vs 12-1 ベースライン）。

『順張りモメンタムだけが OOS を生き残る』という既知所見を踏まえ、その族の中で
今の月次バスケット(12-1・上位20)を強化できる変種があるかを同じ土俵で比較する。

検証する3戦略（大型+中型 ~493銘柄, 月末リバランス, 等加重, 対TOPIX β=1 控除）:
  S1 12-1        : 過去12か月騰落(直近1か月除外)上位20。現行バスケットの定量版(ベースライン)。
  S2 52週高値    : 現値/52週高値 が高い(高値接近)上位20。George-Hwang。12-1の上位互換候補。
  S3 絶対トレンド: 12か月リターン>0 かつ 200日線上 の銘柄のみ等加重・残りは現金。
                  地合い崩れで自動的に現金化＝モメンタム・クラッシュ耐性を見る。

評価: 月次α系列の t（月=自然なクラスタ, 1obs/月）, walk-forward OOS(train≤2023/test2024-),
      コスト long 0.20%/月(乗り換え往復近似)。S3 は絶対リターン/最大DDも併記。

出力: reports/momentum_variants.md
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any, Callable

from scripts._atomic import atomic_write_json, atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
CACHE_PATH = REPO_ROOT / "cache" / "universe_bars.json"
BIG_CACHE = REPO_ROOT / "cache" / "bnf_big_bars.json"  # 大型99(前回取得, {D,O,C})を流用
REPORT_PATH = REPO_ROOT / "reports" / "momentum_variants.md"

LOOKBACK = 252   # 形成期間(営業日)
SKIP = 21        # 12-1 の直近スキップ
TREND_N = 200    # 200日線
TOP_N = 20
COST_PCT = 0.20  # 月次乗り換えコスト(往復近似)
OOS_SPLIT = "2024-01-01"


def _c4(code: str) -> str:
    code = str(code)
    return code[:-1] if len(code) == 5 and code.endswith("0") else code


def _trend_ok(m: dict[str, float], cal: list[str], idx: int) -> bool:
    hist = [m[cal[k]] for k in range(max(0, idx - TREND_N), idx) if cal[k] in m]
    return len(hist) >= TREND_N * 0.75 and cal[idx] in m and m[cal[idx]] >= statistics.fmean(hist)


def metric_12_1(m: dict[str, float], cal: list[str], idx: int) -> float | None:
    fe, fs = cal[idx - SKIP], cal[idx - LOOKBACK]
    if not (fe in m and fs in m and m[fs]):
        return None
    return m[fe] / m[fs] - 1.0


def metric_52wh(m: dict[str, float], cal: list[str], idx: int) -> float | None:
    window = [m[cal[k]] for k in range(idx - LOOKBACK, idx + 1) if cal[k] in m]
    if len(window) < LOOKBACK * 0.75 or cal[idx] not in m:
        return None
    hi = max(window)
    return m[cal[idx]] / hi if hi else None


def _rebalance_idx(cal: list[str]) -> list[int]:
    """各月の最終取引日の cal index 一覧。"""
    out: list[int] = []
    for i in range(1, len(cal)):
        if cal[i][:7] != cal[i - 1][:7]:
            out.append(i - 1)
    out.append(len(cal) - 1)
    return out


def _fwd_ret(m: dict[str, float], cal: list[str], a: int, b: int) -> float | None:
    da, db = cal[a], cal[b]
    if da in m and db in m and m[da]:
        return m[db] / m[da] - 1.0
    return None


def run_selection(closes: dict[str, dict[str, float]], cal: list[str], rebs: list[int],
                  topix: dict[str, float], metric: Callable, *, use_trend: bool,
                  top_n: int) -> list[dict[str, Any]]:
    """上位 top_n 等加重・月次。各月の (date, port_ret, topix_ret, alpha) を返す。"""
    months: list[dict[str, Any]] = []
    for r in range(len(rebs) - 1):
        idx, nxt = rebs[r], rebs[r + 1]
        if idx < LOOKBACK:
            continue
        scored = []
        for code, m in closes.items():
            if use_trend and not _trend_ok(m, cal, idx):
                continue
            v = metric(m, cal, idx)
            if v is not None:
                scored.append((v, code, m))
        if len(scored) < top_n:
            continue
        scored.sort(key=lambda x: x[0], reverse=True)
        picks = scored[:top_n]
        rets = [_fwd_ret(m, cal, idx, nxt) for _, _, m in picks]
        rets = [x for x in rets if x is not None]
        if not rets:
            continue
        port = statistics.fmean(rets) * 100.0
        tr = _fwd_ret(topix, cal, idx, nxt)
        tret = (tr or 0.0) * 100.0
        alpha = port - tret - COST_PCT
        months.append({"date": cal[nxt][:7], "port": port, "topix": tret, "alpha": alpha})
    return months


def run_absolute(closes: dict[str, dict[str, float]], cal: list[str], rebs: list[int],
                 topix: dict[str, float]) -> list[dict[str, Any]]:
    """絶対トレンド: 12か月>0 & 200日線上 を等加重・該当なし月は現金(0%)。"""
    months: list[dict[str, Any]] = []
    for r in range(len(rebs) - 1):
        idx, nxt = rebs[r], rebs[r + 1]
        if idx < LOOKBACK:
            continue
        rets = []
        for code, m in closes.items():
            fs = cal[idx - LOOKBACK]
            if not (fs in m and cal[idx] in m and m[fs] and m[cal[idx]] / m[fs] - 1.0 > 0):
                continue
            if not _trend_ok(m, cal, idx):
                continue
            fr = _fwd_ret(m, cal, idx, nxt)
            if fr is not None:
                rets.append(fr)
        port = (statistics.fmean(rets) * 100.0) if rets else 0.0  # 該当0=現金
        tr = _fwd_ret(topix, cal, idx, nxt)
        tret = (tr or 0.0) * 100.0
        alpha = port - tret - (COST_PCT if rets else 0.0)
        months.append({"date": cal[nxt][:7], "port": port, "topix": tret,
                       "alpha": alpha, "n_held": len(rets)})
    return months


def _stats(months: list[dict[str, Any]], key: str = "alpha") -> dict[str, float]:
    vals = [x[key] for x in months]
    if not vals:
        return {"mean": 0.0, "t": 0.0, "n": 0, "win": 0.0}
    mean = statistics.fmean(vals)
    sd = statistics.pstdev(vals)
    se = sd / math.sqrt(len(vals)) if len(vals) > 1 else 0.0
    t = mean / se if se else 0.0
    win = sum(1 for v in vals if v > 0) / len(vals) * 100
    return {"mean": mean, "t": t, "n": len(vals), "win": win}


def _max_dd(months: list[dict[str, Any]], key: str = "port") -> float:
    eq, peak, dd = 1.0, 1.0, 0.0
    for x in months:
        eq *= (1.0 + x[key] / 100.0)
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1.0)
    return dd * 100.0


def _split(months: list[dict[str, Any]]) -> tuple[list, list]:
    return ([x for x in months if x["date"] < OOS_SPLIT[:7]],
            [x for x in months if x["date"] >= OOS_SPLIT[:7]])


def fetch_closes(codes: list[str], frm: str, to: str) -> dict[str, dict[str, float]]:
    from scripts import _jquants
    cache: dict[str, dict[str, float]] = {}
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text())
    # 大型キャッシュ({D,O,C})から closes を流用
    if BIG_CACHE.exists():
        big = json.loads(BIG_CACHE.read_text())
        for code, bars in big.items():
            if code not in cache:
                cache[code] = {b["D"]: b["C"] for b in bars if b.get("C")}
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    for i, code in enumerate(codes, 1):
        if code in cache:
            continue
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=code, **{"from": frm, "to": to})
            cache[code] = {b["Date"]: (b.get("AdjC") or b.get("C")) for b in bars if (b.get("AdjC") or b.get("C"))}
        except _jquants.JQuantsError:
            cache[code] = {}
        if i % 25 == 0:
            atomic_write_json(CACHE_PATH, cache)
            print(f"  fetched {i}/{len(codes)}")
    atomic_write_json(CACHE_PATH, cache)
    return cache


def _fmt(label: str, months: list[dict[str, Any]], with_dd: bool = False) -> list[str]:
    s = _stats(months)
    tr, te = _split(months)
    st, se = _stats(tr), _stats(te)
    line = (f"- **{label}**: α net {s['mean']:+.2f}%/月 / t {s['t']:+.2f} / 勝月{s['win']:.0f}% / n{s['n']}か月"
            f"  ｜ train {st['mean']:+.2f}%(t{st['t']:+.2f}) → **test {se['mean']:+.2f}%(t{se['t']:+.2f})**")
    if with_dd:
        line += f"  ｜ 絶対リターン平均{_stats(months, 'port')['mean']:+.2f}%/月・最大DD{_max_dd(months):.0f}%"
    return [line]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true", help="キャッシュのみ使用")
    ap.add_argument("--top", type=int, default=TOP_N, help="選別戦略の上位N(既定20)")
    ap.add_argument("--out", type=Path, default=REPORT_PATH, help="出力 md")
    args = ap.parse_args()

    recs = json.loads(MASTER_PATH.read_text())["records"]
    uni = [r for r in recs if r.get("scale_band") in ("大型", "中型")]
    codes = [r["Code"] for r in uni]
    topix = {r["Date"]: r["C"] for r in json.loads(TOPIX_PATH.read_text())["records"] if r.get("C")}
    cal = sorted(topix)
    frm = max(cal[0], "2016-06-13")
    closes = json.loads(CACHE_PATH.read_text()) if (args.no_fetch and CACHE_PATH.exists()) \
        else fetch_closes(codes, frm, cal[-1])
    closes = {c: m for c, m in closes.items() if m}
    rebs = _rebalance_idx(cal)

    s1 = run_selection(closes, cal, rebs, topix, metric_12_1, use_trend=True, top_n=args.top)
    s2 = run_selection(closes, cal, rebs, topix, metric_52wh, use_trend=False, top_n=args.top)
    s3 = run_absolute(closes, cal, rebs, topix)

    L = ["# モメンタム族バリアント比較（52週高値 / 絶対トレンド vs 12-1）", "",
         f"大型+中型 {len(closes)}銘柄 / 月末リバランス / 等加重 / 上位{args.top} / "
         f"対TOPIX α(β=1) / コスト{COST_PCT}%・月 / OOS分割 {OOS_SPLIT[:7]}。", "",
         "α net t が +2 安定かつ **OOS test で符号正・t>1.5** を保てば本物。12-1 を上回れば採用検討。", ""]
    L += _fmt("S1 12-1 (ベースライン)", s1)
    L += _fmt("S2 52週高値接近 上位20", s2)
    L += _fmt("S3 絶対トレンド(12mo>0&200線上, 残現金)", s3, with_dd=True)
    L += ["", "## 判定",
          "- S2/S3 が S1 を α・OOS とも上回れば乗り換え候補。劣れば現行(12-1)維持。",
          "- 12-1 のクラッシュ耐性が課題なら S3 の最大DD/絶対リターンが代替材料。"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, "\n".join(L) + "\n")
    print(f"[mom_variants] 銘柄{len(closes)} / 月数~{len(s1)} → {args.out}")
    for lab, mm in (("S1 12-1", s1), ("S2 52wh", s2), ("S3 absTrend", s3)):
        st = _stats(mm)
        tr, te = _split(mm)
        print(f"  {lab:12s} α{st['mean']:+.2f}%/t{st['t']:+.2f}/win{st['win']:.0f}%/n{st['n']}"
              f"  train{_stats(tr)['mean']:+.2f} test{_stats(te)['mean']:+.2f}(t{_stats(te)['t']:+.2f})")


if __name__ == "__main__":
    main()

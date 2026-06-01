"""RSI(14) 売られすぎ → 平均回帰のロング戦略を universe 横断で検証。

ロジック:
  - 各銘柄日次 close から Wilder smoothing で RSI(14) を算出。
  - エントリ条件: RSI(t-1) > entry かつ RSI(t) <= entry (閾値クロス時に翌日寄り買い)。
  - エグジット条件: RSI(s) >= exit に達した日の翌日寄り売り。
  - 最大保有: max_hold 営業日 (時間切れは翌日寄り売り)。
  - リターン: (exit_open / entry_open - 1) * 100 - cost。

3 エントリ閾値 × 3 エグジット閾値 = 9 パターンを一括検証。
日付クラスタ頑健 t (entry_date) + FDR + walk-forward OOS を適用。
出力: reports/rsi_mean_reversion.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from analyzers.stats import benjamini_hochberg, clustered_se, t_to_p
from scripts._atomic import atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BARS_PATH = REPO_ROOT / "data" / "edge_candidates" / "daily_bars_universe.json"
OUT_REPORT = REPO_ROOT / "reports" / "rsi_mean_reversion.md"
OUT_TRADES = REPO_ROOT / "data" / "edge_candidates" / "rsi_trades.json"

RSI_PERIOD = 14
ENTRY_THRESHOLDS = (20.0, 25.0, 30.0)
EXIT_THRESHOLDS = (50.0, 60.0, 70.0)
LONG_COST = 0.20    # ロング往復%
MAX_HOLD = 60
PASS_EV = 0.5
PASS_T = 2.0
MIN_N = 30


def wilder_rsi(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """closes 系列 (時系列順) に Wilder の RSI(period) を計算。先頭 period 個は None。"""
    n = len(closes)
    rsi: list[float | None] = [None] * n
    if n <= period:
        return rsi
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    avg_g, avg_l = gains / period, losses / period
    rsi[period] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    for i in range(period + 1, n):
        ch = closes[i] - closes[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
        rsi[i] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return rsi


def _adj(bar: dict[str, Any], key: str) -> float | None:
    """調整後優先で価格を返す。/equities/bars/daily の短名 (AdjC/C, AdjO/O) に対応。"""
    short = {"Close": ("AdjC", "C"), "Open": ("AdjO", "O"),
             "High": ("AdjH", "H"), "Low": ("AdjL", "L")}.get(key, (key,))
    for k in short:
        v = bar.get(k)
        if v is not None:
            return v
    return None


def simulate_code(bars: list[dict[str, Any]], entry: float, exit_: float,
                  *, max_hold: int = MAX_HOLD) -> list[dict[str, Any]]:
    """1銘柄について entry/exit 閾値で発注をシミュレートし trades を返す。

    - エントリ判定: RSI[t-1] > entry かつ RSI[t] <= entry の足の翌日 (t+1) 寄りで買い
    - エグジット判定: RSI[s] >= exit に達した足の翌日 (s+1) 寄りで売り
    - 最大保有: 仕掛けから max_hold 営業日経過したら翌日寄り強制売却
    - 仕掛け中は新規エントリしない (オーバーラップ無し)
    """
    closes = [_adj(b, "Close") for b in bars]
    closes_f = [c for c in closes if c is not None]
    if len(closes_f) < RSI_PERIOD + 2:
        return []
    # 抜けがある銘柄はスキップ (Close が None)
    if any(c is None for c in closes):
        return []
    rsi = wilder_rsi(closes)
    trades: list[dict[str, Any]] = []
    in_pos = False
    entry_idx = -1
    entry_o: float | None = None
    n = len(bars)
    for t in range(RSI_PERIOD + 1, n - 1):
        if not in_pos:
            if rsi[t - 1] is None or rsi[t] is None:
                continue
            if rsi[t - 1] > entry and rsi[t] <= entry:
                # 翌日寄りでエントリ
                o = _adj(bars[t + 1], "Open")
                if o is None:
                    continue
                in_pos = True
                entry_idx = t + 1
                entry_o = o
                entry_date = bars[t + 1].get("Date")
                signal_rsi = rsi[t]
        else:
            held = t - entry_idx
            exit_signal = rsi[t] is not None and rsi[t] >= exit_
            timeout = held >= max_hold
            if exit_signal or timeout:
                if t + 1 >= n:
                    break  # 翌日が無いので未決済 trade は捨てる
                ox = _adj(bars[t + 1], "Open")
                if ox is None or entry_o is None:
                    in_pos = False
                    continue
                ret = (ox / entry_o - 1.0) * 100.0
                trades.append({
                    "entry_date": entry_date, "entry_open": entry_o,
                    "exit_date": bars[t + 1].get("Date"), "exit_open": ox,
                    "hold_days": t + 1 - entry_idx, "ret": ret,
                    "rsi_entry": signal_rsi, "rsi_exit": rsi[t],
                    "reason": "timeout" if timeout and not exit_signal else "signal",
                })
                in_pos = False
                entry_o = None
    return trades


def aggregate_pattern(trades: list[dict[str, Any]], *, cost: float, split: float = 0.7) -> dict[str, Any]:
    """trades 全体の net EV / t_clust / 勝率 / OOS を算出。クラスタは entry_date。"""
    n = len(trades)
    if n == 0:
        return {"n": 0}
    nets = [t["ret"] - cost for t in trades]
    mean = statistics.fmean(nets)
    dates = [t["entry_date"] for t in trades]
    cse = clustered_se(nets, dates)
    tval = mean / cse if cse else 0.0
    win = sum(1 for v in nets if v > 0) * 100.0 / n
    so = sorted(zip(dates, nets), key=lambda x: x[0])
    test = so[int(n * split):]
    oos = statistics.fmean([v for _, v in test]) if test else None
    hold_med = statistics.median(t["hold_days"] for t in trades)
    return {"n": n, "net_ev": mean, "t_clust": tval,
            "sd": statistics.pstdev(nets) if n > 1 else 0.0,
            "win": win, "p": t_to_p(tval), "oos": oos, "hold_med": hold_med}


def run_grid(bars_map: dict[str, list[dict[str, Any]]], *,
             entries: tuple[float, ...] = ENTRY_THRESHOLDS,
             exits: tuple[float, ...] = EXIT_THRESHOLDS,
             cost: float = LONG_COST) -> list[dict[str, Any]]:
    """entry × exit グリッドでシミュレートし、パターン別集計を返す。"""
    results: list[dict[str, Any]] = []
    trades_by_pat: dict[tuple[float, float], list[dict[str, Any]]] = defaultdict(list)
    for code, bars in bars_map.items():
        for e in entries:
            for x in exits:
                if x <= e:
                    continue
                for t in simulate_code(bars, e, x):
                    t["code"] = code
                    trades_by_pat[(e, x)].append(t)
    for (e, x), trs in trades_by_pat.items():
        agg = aggregate_pattern(trs, cost=cost)
        agg["entry"], agg["exit"] = e, x
        results.append(agg)
    if results:
        for r, f in zip(results, benjamini_hochberg([r["p"] for r in results if r["n"]], 0.05)):
            r["fdr_significant"] = f
    results.sort(key=lambda r: (-r.get("t_clust", 0.0), r["entry"], r["exit"]))
    return results


def write_report(results: list[dict[str, Any]], *, out_path: Path = OUT_REPORT) -> Path:
    """検証結果を Markdown レポート化。"""
    import datetime
    lines = [f"# RSI(14) 売られすぎ平均回帰 検証結果 ({datetime.date.today()})", "",
             "## 検証設定",
             f"- ロング往復コスト: {LONG_COST:.2f}%",
             f"- 最大保有日数: {MAX_HOLD}営業日",
             f"- エントリ: RSI[t-1]>閾値 かつ RSI[t]≤閾値 で翌日寄り買い",
             f"- エグジット: RSI≥閾値で翌日寄り売り、または最大保有切れ", "",
             "## パターン別結果 (t_clust 降順)", "",
             "| entry | exit | n | net EV | t_clust | 勝率 | 中央保有 | p | FDR | OOS |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if not r.get("n"):
            continue
        mark = "★" if r.get("fdr_significant") else ""
        oos = r["oos"] if r["oos"] is not None else 0.0
        lines.append(f"| {r['entry']:.0f} | {r['exit']:.0f} | {r['n']} | "
                     f"{r['net_ev']:+.2f}% | {r['t_clust']:+.2f} | {r['win']:.0f}% | "
                     f"{r['hold_med']:.0f}d | {r['p']:.3f} | {mark} | {oos:+.2f}% |")
    lines.append("")
    lines.append("## 判定")
    pas = [r for r in results if r.get("n", 0) >= MIN_N and r["net_ev"] > PASS_EV
           and r["t_clust"] > PASS_T and r.get("fdr_significant")
           and r["oos"] is not None and r["oos"] > 0]
    if pas:
        lines.append("以下が通過 (実弾投入可水準):")
        for r in pas:
            lines.append(f"- entry≤{r['entry']:.0f}/exit≥{r['exit']:.0f}: "
                         f"net{r['net_ev']:+.2f}%/t{r['t_clust']:+.2f}/n{r['n']}/OOS{r['oos']:+.2f}%")
    else:
        lines.append("通過パターンなし。")
    atomic_write_text(out_path, "\n".join(lines))
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bars", type=Path, default=BARS_PATH)
    ap.add_argument("--out-report", type=Path, default=OUT_REPORT)
    ap.add_argument("--out-trades", type=Path, default=OUT_TRADES)
    args = ap.parse_args()
    bars_map = json.loads(args.bars.read_text())["bars"]
    print(f"[rsi] universe {len(bars_map)}銘柄")
    results = run_grid(bars_map)
    write_report(results, out_path=args.out_report)
    print(f"wrote {args.out_report}")


if __name__ == "__main__":
    main()

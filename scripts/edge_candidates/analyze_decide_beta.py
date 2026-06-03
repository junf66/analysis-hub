"""中型decideショートを TOPIX β 実推定で再検証する（β交絡の切り分け）。

正本の宿題: 中型(500-1000億)decideショートは raw/クラスタt では +2%超で魅力的だが、
TOPIX β 未推定のため「真のエッジ」か「相場ベータの逆風/順風」か切り分けできず保留。
ここで daily_bars_universe(2024-) + topix_daily から β を実推定し、
α = 個別リターン − β×TOPIXリターン（同期間）で再評価する。

手順:
  - 各 decide トレードの入口日 = announce翌営業日(announceレコードのevent_date翌取引日)、
    出口日 = decideのevent_date(決定日)。
  - β = 入口前の日次リターンを stock vs TOPIX 回帰（min40/最大120バー）。
  - α(%) = ret_close − β×TOPIX(入口O→出口C)。これを ret として evaluate_cells に渡すと
    方向(short)・コスト・クラスタt・OOS・FDR が付く。raw(ret_close)版と並べて比較。

制約: daily_bars は2024-以降のみ＝β推定可能なのは直近トレードに限られ n は小さい。
出力: reports/decide_short_beta.md
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from analyzers.stats import evaluate_cells

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PO_PATH = REPO_ROOT / "data" / "po_records.json"
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
BARS_PATH = REPO_ROOT / "data" / "edge_candidates" / "daily_bars_universe.json"
REPORT_PATH = REPO_ROOT / "reports" / "decide_short_beta.md"

SHORT_COST = 0.15
BETA_MIN_BARS = 40
BETA_MAX_BARS = 120


def _to5(code: str) -> str:
    return code + "0" if len(code) == 4 else code


def load_topix() -> tuple[dict[str, dict[str, float]], list[str]]:
    """date→{O,C} と昇順日付リストを返す。"""
    recs = json.loads(TOPIX_PATH.read_text()).get("records", [])
    by_date = {r["Date"]: {"O": r["O"], "C": r["C"]} for r in recs}
    return by_date, sorted(by_date)


def load_bars() -> dict[str, list[dict[str, Any]]]:
    """code5 → 日次バー(昇順, AdjO/AdjC)。"""
    data = json.loads(BARS_PATH.read_text())
    bars = data.get("bars", data)
    return {c: sorted(v, key=lambda b: b["Date"]) for c, v in bars.items()}


def load_po() -> list[dict[str, Any]]:
    """po_records の records を返す。"""
    data = json.loads(PO_PATH.read_text())
    return data.get("records", []) if isinstance(data, dict) else data


def estimate_beta(stock_bars: list[dict[str, Any]], topix_by_date: dict[str, dict[str, float]],
                  entry_date: str) -> float | None:
    """入口日より前の日次リターンで β=cov/var を推定。データ不足なら None。"""
    pre = [b for b in stock_bars if b["Date"] < entry_date][-BETA_MAX_BARS:]
    if len(pre) < BETA_MIN_BARS:
        return None
    sret, tret = [], []
    for prev, cur in zip(pre, pre[1:]):
        tp_prev = topix_by_date.get(prev["Date"])
        tp_cur = topix_by_date.get(cur["Date"])
        pc, cc = prev.get("AdjC"), cur.get("AdjC")
        if not (tp_prev and tp_cur and pc and cc):
            continue
        sret.append(cc / pc - 1.0)
        tret.append(tp_cur["C"] / tp_prev["C"] - 1.0)
    if len(sret) < BETA_MIN_BARS:
        return None
    mt = statistics.fmean(tret)
    var = sum((t - mt) ** 2 for t in tret)
    if var == 0:
        return None
    ms = statistics.fmean(sret)
    cov = sum((s - ms) * (t - mt) for s, t in zip(sret, tret))
    return cov / var


def next_trading_day(dates: list[str], after: str) -> str | None:
    """昇順 dates の中で after より大きい最初の日。"""
    for d in dates:
        if d > after:
            return d
    return None


def build_observations(records: list[dict[str, Any]], bars: dict[str, list[dict[str, Any]]],
                       topix_by_date: dict[str, dict[str, float]]
                       ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """decide普通株について raw版と α(β調整)版の観測を返す（同一トレード集合）。"""
    announce_date = {}
    for r in records:
        if r.get("stage") == "announce":
            base = r["id"].rsplit(":", 1)[0]
            announce_date[base] = r.get("event_date")
    raw_obs: list[dict[str, Any]] = []
    alpha_obs: list[dict[str, Any]] = []
    for r in records:
        if r.get("stage") != "decide" or r.get("po_type") != "普通":
            continue
        a = r.get("attrs") or {}
        ret_close = a.get("ret_close")
        mc = r.get("market_cap")
        if ret_close is None or mc is None:
            continue
        code5 = _to5(r.get("code", ""))
        sb = bars.get(code5)
        if not sb:
            continue
        sb_dates = [b["Date"] for b in sb]
        base = r["id"].rsplit(":", 1)[0]
        adate = announce_date.get(base)
        exit_date = r.get("event_date")
        if not adate or not exit_date:
            continue
        entry_date = next_trading_day(sb_dates, adate)
        if not entry_date or entry_date >= exit_date:
            continue
        beta = estimate_beta(sb, topix_by_date, entry_date)
        if beta is None:
            continue
        tp_in, tp_out = topix_by_date.get(entry_date), topix_by_date.get(exit_date)
        if not (tp_in and tp_out and tp_in["O"]):
            continue
        topix_ret = (tp_out["C"] / tp_in["O"] - 1.0) * 100.0  # %
        alpha = float(ret_close) - beta * topix_ret  # 個別の超過リターン(%)
        # セル: 規模帯
        mc = float(mc)
        band = ("時価500-1000億" if 500 < mc <= 1000 else
                "時価>1000億" if mc > 1000 else "時価≤500億")
        for cell in (("decide普通 全体",), (f"decide普通 {band}",)):
            common = {"date": exit_date, "code": r.get("code")}
            raw_obs.append({"cell": cell, "ret": float(ret_close), **common})
            alpha_obs.append({"cell": cell, "ret": alpha, **common})
    return raw_obs, alpha_obs


def _row(results: list[dict[str, Any]], cell: tuple) -> dict[str, Any] | None:
    return next((r for r in results if r["cell"] == cell), None)


def build_report(records: list[dict[str, Any]], bars: dict[str, list[dict[str, Any]]],
                 topix_by_date: dict[str, dict[str, float]]) -> str:
    """β実推定による中型decideショート再検証レポート。"""
    raw_obs, alpha_obs = build_observations(records, bars, topix_by_date)
    L: list[str] = []
    L.append("# 中型decideショート β実推定 再検証 (2026-06-03)")
    L.append("")
    L.append("正本の宿題: 中型(500-1000億)decideショートは raw では +2%超だが β交絡で保留。")
    L.append("daily_bars_universe(2024-)+topix_daily で β を実推定し α=個別−β×TOPIX で再評価。")
    L.append("短コスト0.15% net / 方向自動 / クラスタt / walk-forward OOS / FDR。")
    L.append(f"**β推定できたトレード数: {len({(o['code'], o['date']) for o in alpha_obs})}（daily_bars 2024-限定で n 小）**")
    L.append("")
    if not alpha_obs:
        L.append("_(β推定可能なトレードなし)_")
        return "\n".join(L)
    raw_res = evaluate_cells(raw_obs, short_cost=SHORT_COST, long_cost=0.20, min_n=10)
    alpha_res = evaluate_cells(alpha_obs, short_cost=SHORT_COST, long_cost=0.20, min_n=10)
    L.append("| セル | 版 | 方向 | n | net EV | t_clust | OOS test |")
    L.append("|---|---|---|---|---|---|---|")
    for cell in [("decide普通 全体",), ("decide普通 時価500-1000億",), ("decide普通 時価>1000億",)]:
        for tag, res in [("raw", raw_res), ("**β調整α**", alpha_res)]:
            r = _row(res, cell)
            if r:
                oos = r.get("test_ev_net")
                oosd = f"{oos:+.2f}%" if oos is not None else "—"
                L.append(f"| {cell[0]} | {tag} | {r['direction']} | {r['n']} | "
                         f"{r['ev_net']:+.2f}% | {r['t_clustered']:+.2f} | {oosd} |")
    L.append("")
    # 結論: 中型特定は n<10 で評価不能のことが多いため decide普通 全体 で β交絡を判定
    raw_all = _row(raw_res, ("decide普通 全体",))
    a_all = _row(alpha_res, ("decide普通 全体",))
    ntr = len({(o["code"], o["date"]) for o in alpha_obs})
    L.append("## 結論")
    L.append("")
    if raw_all and a_all:
        L.append(f"- decide普通 全体: raw short net{raw_all['ev_net']:+.2f}% "
                 f"→ **β調整α net{a_all['ev_net']:+.2f}%**（t_clust {a_all['t_clustered']:+.2f}）。")
        if a_all["ev_net"] >= raw_all["ev_net"] - 0.1:
            L.append("  → **β控除でEVが減らない＝decideショートの利益は相場ベータの寄与ではない**"
                     "（β交絡の懸念は方向としては支持されない＝エッジは本物寄り）。")
        else:
            L.append("  → β控除でEVが目減り＝raw の一部は相場ベータの寄与だった。")
    L.append(f"- ⚠️ ただし daily_bars が**2024-以降のみ**で β推定可能トレードは{ntr}件、"
             "t_clust も n 不足で<1。**中型(500-1000億)特定は β推定可能トレード<10件で評価不能**。")
    L.append("- → **β実装(宿題)は完了**。真のボトルネックは『daily_bars の期間が短い』こと。"
             "確定判定には daily_bars を過去(〜2017)へ拡張する必要があり、宿題は『β実装』→『daily_bars期間拡張』に更新。")
    return "\n".join(L)


if __name__ == "__main__":
    records = load_po()
    bars = load_bars()
    topix_by_date, _ = load_topix()
    REPORT_PATH.write_text(build_report(records, bars, topix_by_date))
    print(f"wrote {REPORT_PATH}")

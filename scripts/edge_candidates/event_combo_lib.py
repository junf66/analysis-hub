"""同時開示コンボ・エッジ (好悪混在×自社株買い / 業務提携×CB等) の共通検証エンジン。

タイトル基準で TDnet 開示 (cache/disclosures/tdnet_all.json, 2021-06〜) からイベントを抽出し、
反応日 (開示翌取引日) 寄り起点の出口グリッド (d0=寄→引 / +1/+3/+5日引) リターンを
event_bars (調整後O/C) から付与。PIT ユニバース (master_history) で規模/市場/信用区分を
イベント時点で確定。多重検定 BH-FDR ＋ 日付クラスタ頑健 t ＋ 非重複の正直 t ＋ walk-forward OOS。

方向別コスト: long 0.20% / short 0.15% (確定エッジ群と同じ前提)。
+N日はベータ (TOPIX β=1) 控除 α で市場効果を除く。d0 (寄→引) は intraday=raw。

純関数中心 (テスト可)。fetch なし=全データはローカルキャッシュ (offline)。
"""
from __future__ import annotations

import datetime as _dt
import json
import statistics
from pathlib import Path
from typing import Any, Callable

from analyzers.stats import benjamini_hochberg, clustered_se, t_to_p
from scripts.edge_candidates import topix_adjust

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TDNET_PATH = REPO_ROOT / "cache" / "disclosures" / "tdnet_all.json"
EVENT_BARS_PATH = REPO_ROOT / "cache" / "event_bars.json"
MASTER_HIST_PATH = REPO_ROOT / "cache" / "master_history.json"
FINS_PATH = REPO_ROOT / "data" / "edge_candidates" / "fins_summary.json"

LONG_COST = 0.20
SHORT_COST = 0.15
MIN_N = 30
DAYS = [0, 1, 3, 5]
GU_THRESH = 3.0          # 反応日寄りギャップ GU/GD 閾値 %


# ---------- データ読み込み ----------

def load_tdnet_rows() -> list[dict[str, Any]]:
    """tdnet_all (yanoshin ミラー) を {code, date, time, title, market} の行リストで返す。"""
    bd = json.loads(TDNET_PATH.read_text())["by_date"]
    out = []
    for _d, lst in bd.items():
        for r in lst:
            pub = r.get("pubdate") or ""
            out.append({"code": r.get("code"), "date": pub[:10], "time": pub[11:16],
                        "title": r.get("title") or "", "market": r.get("markets") or ""})
    return out


def load_event_bars() -> dict[str, dict[str, list]]:
    """event_bars: {code4: {date: [AdjO, AdjC]}}。"""
    return json.loads(EVENT_BARS_PATH.read_text())


def load_master_history() -> dict[str, dict[str, dict]]:
    """master_history: {snapshot_date: {code5: {ScaleCat,S17Nm,MrgnNm,MktNm,scale_band}}}。"""
    return json.loads(MASTER_HIST_PATH.read_text())


def load_fins_by_code() -> dict[str, list[dict]]:
    """fins_summary: {code5: [決算行...]}。"""
    return json.loads(FINS_PATH.read_text())["by_code"]


# ---------- コード正規化 / PIT ----------

def code4(code: str) -> str:
    """5桁(末尾0) → 4桁。既に4桁ならそのまま。"""
    if len(code) == 5 and code.endswith("0"):
        return code[:-1]
    return code


def code5(code: str) -> str:
    """4桁 → 5桁(末尾0付与)。既に5桁ならそのまま。"""
    return code + "0" if len(code) == 4 else code


def pit_attrs(master_hist: dict, code: str, event_date: str) -> dict[str, Any]:
    """イベント時点で有効な最新スナップショットから規模/市場/信用区分を返す (PIT)。"""
    c5 = code5(code)
    snaps = sorted(master_hist.keys())
    chosen = snaps[0]
    for s in snaps:
        if s <= event_date:
            chosen = s
        else:
            break
    rec = master_hist.get(chosen, {}).get(c5, {})
    return {"scale_band": rec.get("scale_band"), "mkt": rec.get("MktNm"),
            "mrgn": rec.get("MrgnNm"), "s17": rec.get("S17Nm"), "scale_cat": rec.get("ScaleCat")}


# ---------- リターン付与 (event_bars) ----------

def returns_from_event_bars(bars: dict[str, list], event_date: str,
                            days: list[int] = DAYS) -> dict[str, Any]:
    """{date:[O,C]} から反応日(=event_date翌取引日)寄り起点の d{n}_ret・gap を計算。

    entry = event_date より後の最初の取引日の寄り (大引け後開示でも約定可能)。
    gap = entry_open / prev_close - 1 (寄り型 GU/GD 判定用)。
    d0 = 反応日寄→反応日引、d{n} = +n営業日の引け。
    """
    dates = sorted(bars.keys())
    after = [d for d in dates if d > event_date]
    if not after:
        return {"price_error": "no entry bar"}
    entry_date = after[0]
    ei = dates.index(entry_date)
    o = bars[entry_date][0]
    if not o:
        return {"price_error": "no entry open"}
    out: dict[str, Any] = {"entry_date": entry_date, "entry_open": o}
    if ei > 0:
        pc = bars[dates[ei - 1]][1]
        if pc:
            out["gap"] = (o / pc - 1.0) * 100.0
    for n in days:
        j = ei + n
        if j < len(dates):
            c = bars[dates[j]][1]
            if c:
                out[f"d{n}_ret"] = (c / o - 1.0) * 100.0
    return out


def gap_bucket(gap: float | None) -> str:
    """反応日寄りギャップを GU/フラット/GD に層化。"""
    if gap is None:
        return "不明"
    if gap > GU_THRESH:
        return "GU(>+3%)"
    if gap < -GU_THRESH:
        return "GD(<-3%)"
    return "フラット"


def enrich_returns(events: list[dict], ebars: dict, days: list[int] = DAYS) -> int:
    """events に event_bars からリターン/gap を付与し、+N日 TOPIX 超過 α も付ける。付与数を返す。"""
    ok = 0
    for e in events:
        bars = ebars.get(code4(e["code"]))
        a = e.setdefault("attrs", {})
        if not bars:
            a["price_error"] = "no bars"
            continue
        a.update(returns_from_event_bars(bars, e["event_date"], days))
        if a.get("d0_ret") is not None:
            ok += 1
    topix_adjust.enrich_with_alpha(events, [d for d in days if d > 0])
    return ok


# ---------- 統計 (方向別) ----------

def _nonoverlap_keep(obs: list[tuple[str, str, float]], hold_days: int) -> list[float]:
    """同一銘柄で hold_days 以内に重なるイベントを間引き、非重複の値リストを返す (正直 t 用)。

    obs: (date, code, value)。code ごとに日付昇順で貪欲に hold_days 以上空けて採用。
    """
    by_code: dict[str, list[tuple[str, float]]] = {}
    for d, c, v in obs:
        by_code.setdefault(c, []).append((d, v))
    kept: list[float] = []
    for c, lst in by_code.items():
        lst.sort()
        last: _dt.date | None = None
        for d, v in lst:
            dd = _dt.date.fromisoformat(d)
            if last is None or (dd - last).days > hold_days:
                kept.append(v)
                last = dd
    return kept


def directional_stats(records: list[dict], metric: str, direction: str, cost: float,
                      *, hold_days: int = 1, split: float = 0.7) -> dict[str, Any] | None:
    """1出口の方向別 net EV / クラスタ t / 正直(非重複) t / 勝率 / OOS を計算。

    direction: 'long' (pnl=ret-cost) / 'short' (pnl=-ret-cost)。
    hold_days: 非重複 t 用の保有期間 (同一銘柄の重複イベント間引き)。
    """
    obs: list[tuple[str, str, float]] = []
    for r in records:
        a = r.get("attrs") or {}
        v = a.get(metric)
        d = r.get("event_date")
        c = r.get("code")
        if v is not None and d and c:
            pnl = (v - cost) if direction == "long" else (-v - cost)
            obs.append((d, c, pnl))
    n = len(obs)
    if n == 0:
        return None
    vals = [v for _, _, v in obs]
    mean = statistics.fmean(vals)
    cse = clustered_se(vals, [d for d, _, _ in obs])
    t_clust = mean / cse if cse else 0.0
    win = sum(1 for v in vals if v > 0) * 100.0 / n
    # 非重複の正直 t
    keep = _nonoverlap_keep(obs, hold_days)
    if len(keep) > 1 and statistics.pstdev(keep):
        honest_t = statistics.fmean(keep) / (statistics.stdev(keep) / (len(keep) ** 0.5))
    else:
        honest_t = 0.0
    so = sorted(obs, key=lambda x: x[0])
    test = so[int(n * split):]
    oos = statistics.fmean([v for _, _, v in test]) if test else None
    return {"n": n, "net_ev": mean, "t_clust": t_clust, "honest_t": honest_t,
            "n_indep": len(keep), "win": win, "p": t_to_p(t_clust), "oos": oos,
            "sd": statistics.pstdev(vals) if n > 1 else 0.0}


def apply_fdr(cells: list[dict], alpha: float = 0.05, min_n: int = MIN_N) -> None:
    """n>=min_n のセル群に BH-FDR を適用し fdr_significant を立てる (in-place)。"""
    elig = [c for c in cells if c and c["n"] >= min_n]
    for c in cells:
        if c:
            c["fdr_significant"] = False
    if elig:
        for c, f in zip(elig, benjamini_hochberg([c["p"] for c in elig], alpha)):
            c["fdr_significant"] = f


# 出口 metric: d0 は raw、+N日は α
EXITS_LONG = [("反応日(寄→引)", "d0_ret", 1), ("+1日α", "alpha_d1_ret", 1),
              ("+3日α", "alpha_d3_ret", 3), ("+5日α", "alpha_d5_ret", 5)]
EXITS_SHORT = EXITS_LONG  # 出口グリッドは同形 (方向のみ違う)


def verdict(s: dict, ev_pass: float = 0.5, t_pass: float = 2.0) -> str:
    """3段ガード総合判定。★=net>ev_pass & t_clust>t_pass & 正直t>t_pass & FDR生存 & OOS>0。"""
    if s["n"] < MIN_N:
        return "—(n<30)"
    if (s["net_ev"] > ev_pass and s["t_clust"] > t_pass and s["honest_t"] > t_pass
            and s.get("fdr_significant") and (s["oos"] or 0) > 0):
        return "★通過"
    if s["net_ev"] > 0 and s["t_clust"] > t_pass:
        return "△(FDR/正直t前のみ)"
    if s["net_ev"] <= 0 or s["t_clust"] < -1:
        return "✕"
    return "—"

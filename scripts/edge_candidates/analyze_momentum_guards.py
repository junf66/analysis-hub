"""モメンタム・クラッシュのガード強化（露出レベルの対策を同一土俵で比較）。

既存の analyze_volscaled_momentum は**選別レベル**(どの銘柄を選ぶか=高ボラ除外/再ランク)で
全滅(αを殺しDDも縮まない)。本スクリプトは未検証の**露出レベル**ガードを検証する:

  G0 ベース      : 12-1 top20 等加重・フル露出(ロングオンリー)。
  G1 ナイフ除外  : 選別時に「直近20日 −15%以下」の銘柄を外し次点で補充(正本提案)。
  G2 ボラ標的    : 銘柄選別はベースのまま、**組合せ全体の建玉を直近ボラの逆数でスケール**
                   (Barroso-Santa-Clara。露出=min(1, 目標ボラ/バスケット実現ボラ)。残りは現金)。
                   クラッシュは高ボラ regime ゆえ事前に自動減量する。複数の目標ボラで感度。
  G3 標的+ナイフ : G2 と G1 の併用。

ロングオンリー(S株現物・レバ無し)ゆえ β=1 α でなく**絶対リターン CAGR / 最大DD / Calmar /
最悪月 / 平均露出**で評価(露出を絞ると β が動くため α 比較は不適)。参考に β=1 α も併記。
市場タイミング(指数ON/OFF)は正本で逆効果と既知ゆえ対象外＝『戦略自身のボラ』で絞るのが要点。

入力: cache/universe_bars.json (code→{date:close})。出力: reports/momentum_guards.md
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
BARS_PATH = REPO_ROOT / "cache" / "universe_bars.json"
REPORT_PATH = REPO_ROOT / "reports" / "momentum_guards.md"

LOOKBACK = 252
SKIP = 21
TREND_N = 200
VOL_WIN = 63       # バスケット実現ボラの窓(~3か月・クラッシュに即応)
TOP_N = 20
KNIFE_WIN = 20     # 直近20日
KNIFE_THR = -0.15  # −15%以下を除外
COST_PCT = 0.20
OOS_SPLIT = "2024-01"


def mom_12_1(m: dict[str, float], cal: list[str], i: int) -> float | None:
    """12-1モメンタム(直近1か月除外)。"""
    fe, fs = cal[i - SKIP], cal[i - LOOKBACK]
    if fe in m and fs in m and m[fs]:
        return m[fe] / m[fs] - 1.0
    return None


def trend_ok(m: dict[str, float], cal: list[str], i: int) -> bool:
    """200日線の上か(データ75%以上)。"""
    hist = [m[cal[k]] for k in range(max(0, i - TREND_N), i) if cal[k] in m]
    return len(hist) >= TREND_N * 0.75 and cal[i] in m and m[cal[i]] >= statistics.fmean(hist)


def recent_ret(m: dict[str, float], cal: list[str], i: int, win: int) -> float | None:
    """直近 win 営業日のリターン。"""
    a = cal[i - win]
    if a in m and cal[i] in m and m[a]:
        return m[cal[i]] / m[a] - 1.0
    return None


def fwd(m: dict[str, float], cal: list[str], a: int, b: int) -> float | None:
    """cal[a]→cal[b] のリターン。"""
    if cal[a] in m and cal[b] in m and m[cal[a]]:
        return m[cal[b]] / m[cal[a]] - 1.0
    return None


def rebalance_idx(cal: list[str]) -> list[int]:
    """各月末取引日の index。"""
    out = [i - 1 for i in range(1, len(cal)) if cal[i][:7] != cal[i - 1][:7]]
    out.append(len(cal) - 1)
    return out


def basket_vol(picks: list[dict[str, float]], cal: list[str], i: int) -> float | None:
    """選別バスケット(等加重)の直近 VOL_WIN 日次リターン年率ボラ。"""
    daily: list[float] = []
    for k in range(i - VOL_WIN + 1, i + 1):
        rs = []
        for m in picks:
            if cal[k] in m and cal[k - 1] in m and m[cal[k - 1]]:
                rs.append(m[cal[k]] / m[cal[k - 1]] - 1.0)
        if rs:
            daily.append(statistics.fmean(rs))
    if len(daily) < VOL_WIN * 0.6:
        return None
    sd = statistics.pstdev(daily)
    return sd * math.sqrt(252) if sd else None


def select(closes: dict, cal: list[str], i: int, *, knife: bool) -> list[dict[str, float]]:
    """12-1×200日線で上位 TOP_N を選別(knife=直近20日−15%以下を除外し次点補充)。"""
    scored = []
    for code, m in closes.items():
        if not trend_ok(m, cal, i):
            continue
        v = mom_12_1(m, cal, i)
        if v is None:
            continue
        if knife:
            rr = recent_ret(m, cal, i, KNIFE_WIN)
            if rr is not None and rr <= KNIFE_THR:
                continue
        scored.append((v, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:TOP_N]]


def run(closes: dict, cal: list[str], rebs: list[int], topix: dict, *,
        knife: bool = False, target_vol: float | None = None) -> list[dict[str, Any]]:
    """月次バスケットを実行。target_vol 指定で露出=min(1, target/実現ボラ)、残りは現金。"""
    months: list[dict[str, Any]] = []
    for r in range(len(rebs) - 1):
        i, nxt = rebs[r], rebs[r + 1]
        if i < LOOKBACK:
            continue
        picks = select(closes, cal, i, knife=knife)
        if len(picks) < TOP_N:
            continue
        rets = [fwd(m, cal, i, nxt) for m in picks]
        rets = [x for x in rets if x is not None]
        if not rets:
            continue
        gross = statistics.fmean(rets)
        expo = 1.0
        if target_vol is not None:
            bv = basket_vol(picks, cal, i)
            expo = min(1.0, target_vol / bv) if bv else 1.0
        port = (gross * expo) * 100.0
        tret = (fwd(topix, cal, i, nxt) or 0.0) * 100.0
        months.append({"date": cal[nxt][:7], "port": port, "topix": tret,
                       "alpha": port - tret * expo - COST_PCT * expo, "expo": expo})
    return months


def _ann_cagr(months: list[dict[str, Any]]) -> float:
    """月次 port から年率 CAGR(%)。"""
    eq = 1.0
    for x in months:
        eq *= (1 + x["port"] / 100.0)
    yrs = len(months) / 12.0
    return ((eq ** (1 / yrs) - 1) * 100.0) if yrs > 0 and eq > 0 else 0.0


def _max_dd(months: list[dict[str, Any]]) -> float:
    """ポート絶対リターンの最大DD(%)。"""
    eq = peak = 1.0
    dd = 0.0
    for x in months:
        eq *= (1 + x["port"] / 100.0)
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1.0)
    return dd * 100.0


def _sharpe_m(months: list[dict[str, Any]]) -> float:
    """月次 port の Sharpe(年率, rf=0)。"""
    v = [x["port"] for x in months]
    if len(v) < 2:
        return 0.0
    sd = statistics.pstdev(v)
    return (statistics.fmean(v) / sd * math.sqrt(12)) if sd else 0.0


def _alpha_t(months: list[dict[str, Any]]) -> tuple[float, float]:
    """β=1 α の平均と月次t。"""
    v = [x["alpha"] for x in months]
    if len(v) < 2:
        return (0.0, 0.0)
    se = statistics.pstdev(v) / math.sqrt(len(v))
    return statistics.fmean(v), (statistics.fmean(v) / se if se else 0.0)


def summary(label: str, months: list[dict[str, Any]]) -> dict[str, Any]:
    """1戦略の評価指標一式。"""
    cagr, dd = _ann_cagr(months), _max_dd(months)
    am, at = _alpha_t(months)
    te = [x for x in months if x["date"] >= OOS_SPLIT]
    tem, tet = _alpha_t(te)
    worst = min((x["port"] for x in months), default=0.0)
    avg_expo = statistics.fmean([x.get("expo", 1.0) for x in months]) if months else 1.0
    return {"label": label, "cagr": cagr, "dd": dd, "calmar": (cagr / -dd if dd < 0 else 0.0),
            "sharpe": _sharpe_m(months), "worst": worst, "alpha": am, "at": at,
            "oos": tem, "oost": tet, "expo": avg_expo, "n": len(months)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args()
    closes = {c: m for c, m in json.loads(BARS_PATH.read_text()).items() if m}
    topix = {r["Date"]: r["C"] for r in json.loads(TOPIX_PATH.read_text())["records"] if r.get("C")}
    cal = sorted(topix)
    rebs = rebalance_idx(cal)

    variants = [
        summary("G0 ベース(12-1 top20 フル)", run(closes, cal, rebs, topix)),
        summary("G1 ナイフ除外(直近20日−15%↓)", run(closes, cal, rebs, topix, knife=True)),
        summary("G2 ボラ標的20%", run(closes, cal, rebs, topix, target_vol=0.20)),
        summary("G2 ボラ標的25%", run(closes, cal, rebs, topix, target_vol=0.25)),
        summary("G2 ボラ標的30%", run(closes, cal, rebs, topix, target_vol=0.30)),
        summary("G3 標的25%+ナイフ", run(closes, cal, rebs, topix, knife=True, target_vol=0.25)),
    ]
    L = ["# モメンタム・クラッシュ ガード強化（露出レベル対策）", "",
         f"大型+中型 {len(closes)}銘柄 / 月末等加重 / ロングオンリー / コスト{COST_PCT}%・月 / OOS={OOS_SPLIT}。",
         "ロングオンリーゆえ**絶対リターンCAGR・最大DD・Calmar(=CAGR/|DD|)・最悪月・平均露出**で評価。", "",
         "| 戦略 | CAGR% | 最大DD% | Calmar | Sharpe | 最悪月% | 平均露出 | α(t) | OOS α(t) |",
         "|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for s in variants:
        L.append(f"| {s['label']} | {s['cagr']:+.1f} | {s['dd']:.0f} | {s['calmar']:.2f} | "
                 f"{s['sharpe']:.2f} | {s['worst']:+.1f} | {s['expo']:.2f} | "
                 f"{s['alpha']:+.2f}(t{s['at']:+.1f}) | {s['oos']:+.2f}(t{s['oost']:+.1f}) |")
    L += ["", "## 判定の読み方",
          "- **Calmar(CAGR/|DD|)が上がれば改良**＝同じリターンを浅いDDで取れる(クラッシュ耐性↑)。",
          "- ボラ標的はクラッシュ(高ボラ)前に露出を自動で絞る。CAGRをあまり削らずDDが縮めば採用。",
          "- ナイフ除外がCAGR/Calmar/OOSを落とすなら『落ちるナイフ除外はリバウンドも逃す』=不採用。",
          "- αとOOS αは露出スケール後ゆえ参考(露出を絞るとβが減り絶対αは下がる)。主指標はCalmar/DD。"]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(REPORT_PATH, "\n".join(L) + "\n")
    for s in variants:
        print(f"  {s['label']:24s} CAGR{s['cagr']:+.1f}% DD{s['dd']:.0f}% Calmar{s['calmar']:.2f} "
              f"Sharpe{s['sharpe']:.2f} 最悪{s['worst']:+.1f}% 露出{s['expo']:.2f} "
              f"OOSα{s['oos']:+.2f}(t{s['oost']:+.1f})")
    print(f"[mom_guards] → {REPORT_PATH}")


if __name__ == "__main__":
    main()

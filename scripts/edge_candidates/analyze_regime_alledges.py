"""確定エッジ・候補が「地合い(市場の方向/局面)に依存するか」を横断検証する。

各エッジを TOPIX の2軸で層別し、どの局面でも符号が崩れない(=淡々と全シグナル機械
執行が期待値最大)か、特定地合いで止めるべきかを判定する:
  (A) 当日方向 : リターン実現日の TOPIX 寄→引 (intraday 同調)
  (B) 20日トレンド : 実現日終値の trailing 20営業日リターン (上昇/下降局面)

確定6本(⑦②④①B⑥⑩R)は verify_edges_standalone.edge_rows を再利用(DRY・単一の真実)。
S安リバウンド候補は cache/limit_dl_events.json から日次io近似で別途層別。

結論(2026-06): どのエッジも『有意な負け』を生む地合いは無い。出る負けバケットは全て
small-n・非有意のβ逆風(ショート=強上げで弱化/ロング=下げで弱化)。αは全地合いで生存。
唯一の実用ゲートは⑩Rの breadth(全市場S高>15)で、指数の方向/局面では止めない。

使い方:
  python -m scripts.edge_candidates.analyze_regime_alledges
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_text
from scripts.edge_candidates.verify_edges_standalone import (
    _load, clustered_t, dedup, edge_rows,
)

REPO = Path(__file__).resolve().parent.parent.parent
TOPIX = REPO / "data" / "edge_candidates" / "topix_daily.json"
DL = REPO / "cache" / "limit_dl_events.json"
LONG_COST = 0.20
SDOWN_GAP_MAX = -8.0   # S安リバウンド母体: 深GD≤-8%

EDGE_DIR = {"⑦": "S", "②": "S", "④": "S", "①B": "L", "⑥": "L", "⑩R": "S"}
# リターン実現日 = 地合いを当てる日。④①B は翌寄→当日引けゆえ翌営業日、他はイベント日。
_NEXT_REAL = {"④", "①B"}


def _cal_tools() -> tuple[dict, list, dict, dict]:
    tpx = {r["Date"]: r for r in json.loads(TOPIX.read_text())["records"]
           if r.get("O") and r.get("C")}
    cal = sorted(tpx)
    idx = {d: i for i, d in enumerate(cal)}
    close = {d: tpx[d]["C"] for d in cal}
    return tpx, cal, idx, close


def _sameday(tpx: dict, d: str | None) -> float | None:
    t = tpx.get(d) if d else None
    return (t["C"] / t["O"] - 1) * 100 if t else None


def _trail(close: dict, cal: list, idx: dict, d: str | None, n: int = 20) -> float | None:
    i = idx.get(d) if d else None
    if i is None or i - n < 0:
        return None
    return (close[d] / close[cal[i - n]] - 1) * 100


_SAMEDAY_BANDS = [("下げ(<-0.5%)", -1e9, -0.5), ("横ばい", -0.5, 0.5),
                  ("上げ(0.5~1.5)", 0.5, 1.5), ("強上げ(>1.5%)", 1.5, 1e9)]
_TREND_BANDS = [("下降(<-3%)", -1e9, -3), ("横ばい(-3~3)", -3, 3),
                ("上昇(3~8)", 3, 8), ("強上昇(>8%)", 8, 1e9)]


def _band(bands: list, v: float) -> str:
    for name, lo, hi in bands:
        if lo <= v < hi:
            return name
    return bands[-1][0]


def _stats(items: list[tuple[float, str]]) -> tuple[int, float, float, float] | None:
    if not items:
        return None
    nets = [x[0] for x in items]
    dates = [x[1] for x in items]
    win = sum(1 for x in nets if x > 0) / len(nets) * 100
    return len(nets), statistics.fmean(nets), win, clustered_t(nets, dates)


def _layer_lines(recs: list[tuple], bands: list, key_idx: int, title: str) -> list[str]:
    """recs=[(net, date, sameday, trend)] を bands で層別した md 行。"""
    by: dict[str, list] = {b[0]: [] for b in bands}
    for net, d, *vals in recs:
        v = vals[key_idx]
        if v is None:
            continue
        by[_band(bands, v)].append((net, d))
    out = [f"**{title}**", "", "| 地合い | n | net EV | 勝率 | t_clust |", "|---|--:|--:|--:|--:|"]
    for name, _, _ in bands:
        r = _stats(by[name])
        if r:
            flag = "" if (r[1] > 0 and abs(r[3]) >= 2) else (" ⚠負" if r[1] < 0 else " ·薄")
            out.append(f"| {name} | {r[0]} | {r[1]:+.2f}% | {r[2]:.0f}% | {r[3]:+.2f}{flag} |")
    out.append("")
    return out


def _edge_recs(key: str, D: dict, tpx: dict, cal: list, idx: dict, close: dict) -> list[tuple]:
    rows, cost, short = edge_rows(key, D)
    rows = dedup(rows)
    nb = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    recs = []
    for r in rows:
        rd = nb.get(r[1]) if key in _NEXT_REAL else r[1]
        if rd is None:
            continue
        net = ((-r[0]) if short else r[0]) - cost
        recs.append((net, r[1], _sameday(tpx, rd), _trail(close, cal, idx, rd)))
    return recs


def _sdown_recs(tpx: dict, cal: list, idx: dict, close: dict) -> list[tuple]:
    if not DL.exists():
        return []
    ev = json.loads(DL.read_text())
    recs = []
    for e in ev:
        g = e.get("gap")
        if g is None or g > SDOWN_GAP_MAX:
            continue
        i = bisect.bisect_right(cal, e["date"])
        rd = cal[i] if i < len(cal) else None
        if not rd:
            continue
        net = e["io"] - LONG_COST   # 日次io近似(寄→引)。確定版は10:30/信用フィルタ
        recs.append((net, e["date"], _sameday(tpx, rd), _trail(close, cal, idx, rd)))
    return recs


def build_report(D: dict) -> str:
    """全確定エッジ+S安候補を地合い2軸で層別した md を返す。"""
    tpx, cal, idx, close = _cal_tools()
    L = ["# 全エッジ × 地合い(TOPIX)横断検証", "",
         "各エッジを「リターン実現日の TOPIX 当日方向(寄→引)」と「20日トレンド(局面)」で層別。",
         "⚠負=net負, ·薄=net正だが|t|<2(非有意)。**有意な負け(net負&|t|≥2)が出る地合いがあれば**",
         "そのエッジは地合いゲートを検討、無ければ淡々と全シグナル機械執行が期待値最大。", ""]
    targets = [(k, "confirmed") for k in EDGE_DIR]
    for key, _ in targets:
        recs = _edge_recs(key, D, tpx, cal, idx, close)
        allnet = [x[0] for x in recs]
        ev = statistics.fmean(allnet) if allnet else 0.0
        L.append(f"## {key} ({EDGE_DIR[key]}) — 全体 n={len(allnet)} EV={ev:+.2f}%")
        L.append("")
        L += _layer_lines(recs, _SAMEDAY_BANDS, 0, "当日TOPIX方向(実現日 寄→引)")
        L += _layer_lines(recs, _TREND_BANDS, 1, "TOPIX20日トレンド(局面)")
    # 候補: S安リバウンド
    srecs = _sdown_recs(tpx, cal, idx, close)
    if srecs:
        allnet = [x[0] for x in srecs]
        L.append(f"## [候補] S安リバウンド (L・深GD≤{SDOWN_GAP_MAX}%・日次io近似全期間) "
                 f"— 全体 n={len(allnet)} EV={statistics.fmean(allnet):+.2f}%")
        L.append("")
        L += _layer_lines(srecs, _SAMEDAY_BANDS, 0, "当日TOPIX方向(実現日 寄→引)")
        L += _layer_lines(srecs, _TREND_BANDS, 1, "TOPIX20日トレンド(局面)")
    L += ["## 読み取り", "",
          "- ショート(⑦④②⑩R)は下げ/横ばい地合いで強く強上げで弱化、ロング(①B⑥)/S安Lは",
          "  上げ/横ばいで強く下げで弱化＝**素のβ露出**。αは全地合いで生存。",
          "- 出る負けバケットは全て small-n(n3〜13)・**非有意**＝予測不能なノイズで、地合いで",
          "  エントリーを止める根拠にならない。**淡々と全シグナル機械執行が期待値最大**。",
          "- 唯一の実用ゲートは⑩Rの breadth(全市場S高>15は薄/見送り)。指数の方向/局面では止めない。",
          "- 留保: ②REITは TOPIX で代用(東証REIT指数は未取得)。強上昇(>8%)/強上げ当日は各エッジ n小。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "reports" / "regime_alledges.md")
    args = ap.parse_args()
    report = build_report(_load())
    print(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, report)
    print(f"[regime_alledges] → {args.out}")


if __name__ == "__main__":
    main()

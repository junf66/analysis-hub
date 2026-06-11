"""PO発表翌日の『S安/大幅GDで投げられた非希薄化PO』の自律反発ロング検証。

仮説: 希薄化しない(売出中心)・本質的に悪材料でないPOで、翌日に knee-jerk で
大きくGD/S安まで投げられた銘柄は、当日中に自律反発する → 翌寄り買い→翌引け売り。

データ: data/po_records.json(stage=announce) の dilution/gap_pct/po_scale + 翌営業日(D+1)の
日足(生O/C・S安フラグLL)を追加取得。価格は同一バーの生値で統一(調整ズレ回避)。
リターン= D+1 寄→引 (raw)。コスト long 0.20%。クラスタ=発表日。OOS=2024split。

出力: reports/po_oversold_long.md
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
PO_PATH = REPO_ROOT / "data" / "po_records.json"
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
CACHE_PATH = REPO_ROOT / "cache" / "po_announce_d1.json"
REPORT_PATH = REPO_ROOT / "reports" / "po_oversold_long.md"

LONG_COST_PCT = 0.20
OOS_SPLIT = "2024-01-01"
DILUTE_MAX = 2.0   # 非希薄化のしきい値(%)


def load_announce(cal: list[str]) -> list[dict[str, Any]]:
    """stage=announce の発表イベント(code, date, dil, gap, scale, mcap, d1)を返す。"""
    d = json.loads(PO_PATH.read_text())
    po = d["records"] if isinstance(d, dict) else d
    idx = {x: i for i, x in enumerate(cal)}
    out: list[dict[str, Any]] = []
    for r in po:
        if r.get("stage") != "announce":
            continue
        a = r.get("attrs") or {}
        code, dt = str(r.get("code") or "")[:4], r.get("event_date")
        if not code or not dt:
            continue
        prior = [x for x in cal if x <= dt]
        if not prior or idx[prior[-1]] + 1 >= len(cal):
            continue
        out.append({"code": code, "date": dt, "d1": cal[idx[prior[-1]] + 1],
                    "dil": r.get("dilution"), "gap": a.get("gap_pct"),
                    "scale": r.get("po_scale"), "mcap": r.get("market_cap"),
                    "po_type": r.get("po_type")})
    return out


def fetch_d1(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """各イベントの D+1 日足(生 O/C・LL)を取得。キー=`code|d1`。cache 併用。"""
    from scripts import _jquants
    cache: dict[str, dict[str, Any]] = {}
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text())
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    for i, ev in enumerate(events, 1):
        key = f"{ev['code']}|{ev['d1']}"
        if key in cache:
            continue
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=ev["code"],
                                     **{"from": ev["d1"], "to": ev["d1"]})
            b = bars[0] if bars else {}
            cache[key] = {"O": b.get("O"), "C": b.get("C"), "LL": b.get("LL")}
        except _jquants.JQuantsError:
            cache[key] = {}
        if i % 50 == 0:
            atomic_write_json(CACHE_PATH, cache)
            print(f"  fetched {i}/{len(events)}")
    atomic_write_json(CACHE_PATH, cache)
    return cache


def build_rows(events: list[dict[str, Any]], d1bars: dict, scale_band: dict[str, str]) -> list[dict[str, Any]]:
    """各イベントに D+1 寄→引リターン(生)と規模区分を付与。"""
    rows: list[dict[str, Any]] = []
    for ev in events:
        b = d1bars.get(f"{ev['code']}|{ev['d1']}") or {}
        o, c = b.get("O"), b.get("C")
        if not o or not c:
            continue
        rows.append({**ev, "ret": (c / o - 1.0) * 100.0, "ll": b.get("LL") == "1",
                     "band": scale_band.get(ev["code"])})
    return rows


def _clustered_t(byd: dict[str, list[float]]) -> tuple[float, float, int]:
    """発表日クラスタ頑健 t。"""
    allv = [v for vs in byd.values() for v in vs]
    n = len(allv)
    if n < 2:
        return (statistics.fmean(allv) if allv else 0.0), 0.0, n
    mean = statistics.fmean(allv)
    num = sum(sum(v - mean for v in vs) ** 2 for vs in byd.values())
    se = math.sqrt(num) / n
    return mean, (mean / se if se else 0.0), n


def stat(rows: list[dict[str, Any]], filt: Callable[[dict], bool]) -> dict[str, Any]:
    """フィルタ後の long net EV・t・勝率・OOS test を返す。"""
    sub = [r for r in rows if filt(r)]

    def byd(s: list[dict]) -> dict[str, list[float]]:
        g: dict[str, list[float]] = {}
        for r in s:
            g.setdefault(r["date"], []).append(r["ret"])
        return g
    m, t, n = _clustered_t(byd(sub))
    win = (sum(1 for r in sub if r["ret"] > 0) / len(sub) * 100) if sub else 0.0
    mte, tte, nte = _clustered_t(byd([r for r in sub if r["date"] >= OOS_SPLIT]))
    return {"net": m - LONG_COST_PCT, "raw": m, "t": t, "n": n, "win": win,
            "test_net": mte - LONG_COST_PCT, "test_t": tte, "test_n": nte}


def build_report(rows: list[dict[str, Any]]) -> str:
    """各フィルタの long EV を Markdown 表に。"""
    cuts: list[tuple[str, Callable[[dict], bool]]] = [
        ("全announce 翌寄→引", lambda r: True),
        ("非希薄化(≤2%)", lambda r: (r["dil"] or 99) <= DILUTE_MAX),
        ("大幅GD gap≤-5%", lambda r: (r["gap"] or 0) <= -5),
        ("大幅GD gap≤-7%", lambda r: (r["gap"] or 0) <= -7),
        ("S安(LL flag)", lambda r: r["ll"]),
        ("非希薄化 × gap≤-5%", lambda r: (r["dil"] or 99) <= DILUTE_MAX and (r["gap"] or 0) <= -5),
        ("非希薄化 × gap≤-7%", lambda r: (r["dil"] or 99) <= DILUTE_MAX and (r["gap"] or 0) <= -7),
        ("中型(Mid400) × gap≤-5%", lambda r: r["band"] == "中型" and (r["gap"] or 0) <= -5),
        ("小型 × gap≤-5%", lambda r: r["band"] == "小型" and (r["gap"] or 0) <= -5),
    ]
    L = ["# PO発表翌日『S安/大幅GD非希薄化』自律反発ロング検証", "",
         f"announce {len(rows)}件(D+1価格取得済)。翌寄り買い→翌引け売り(生値)。"
         f" net=long往復{LONG_COST_PCT}%控除。クラスタ=発表日。OOS={OOS_SPLIT[:7]}。", "",
         "| フィルタ | net EV% | raw% | t_clust | 勝率% | n | OOS test |",
         "|---|--:|--:|--:|--:|--:|---|"]
    for label, f in cuts:
        s = stat(rows, f)
        L.append(f"| {label} | {s['net']:+.2f} | {s['raw']:+.2f} | {s['t']:+.2f} | {s['win']:.0f} | "
                 f"{s['n']} | {s['test_net']:+.2f}(t{s['test_t']:+.1f})/n{s['test_n']} |")
    L += ["", "## 読み方",
          "- net EV>0 かつ |t_clust|≥2、OOS test も正で残れば自律反発ロングのエッジ候補。",
          "- 寄→引は当日完結ゆえ β 露出小（raw≒net+cost）。S安は寄付が約定しにくい点に注意。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true", help="キャッシュのみ使用")
    ap.add_argument("--out", type=Path, default=REPORT_PATH, help="出力 md")
    args = ap.parse_args()

    cal = sorted(r["Date"] for r in json.loads(TOPIX_PATH.read_text())["records"])
    events = load_announce(cal)
    scale_band = {str(r["Code"])[:4]: r.get("scale_band")
                  for r in json.loads(MASTER_PATH.read_text())["records"]}
    d1bars = json.loads(CACHE_PATH.read_text()) if (args.no_fetch and CACHE_PATH.exists()) \
        else fetch_d1(events)
    rows = build_rows(events, d1bars, scale_band)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, build_report(rows))
    print(f"[po_oversold] announce{len(events)} / 価格化{len(rows)} → {args.out}")
    for label, f in (("全体", lambda r: True),
                     ("非希薄化×gap≤-5%", lambda r: (r["dil"] or 99) <= DILUTE_MAX and (r["gap"] or 0) <= -5),
                     ("gap≤-7%", lambda r: (r["gap"] or 0) <= -7),
                     ("S安LL", lambda r: r["ll"])):
        s = stat(rows, f)
        print(f"  {label:16s} net{s['net']:+.2f}% t{s['t']:+.2f} win{s['win']:.0f}% n{s['n']}"
              f"  test{s['test_net']:+.2f}(t{s['test_t']:+.1f})")


if __name__ == "__main__":
    main()

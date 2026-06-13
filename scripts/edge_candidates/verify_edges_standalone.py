"""エッジ独立検証スクリプト（自己完結・第三者監査用）。

第三者(別AI/別実装)が「主張されたエッジの数値」を生JSONから再計算して突合できるよう、
統計関数(クラスタ頑健t・OOS分割)を**インライン**で持つ自己完結スクリプト。
統計ロジックは analyzers/ 等に依存しない（=検証器ごと疑える）。ファイル書き込みのみ
scripts._atomic を使う（リポ規約: 中断耐性のため）。

2モード:
  (既定) 検証   : 生JSONから定義どおり再計算 → CLAIMED と突合し ✅/⚠ を出す。
  --export PATH : 各エッジのトレード明細(メタ付)を1つの JSON に書き出す。
                  → リポ/API に触れない第三者(Codex等)が、その JSON だけで
                    (1)統計の再計算 (2)銘柄選定の異常検知(ETF混入/極端値/規模誤分類) ができる。

許容差: EV/OOS ±0.06%, t ±0.12, 勝率 ±2.5pt, n 完全一致。

使い方:
  python -m scripts.edge_candidates.verify_edges_standalone            # 検証
  python -m scripts.edge_candidates.verify_edges_standalone --export edge_trades.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json, atomic_write_text

REPO = Path(__file__).resolve().parent.parent.parent
LONG_COST, SHORT_COST = 0.20, 0.15


# ---- インライン統計 (第三者が1行ずつ監査できるよう自前実装) -----------------

def clustered_t(values: list[float], clusters: list[Any]) -> float:
    """日付クラスタ頑健 t。同一クラスタ内相関で素朴 t が水増しされるのを補正。"""
    n = len(values)
    if n < 2:
        return 0.0
    mu = statistics.fmean(values)
    by: dict[Any, float] = defaultdict(float)
    for v, c in zip(values, clusters):
        by[c] += v - mu
    g = len(by)
    if g < 2:
        s = statistics.stdev(values)
        return mu / (s / math.sqrt(n)) if s else 0.0
    meat = sum(s * s for s in by.values())
    var = (g / (g - 1)) * meat / (n * n)
    return mu / math.sqrt(var) if var > 0 else 0.0


def oos_test(rows: list[tuple], cost: float, short: bool, frac: float = 0.7) -> float:
    """walk-forward: 日付順 frac で train/test 分割、方向は train で決め test の net EV。"""
    rows = sorted(rows, key=lambda x: x[1])
    cut = int(len(rows) * frac)
    tr, te = rows[:cut], rows[cut:]
    if not tr or not te:
        return float("nan")
    tr_short = statistics.fmean([r[0] for r in tr]) < 0
    sgn = -1 if tr_short else 1
    return statistics.fmean([sgn * r[0] - cost for r in te])


def metrics(rows: list[tuple], cost: float, short: bool) -> dict[str, float]:
    """rows=[(生リターン%, 日付, code, ...)] → n/EV/勝率/t_clust/OOS/直近3年頻度 を net 基準で。"""
    nets = [(-r[0] if short else r[0]) - cost for r in rows]
    dates = [r[1] for r in rows]
    yr = Counter(d[:4] for d in dates)
    return {
        "n": len(nets),
        "ev": statistics.fmean(nets),
        "win": sum(1 for x in nets if x > 0) / len(nets) * 100,
        "t": clustered_t(nets, dates),
        "oos": oos_test(rows, cost, short),
        "freq3y": statistics.mean([yr.get(y, 0) for y in ("2024", "2025", "2026")]),
    }


# ---- データ ----------------------------------------------------------------

def _load() -> dict[str, Any]:
    j = lambda p: json.loads((REPO / p).read_text())  # noqa: E731
    return {
        "po": j("data/po_records.json")["records"],
        "ko": j("data/kouaku_records.json")["records"],
        "enr": j("data/edge_candidates/po_enriched.json")["by_id"],
        "tpx": {r["Date"]: r for r in j("data/edge_candidates/topix_daily.json")["records"]
                if r.get("O") and r.get("C")},
        "mst": {r["Code"]: r for r in j("data/edge_candidates/equities_master.json")["records"]},
        "ul": j("cache/limit_ul_events.json") if (REPO / "cache/limit_ul_events.json").exists() else [],
        "hist": j("cache/master_history.json") if (REPO / "cache/master_history.json").exists() else {},
    }


def _c5(c: str) -> str:
    return c + "0" if len(c) == 4 else c


def _disc_after_close(r: dict) -> bool:
    """大引け後(15:30以降)開示か。正準 disc_bucket と同義: 最早 disc_time >= 15:30。"""
    times = [f.get("disc_time") for f in (r.get("bad_factors") or []) + (r.get("good_factors") or [])]
    times = [t for t in times if t]
    return bool(times) and min(times) >= "15:30"


def _snap(dates_sorted: list[str], date: str) -> str | None:
    if not dates_sorted:
        return None
    chosen = dates_sorted[0]
    for d in dates_sorted:
        if d <= date:
            chosen = d
        else:
            break
    return chosen


def _pit(hist: dict, hd: list[str], code: str, date: str) -> dict:
    s = _snap(hd, date)
    return (hist[s].get(_c5(code)) or {}) if s else {}


# ---- 各エッジの再計算。row = (ret%, date, code, name, market) -----------------

def edge_rows(key: str, D: dict[str, Any]) -> tuple[list[tuple], float, bool]:
    """エッジ key の (rows, cost, short)。row=(ret%,date,code,name,market)。"""
    po, ko, enr, tpx, mst, ul = D["po"], D["ko"], D["enr"], D["tpx"], D["mst"], D["ul"]
    cal = sorted(tpx)
    nxt = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    hd = sorted(D["hist"])
    nm = lambda c: (mst.get(_c5(c)) or {}).get("CoName", "?")            # noqa: E731
    mk = lambda c: (mst.get(_c5(c)) or {}).get("MktNm", "?")            # noqa: E731

    rows: list[tuple] = []
    if key == "⑦":   # 売出のみ×普通×決定日場中(寄→引) α=TOPIX同日控除, short
        for r in po:
            if r.get("stage") == "decide" and r.get("po_type") == "普通" and r.get("dilution") == 0.0:
                a = r.get("attrs") or {}
                do, dc = a.get("dec_open"), a.get("dec_close")
                tr = tpx.get(r.get("event_date")) or {}
                if do and dc and tr.get("O") and tr.get("C"):
                    v = (dc / do - 1) * 100 - (tr["C"] / tr["O"] - 1) * 100
                    rows.append((v, r["event_date"], r["code"], nm(r["code"]), mk(r["code"])))
        return rows, SHORT_COST, True

    if key == "②":   # REIT×貸借×決定 ret_close short (raw)
        for r in po:
            if r.get("stage") == "decide" and r.get("po_type") == "リート" and r.get("lending_type") == "貸借":
                rc = (r.get("attrs") or {}).get("ret_close")
                if rc is not None:
                    rows.append((float(rc), r["event_date"], r["code"], nm(r["code"]), mk(r["code"])))
        return rows, SHORT_COST, True

    if key == "④":   # zouhai_kahou_nx×大引け後 翌寄→引 short (raw)
        for r in ko:
            a = r.get("attrs") or {}
            if (r.get("subpattern") == "zouhai_kahou_nx" and _disc_after_close(r)
                    and not a.get("limit_locked") and a.get("next_day_open_to_close_ret") is not None):
                rows.append((float(a["next_day_open_to_close_ret"]), r["event_date"], r["code"],
                             nm(r["code"]), mk(r["code"])))
        return rows, SHORT_COST, True

    if key == "①B":  # 普通×PIT中型×翌日GD(gap≤-0.5) 翌寄→引 long
        for r in po:
            if r.get("stage") == "announce" and r.get("po_type") == "普通":
                a = r.get("attrs") or {}
                gap = a.get("gap_pct")
                oc = (enr.get(r["id"]) or {}).get("next_day_open_to_close_ret")
                if gap is not None and gap <= -0.5 and oc is not None \
                        and _pit(D["hist"], hd, r["code"], r.get("event_date")).get("scale_band") == "中型":
                    rows.append((float(oc), r["event_date"], r["code"], nm(r["code"]), mk(r["code"])))
        return rows, LONG_COST, False

    if key == "⑥":   # 普通×受渡日×gap<+0.5×調達額≥300億 寄→引 long
        for r in po:
            a = r.get("attrs") or {}
            if (r.get("stage") == "deliver" and r.get("po_type") == "普通" and a.get("gap_pct") is not None
                    and float(a["gap_pct"]) < 0.5 and r.get("po_scale") and float(r["po_scale"]) >= 300
                    and a.get("next_day_open_to_close_ret") is not None):
                rows.append((float(a["next_day_open_to_close_ret"]), r["event_date"], r["code"],
                             nm(r["code"]), mk(r["code"])))
        return rows, LONG_COST, False

    if key == "①A":  # 普通×時価≥5000億×翌日GD 翌寄→引 long (raw)
        for r in po:
            if r.get("stage") == "announce" and r.get("po_type") == "普通" and (r.get("market_cap") or 0) >= 5000:
                a = r.get("attrs") or {}
                gap = a.get("gap_pct")
                oc = (enr.get(r["id"]) or {}).get("next_day_open_to_close_ret")
                if gap is not None and gap <= -0.5 and oc is not None:
                    rows.append((float(oc), r["event_date"], r["code"], nm(r["code"]), mk(r["code"])))
        return rows, LONG_COST, False

    if key == "⑩R":  # スタ/グロ×貸借(PIT)×S高×翌朝中GU(5-10%) 翌寄→引 short
        for e in ul:
            p = _pit(D["hist"], hd, e["code"], e["date"])
            if p.get("scale_band") != "小型" or p.get("MrgnNm") != "貸借":
                continue
            if mk(e["code"]) not in ("スタンダード", "グロース"):
                continue
            g = e.get("gap")
            if g is None or not (5 < g <= 10) or not (nxt.get(e["date"]) and tpx.get(nxt.get(e["date"]))):
                continue
            rows.append((e["io"], e["date"], e["code"], nm(e["code"]), mk(e["code"])))
        return rows, SHORT_COST, True

    raise ValueError(key)


# 主張値 (チートシート/共有資料の数値。検証で一致を確認する対象)
CLAIMED = {
    "⑦":  {"dir": "S", "n": 211, "ev": 0.68, "win": 58, "t": 3.77, "oos": 0.43},
    "②":  {"dir": "S", "n": 131, "ev": 0.98, "win": 60, "t": 3.49, "oos": 0.93},  # raw版
    "④":  {"dir": "S", "n": 239, "ev": 0.88, "win": 63, "t": 4.98, "oos": 1.28},  # raw実現EV
    "①B": {"dir": "L", "n": 34,  "ev": 1.05, "win": 68, "t": 2.81, "oos": 1.39},
    "⑥":  {"dir": "L", "n": 61,  "ev": 0.78, "win": 61, "t": 2.72, "oos": 0.50},
    "①A": {"dir": "L", "n": 27,  "ev": 0.83, "win": 56, "t": 1.79, "oos": 1.17},  # raw(候補)
    "⑩R": {"dir": "S", "n": 430, "ev": 2.68, "win": 61, "t": 5.11, "oos": 2.01},  # 中GUクリーン(候補)
}
_DEF = {
    "⑦": "売出のみ(dilution=0)×普通株×PO発行価格決定日。決定日 寄→引 を空売り。α=同日TOPIX寄→引控除。cost0.15。",
    "②": "J-REIT×貸借×PO価格決定。発表翌寄→決定日引け を空売り(ret_close)。cost0.15。raw。",
    "④": "増配+来期下方修正(zouhai_kahou_nx)×大引け後(15:30+)開示。翌寄→当日引け を空売り。cost0.15。raw。",
    "①B": "普通株×イベント日時点TOPIX中型(Mid400)×翌日GD(gap≤-0.5%)。翌寄→当日引け を買い。cost0.20。",
    "⑥": "普通株×PO受渡日×gap<+0.5%×調達額≥300億。受渡日 寄→引け を買い。cost0.20。",
    "①A": "普通株×時価総額≥5000億円×翌日GD(gap≤-0.5%)。翌寄→当日引け を買い。cost0.20。raw(候補)。",
    "⑩R": "スタンダード/グロース×貸借(イベント日時点)×当日S高×翌朝中GU(寄り前日比+5〜10%)。翌寄→当日引け を空売り。cost0.15。",
}
_TOL = {"ev": 0.06, "oos": 0.06, "t": 0.12, "win": 2.5}


def build_report(D: dict[str, Any]) -> str:
    """全エッジを再計算し CLAIMED と突合した md を返す。"""
    L = ["# エッジ独立検証 (自己完結・第三者監査用)", "",
         "生JSONから定義どおり再計算し主張値(CLAIMED)と突合。⚠=主張値と不一致(主張側が誤り)。",
         f"許容差: EV±{_TOL['ev']}% / OOS±{_TOL['oos']}% / t±{_TOL['t']} / 勝率±{_TOL['win']}pt / n完全一致。", "",
         "| エッジ | 方向 | n | EV | 勝率 | t_clust | OOS | 直近3年/年 | 判定 |",
         "|---|:--:|---|---|---|---|---|--:|:--:|"]
    allok = True
    for k, c in CLAIMED.items():
        rows, cost, short = edge_rows(k, D)
        m = metrics(rows, cost, short)
        f = lambda a, b, t: "" if abs(a - b) <= t else "⚠"  # noqa: E731
        chk = (m["n"] == c["n"] and not any([f(m["ev"], c["ev"], _TOL["ev"]), f(m["win"], c["win"], _TOL["win"]),
               f(m["t"], c["t"], _TOL["t"]), f(m["oos"], c["oos"], _TOL["oos"])]))
        allok = allok and chk
        L.append(
            f"| {k} | {c['dir']} | {c['n']}→{m['n']}{'' if m['n']==c['n'] else '⚠'} | "
            f"{c['ev']:+.2f}→{m['ev']:+.2f}{f(m['ev'],c['ev'],_TOL['ev'])} | "
            f"{c['win']:.0f}→{m['win']:.0f}{f(m['win'],c['win'],_TOL['win'])} | "
            f"{c['t']:+.2f}→{m['t']:+.2f}{f(m['t'],c['t'],_TOL['t'])} | "
            f"{c['oos']:+.2f}→{m['oos']:+.2f}{f(m['oos'],c['oos'],_TOL['oos'])} | {m['freq3y']:.0f} | "
            f"{'✅' if chk else '⚠'} |")
    L += ["", f"## 総合: {'✅ 全エッジ主張値と一致' if allok else '⚠ 不一致あり'}", "",
          "②=raw版で照合(β=0.43調整版は+0.85%/t3.22)。④=raw実現EV(固有=基線控除後+0.51%/t2.87)。"
          "①A・⑩Rは候補(①A=FDR未通過 / ⑩R=逆日歩コスト未確定)。"]
    return "\n".join(L) + "\n"


def export_bundle(D: dict[str, Any], path: Path) -> None:
    """各エッジのトレード明細(メタ付)+定義+主張値を1 JSON に。リポ/API不要で第三者が検証可能。"""
    out: dict[str, Any] = {"_about": "エッジ独立検証バンドル: rows から n/EV/勝率/t/OOS を再計算し claimed と突合。"
                           "row=[ret%, date, code, name, market]。net=方向別cost控除後。",
                           "cost": {"long": LONG_COST, "short": SHORT_COST}, "edges": {}}
    for k, c in CLAIMED.items():
        rows, cost, short = edge_rows(k, D)
        out["edges"][k] = {
            "definition": _DEF[k], "direction": "short" if short else "long", "cost": cost,
            "claimed": c, "recomputed": metrics(rows, cost, short),
            "rows": [[round(r[0], 4), r[1], r[2], r[3], r[4]] for r in rows],
        }
    atomic_write_json(path, out, indent=1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "reports" / "edge_verification.md")
    ap.add_argument("--export", type=Path, default=None, help="トレード明細バンドル(JSON)を書き出す")
    args = ap.parse_args()
    D = _load()
    if args.export:
        export_bundle(D, args.export)
        print(f"[verify] bundle → {args.export}")
        return
    report = build_report(D)
    print(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, report)
    print(f"[verify] → {args.out}")


if __name__ == "__main__":
    main()

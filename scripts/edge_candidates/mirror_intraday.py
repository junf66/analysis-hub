"""確定エッジの鏡(反対方向)を分足出口で検証(②⑦⑥⑩R・2024-05〜)。④①Bは記録済ゆえ対象外。

各確定エッジを反対方向にし、取引日(⑦=決定日/⑥=受渡日/⑩R=翌日/②=決定日)の分足で
寄→{9:15,9:30,10:00,11:30,引け}を計算。鏡が全出口でマイナスなら方向は本物。
②は多日(announce翌寄→決定日)ゆえ entry=ref_open(announce翌寄)→決定日intraday。

価格cache: cache/mirror_minute.json。出力: 標準出力。
"""
from __future__ import annotations
import bisect
import json
import statistics
from pathlib import Path
from scripts import _jquants
from scripts._atomic import atomic_write_json
from scripts.edge_candidates.verify_edges_standalone import _load, _pit
from analyzers.stats import clustered_se

REPO = Path(__file__).resolve().parent.parent.parent
MC = REPO / "cache" / "mirror_minute.json"
EXITS = ["09:15", "09:30", "10:00", "11:30"]


def _c5(c): return c if len(str(c)) == 5 else str(c) + "0"


def main() -> None:
    D = _load(); po = D["po"]; ul = D["ul"]; hist = D["hist"]; hd = sorted(hist) if hist else []
    tpx = D["tpx"]; cal = sorted(tpx)
    cache = json.loads(MC.read_text()) if MC.exists() else {}

    def nxt(d):
        i = bisect.bisect_right(cal, d); return cal[i] if i < len(cal) else None

    def minute(code, date):
        k = f"{code}|{date}"
        if k in cache:
            return cache[k]
        try:
            b = _jquants.get_list("/equities/bars/minute", code=_c5(code), date=date)
            rows = [[x["Time"], x.get("O"), x.get("C")] for x in b if x.get("O") and x.get("C")]
        except Exception:
            rows = []
        cache[k] = rows
        return rows

    # 各エッジ: (events=[(code,trade_day,entry_price_or_None)], short_mirror, cost, label)
    inst = {"プライム", "東証一部", "その他", "TOKYO PRO MARKET", None}
    jobs = {}
    # ⑩R鏡=LONG(本来short) entry=翌日寄(=その日のminute open) cost0.20
    r10 = []
    for e in ul:
        p = _pit(hist, hd, e["code"], e["date"])
        if p.get("scale_band") == "小型" and p.get("MrgnNm") == "貸借" and p.get("MktNm") not in inst:
            g = e.get("gap"); nb = nxt(e["date"])
            if g is not None and 5 < g <= 10 and nb and nb >= "2024-05-21":
                r10.append((e["code"], nb, None))
    jobs["⑩R鏡(LONG)"] = (r10, False, 0.20)
    # ⑦鏡=LONG entry=決定日寄 cost0.20
    uri = [(r["code"], r["event_date"], None) for r in po if r.get("stage") == "decide"
           and r.get("po_type") == "普通" and r.get("dilution") == 0 and r["event_date"] >= "2024-05-21"]
    jobs["⑦鏡(LONG)"] = (uri, False, 0.20)
    # ⑥鏡=SHORT entry=受渡日寄 cost0.15
    dlv = [(r["code"], r["event_date"], None) for r in po if r.get("stage") == "deliver"
           and r.get("po_type") == "普通" and (r.get("attrs") or {}).get("gap_pct") is not None
           and r["attrs"]["gap_pct"] < 0.5 and r.get("po_scale") and float(r["po_scale"]) >= 300
           and r["event_date"] >= "2024-05-21"]
    jobs["⑥鏡(SHORT)"] = (dlv, True, 0.15)
    # ②鏡=LONG entry=announce翌寄(ref_open) exit=決定日intraday cost0.20
    reit = [(r["code"], r["event_date"], (r.get("attrs") or {}).get("ref_open")) for r in po
            if r.get("stage") == "decide" and r.get("po_type") == "リート" and r["event_date"] >= "2024-05-21"]
    jobs["②鏡(LONG・寄=announce翌寄)"] = (reit, False, 0.20)

    for lab, (events, short, cost) in jobs.items():
        per = {t: [] for t in EXITS + ["引け"]}
        for code, day, entry0 in events:
            b = minute(code, day)
            if not b:
                continue
            o = entry0 if entry0 else b[0][1]
            if not o:
                continue
            tmap = {t: c for t, _, c in b}
            for t in EXITS:
                p = tmap.get(t) or next((c for tt, c in sorted(tmap.items()) if tt <= t), None)
                if p:
                    r = (p / o - 1) * 100
                    per[t].append(((-r if short else r) - cost, day[:7]))
            cl = b[-1][2]
            if cl:
                r = (cl / o - 1) * 100
                per["引け"].append(((-r if short else r) - cost, day[:7]))
        atomic_write_json(MC, cache)
        print(f"=== {lab} ===")
        for t in EXITS + ["引け"]:
            rs = per[t]
            if len(rs) < 12:
                print(f"  {t}: n{len(rs)}"); continue
            v = [a for a, _ in rs]; se = clustered_se(v, [m for _, m in rs])
            print(f"  寄→{t}: net{statistics.fmean(v):+.2f}% 勝{sum(1 for x in v if x>0)/len(v)*100:.0f}% "
                  f"t{(statistics.fmean(v)/se if se else 0):+.2f} n{len(v)}")
    atomic_write_json(MC, cache)


if __name__ == "__main__":
    main()

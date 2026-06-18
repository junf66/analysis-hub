"""信用→貸借 銘柄選定イベントの検出 + 翌寄→各出口 long の検証（新ネタ）。

東証/日証金が「信用銘柄を貸借銘柄に選定」(発表=前日引け後・実施=当日)。流動性向上の買い期待 vs
空売り可能化(弱気)の綱引き。検証: 実施日の寄りで long → 当日デイトレ手仕舞い、出口別・GU帯別。

イベント検出: /equities/master を月次グリッドで取得→MrgnNm が (信用|なし)→貸借 に変わったコードを特定し、
窓内を日次で二分探索して**正確な実施日**を確定。実施日=貸借になった最初の日(=寄りで買える日)。
前日終値=実施前日の終値、寄り/引け=実施日。out=寄→引(15:30)を主に、GU帯別 EV/勝率/クラスタt。

network: api.jquants.com。出力: data/edge_candidates/margin_promotion_events.json + 標準出力に分析。
"""
from __future__ import annotations

import bisect
import datetime
import json
import statistics
from pathlib import Path

from scripts import _jquants
from scripts._atomic import atomic_write_json
from scripts.edge_candidates.analyze_archive_regime import clustered_t

REPO = Path(__file__).resolve().parent.parent.parent
EVENTS = REPO / "data" / "edge_candidates" / "margin_promotion_events.json"
START = "2024-05-21"     # 分足開始＝以降ならintraday拡張可。日足検証はこの制約不要だが揃える
SHORT_HINT = "貸借"


def _master_mrgn(date: str, cache: dict) -> dict:
    """その日の Code→MrgnNm（キャッシュ付き）。"""
    if date in cache:
        return cache[date]
    try:
        r = _jquants.get_list("/equities/master", date=date)
        m = {str(x["Code"]): x.get("MrgnNm") for x in r}
    except Exception:  # noqa: BLE001
        m = {}
    cache[date] = m
    return m


def _month_grid(start: str, end: str) -> list[str]:
    s = datetime.date.fromisoformat(start); e = datetime.date.fromisoformat(end)
    out = []
    y, m = s.year, s.month
    while datetime.date(y, m, 1) <= e:
        out.append(datetime.date(y, m, 1).isoformat())
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    out.append(end)
    return out


def detect_events(cache: dict, cal: list[str]) -> list[dict]:
    """月次グリッドで遷移コードを拾い、二分探索で実施日を確定。"""
    end = cal[-1]
    grid = [d for d in _month_grid(START, end)]
    grid = [_onafter(cal, d) for d in grid]
    grid = sorted(set(d for d in grid if d))
    snaps = {d: _master_mrgn(d, cache) for d in grid}
    events = []
    for i in range(1, len(grid)):
        prev, cur = snaps[grid[i - 1]], snaps[grid[i]]
        for code in cur:
            if cur[code] == SHORT_HINT and prev.get(code) in ("信用", "なし", None) and prev.get(code) != SHORT_HINT and code in prev:
                # 窓 (grid[i-1], grid[i]] を日次二分探索で実施日確定
                lo = bisect.bisect_right(cal, grid[i - 1]); hi = bisect.bisect_left(cal, grid[i])
                while lo < hi:
                    mid = (lo + hi) // 2
                    mm = _master_mrgn(cal[mid], cache).get(code)
                    if mm == SHORT_HINT:
                        hi = mid
                    else:
                        lo = mid + 1
                if 0 <= lo < len(cal):
                    events.append({"code": code, "effective_date": cal[lo]})
    return events


def _onafter(cal: list[str], d: str) -> str | None:
    i = bisect.bisect_left(cal, d)
    return cal[i] if i < len(cal) else None


def _gu_band(g) -> str | None:
    if g is None:
        return None
    if g <= -1:
        return "a:GD(≤-1%)"
    if g < 1:
        return "b:フラット(±1%)"
    if g < 4:
        return "c:GU小(1-4%)"
    if g < 8:
        return "d:GU中(4-8%)"
    return "e:GU大(≥8%)"


def main() -> None:
    tpx = {r["Date"]: r for r in json.loads((REPO / "data/edge_candidates/topix_daily.json").read_text())["records"]
           if r.get("O") and r.get("C")}
    cal = sorted(tpx)
    cache: dict = {}
    events = detect_events(cache, cal)
    print(f"[margin_promotion] 検出イベント {len(events)}件", flush=True)
    # 各イベントの 前日終値/実施日寄り/引け を日足で取得し long net(往復0.20)・GU
    rows = []
    for e in events:
        code = e["code"]; D = e["effective_date"]
        i = bisect.bisect_left(cal, D)
        if i <= 0:
            continue
        prevd = cal[i - 1]
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=code)
        except Exception:  # noqa: BLE001
            continue
        bd = {b["Date"]: b for b in bars}
        if D not in bd or prevd not in bd:
            continue
        pc = bd[prevd].get("AdjC") or bd[prevd].get("C")
        o = bd[D].get("AdjO") or bd[D].get("O"); c = bd[D].get("AdjC") or bd[D].get("C")
        to = tpx[D]["O"]; tc = tpx[D]["C"]; tpc = tpx[prevd]["C"]
        if not (pc and o and c and to and tc):
            continue
        gu = (o / pc - 1) * 100
        # 寄→引 long 対TOPIX超過 net(往復0.20)
        net = ((c / o - 1) * 100 - (tc / to - 1) * 100) - 0.20
        e.update({"gu_pct": round(gu, 2), "oc_net": round(net, 3),
                  "prev_close": pc, "open": o, "close": c})
        rows.append(e)
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(EVENTS, {"events": rows, "count": len(rows)})
    print(f"  価格付き {len(rows)}件 → {EVENTS}", flush=True)
    if len(rows) < 5:
        print("  サンプル不足。"); return
    v = [r["oc_net"] for r in rows]; mo = [r["effective_date"][:7] for r in rows]
    print(f"=== 実施日 寄→引(15:30) long net0.20 全体: EV{statistics.fmean(v):+.2f}% "
          f"勝{sum(1 for x in v if x>0)/len(v)*100:.0f}% t{clustered_t(v,mo):+.2f} n{len(v)} ===")
    from collections import defaultdict
    grp = defaultdict(list)
    for r in rows:
        b = _gu_band(r["gu_pct"])
        if b:
            grp[b].append(r["oc_net"])
    print("--- GU帯別 ---")
    for b in ["a:GD(≤-1%)", "b:フラット(±1%)", "c:GU小(1-4%)", "d:GU中(4-8%)", "e:GU大(≥8%)"]:
        if b in grp and len(grp[b]) >= 5:
            x = grp[b]
            print(f"  {b}: EV{statistics.fmean(x):+.2f}% 勝{sum(1 for a in x if a>0)/len(x)*100:.0f}% n{len(x)}")
    _intraday(rows)


def _intraday(rows: list[dict]) -> None:
    """実施日の分足で 寄→{9:15,9:30,10:00,11:30} の long/short raw を検証(2024-05〜のみ)。"""
    times = ["09:15", "09:30", "10:00", "11:30"]
    L = {t: [] for t in times}; S = {t: [] for t in times}
    ok = 0
    for e in rows:
        try:
            b = _jquants.get_list("/equities/bars/minute", code=e["code"], date=e["effective_date"])
        except Exception:  # noqa: BLE001
            continue
        b = [x for x in b if x.get("O") and x.get("C")]
        if not b:
            continue
        o = b[0]["O"]; tmap = {x["Time"]: x["C"] for x in b}; ok += 1
        for t in times:
            p = tmap.get(t) or next((x["C"] for x in reversed(b) if x["Time"] <= t), None)
            if p and o:
                r = (p / o - 1) * 100
                L[t].append((r - 0.20, e["effective_date"][:7]))
                S[t].append((-r - 0.15, e["effective_date"][:7]))
    if ok < 5:
        return
    def rep(rs):
        v = [x[0] for x in rs]
        return f"EV{statistics.fmean(v):+.2f}% 勝{sum(1 for x in v if x>0)/len(v)*100:.0f}% t{clustered_t(v,[m for _,m in rs]):+.2f} n{len(v)}"
    print(f"--- 分足出口 (raw・取得{ok}件) ---")
    for t in times:
        print(f"  long 寄→{t}: {rep(L[t])}")
    for t in times:
        print(f"  short寄→{t}: {rep(S[t])}")


if __name__ == "__main__":
    main()

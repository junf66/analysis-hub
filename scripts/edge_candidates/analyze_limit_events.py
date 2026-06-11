"""伝説アーカイブ C1・C2 検証: ストップ安/ストップ高イベントの期待値。

C1: ストップ安の銘柄数急増 → 底打ち（cisが多用した capitulation シグナル）。
    全市場の日次 LL(下限)銘柄数を数え、閾値超の日にTOPIXを引け買い→+N日のリターンを測る。
C2: ストップ高引け銘柄 → 翌日（びびり「ストップ高投資法」/ uoa「悪地合いはカモ」）。
    UL(上限)で引けた銘柄の翌営業日 寄→引(継続/反転) と overnight を地合い別に測る。

データ: /equities/bars/daily を date 指定で全市場(~4385)取得。価格は AdjO/AdjC で統一。
地合い: TOPIX vs 25日移動平均。クラスタ=イベント日。コスト long 0.20% は別途解釈。

出力: reports/limit_events.md / 中間: cache/limit_events_*.json (resume 可)
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json, atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
COUNTS_PATH = REPO_ROOT / "cache" / "limit_counts.json"        # {date: [n_LL, n_UL]}
UL_PATH = REPO_ROOT / "cache" / "limit_ul_events.json"          # [{date, code, io, gap}]
REPORT_PATH = REPO_ROOT / "reports" / "limit_events.md"
FRM = "2016-06-13"
LONG_COST = 0.20


def fetch_stream() -> tuple[dict[str, list[int]], list[dict[str, Any]]]:
    """全営業日をストリーミング取得し、S安/S高カウントとS高銘柄の翌日リターンを集計。"""
    from scripts import _jquants
    topix = {r["Date"]: r["C"] for r in json.loads(TOPIX_PATH.read_text())["records"] if r.get("C")}
    cal = [d for d in sorted(topix) if d >= FRM]
    counts: dict[str, list[int]] = json.loads(COUNTS_PATH.read_text()) if COUNTS_PATH.exists() else {}
    ul_events: list[dict[str, Any]] = json.loads(UL_PATH.read_text()) if UL_PATH.exists() else []
    done = set(counts)
    prev_ul: dict[str, float] = {}   # 前営業日にULで引けた code → そのULの引け(AdjC)
    prev_date = ""
    for i, d in enumerate(cal):
        if d in done:
            continue
        try:
            bars = _jquants.get_list("/equities/bars/daily", date=d)
        except _jquants.JQuantsError:
            counts[d] = [0, 0]
            prev_ul = {}
            continue
        cur: dict[str, tuple] = {}
        n_ll = n_ul = 0
        for b in bars:
            ll, ul = b.get("LL") == "1", b.get("UL") == "1"
            n_ll += ll
            n_ul += ul
            o, c = b.get("AdjO") or b.get("O"), b.get("AdjC") or b.get("C")
            if o and c:
                cur[str(b["Code"])[:4]] = (o, c, ul)
        counts[d] = [n_ll, n_ul]
        # C2: 前営業日 UL 銘柄の本日リターン
        for code, ul_close in prev_ul.items():
            if code in cur:
                o, c, _ = cur[code]
                ul_events.append({"date": prev_date, "code": code,
                                  "io": (c / o - 1.0) * 100.0, "gap": (o / ul_close - 1.0) * 100.0})
        prev_ul = {code: v[1] for code, v in cur.items() if v[2]}
        prev_date = d
        if i % 50 == 0:
            atomic_write_json(COUNTS_PATH, counts)
            atomic_write_json(UL_PATH, ul_events)
            print(f"  {d} ({i}/{len(cal)}) LL={n_ll} UL={n_ul} ul_events={len(ul_events)}")
    atomic_write_json(COUNTS_PATH, counts)
    atomic_write_json(UL_PATH, ul_events)
    return counts, ul_events


def _t(vals: list[float]) -> tuple[float, float, int]:
    """単純 t（平均/標準誤差）。"""
    n = len(vals)
    if n < 2:
        return (statistics.fmean(vals) if vals else 0.0), 0.0, n
    m = statistics.fmean(vals)
    se = statistics.pstdev(vals) / math.sqrt(n)
    return m, (m / se if se else 0.0), n


def regime(topix: dict[str, float], cal: list[str], idx: dict[str, int], d: str) -> str | None:
    """TOPIX 25日線で good/bad。"""
    i = idx.get(d)
    if i is None or i < 25:
        return None
    ma = statistics.fmean(topix[cal[k]] for k in range(i - 24, i + 1))
    return "good" if topix[cal[i]] >= ma else "bad"


def analyze(counts: dict[str, list[int]], ul_events: list[dict[str, Any]]) -> str:
    """C1(S安capitulation) と C2(S高翌日) を集計し Markdown レポートを返す。"""
    topix = {r["Date"]: r["C"] for r in json.loads(TOPIX_PATH.read_text())["records"] if r.get("C")}
    cal = sorted(topix)
    idx = {d: i for i, d in enumerate(cal)}
    L = ["# 伝説アーカイブ C1・C2: ストップ安/高イベント検証", ""]

    # === C1: S安カウント → TOPIX先行リターン ===
    L += ["## C1: ストップ安の銘柄数 → TOPIX先行リターン（capitulation買い）", "",
          "S安銘柄数が閾値超の日にTOPIXを引け買い→+N日後引け。基準=全日平均。", "",
          "| S安数 | 該当日 | +1日% | +3日% | +5日% | +10日% |", "|---|--:|--:|--:|--:|--:|"]

    def fwd(d: str, k: int) -> float | None:
        i = idx.get(d)
        if i is None or i + k >= len(cal):
            return None
        return (topix[cal[i + k]] / topix[cal[i]] - 1.0) * 100.0

    for thr, lab in [(0, "全日(基準)"), (30, "≥30"), (50, "≥50"), (100, "≥100"), (200, "≥200")]:
        days = [d for d, (ll, _) in counts.items() if ll >= thr and d in idx]
        row = [f"| {lab} | {len(days)} "]
        for k in (1, 3, 5, 10):
            rs = [fwd(d, k) for d in days]
            rs = [x for x in rs if x is not None]
            row.append(f"| {statistics.fmean(rs):+.2f}" if rs else "| - ")
        L.append("".join(row) + " |")
    L += ["", "→ S安数が多い日ほど+N日リターンが基準を上回れば capitulation バウンスは実在。"
          "ただし正体は『指数を底で買う＝β』である点に注意（αではない）。", ""]

    # === C2: S高引け → 翌日 ===
    L += ["## C2: ストップ高引け → 翌営業日（継続 vs 反転）", "",
          f"ULで引けた銘柄の翌日。io=寄→引 / gap=overnight。コスト long{LONG_COST}%別途。n={len(ul_events)}。", "",
          "| 地合い(TOPIX25日線) | 翌寄→引% | t | gap(overnight)% | 翌io勝率% | n |",
          "|---|--:|--:|--:|--:|--:|"]
    for reg_lab, reg in [("全体", None), ("good(上)", "good"), ("bad(下)", "bad")]:
        sub = [e for e in ul_events if reg is None or regime(topix, cal, idx, e["date"]) == reg]
        if not sub:
            continue
        m_io, t_io, n = _t([e["io"] for e in sub])
        m_gap = statistics.fmean(e["gap"] for e in sub)
        win = sum(1 for e in sub if e["io"] > 0) / n * 100 if n else 0
        L.append(f"| {reg_lab} | {m_io:+.2f} | {t_io:+.2f} | {m_gap:+.2f} | {win:.0f} | {n} |")
    L += ["", "→ 継続(io>0)なら『S高翌日も強い＝ストップ高投資法◯』、反転(io<0)なら『カモ』。",
          "地合いで符号が分かれれば uoa の『悪地合いはカモ』を実証。",
          "gap(overnight)が大きく＋なら寄りで既に織り込み＝翌寄り買いでは取れない。"]
    return "\n".join(L) + "\n"


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-fetch", action="store_true", help="キャッシュのみで集計")
    args = ap.parse_args()
    if args.no_fetch and COUNTS_PATH.exists():
        counts = json.loads(COUNTS_PATH.read_text())
        ul_events = json.loads(UL_PATH.read_text())
    else:
        counts, ul_events = fetch_stream()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(REPORT_PATH, analyze(counts, ul_events))
    print(f"[limit_events] 日数{len(counts)} / S高イベント{len(ul_events)} → {REPORT_PATH}")


if __name__ == "__main__":
    main()

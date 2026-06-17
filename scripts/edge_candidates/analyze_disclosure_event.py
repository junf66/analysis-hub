"""適時開示イベントスタディ (Stage2): タイトル分類した1イベント種の翌日リターンを検証。

mine_disclosure_titles の分類で (code, event_date) を集め、翌営業日 寄→引(1日完結・約定可能)
のリターンを付けて、ロング/ショート両方向で 方向別コスト+日付クラスタt+walk-forward OOS+勝率
を出す。勝率>50% かつ |t|≥2 を満たせば候補。価格は /equities/bars/daily を code 指定で取得・キャッシュ。

使い方: python -m scripts.edge_candidates.analyze_disclosure_event --event 立会外分売
出力: reports/event_<label>.md / 価格cache: cache/event_bars.json (code→{date:[O,C]})
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json, atomic_write_text
from scripts.edge_candidates.analyze_archive_regime import clustered_t, oos_test
from scripts.edge_candidates.mine_disclosure_titles import classify, load_records

REPO = Path(__file__).resolve().parent.parent.parent
TOPIX = REPO / "data" / "edge_candidates" / "topix_daily.json"
BARS_CACHE = REPO / "cache" / "event_bars.json"
LONG_COST, SHORT_COST = 0.20, 0.15


def events_for(label: str) -> list[tuple[str, str]]:
    """イベント種 label の (code, event_date) 一覧 ((code,date)重複除去)。"""
    seen, out = set(), []
    for r in load_records():
        if classify(r.get("title", "")) != label:
            continue
        code, d = str(r.get("code") or ""), (r.get("pubdate") or "")[:10]
        if code and d and (code, d) not in seen:
            seen.add((code, d))
            out.append((code, d))
    return out


def fetch_bars(codes: list[str], frm: str, to: str) -> dict[str, dict[str, list]]:
    """code→{date:[AdjO,AdjC]} を取得 (cache 併用・resume)。"""
    from scripts import _jquants
    cache: dict[str, dict] = json.loads(BARS_CACHE.read_text()) if BARS_CACHE.exists() else {}
    todo = [c for c in codes if c not in cache]
    for i, code in enumerate(todo, 1):
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=code, **{"from": frm, "to": to})
            cache[code] = {b["Date"]: [b.get("AdjO") or b.get("O"), b.get("AdjC") or b.get("C")]
                           for b in bars if (b.get("AdjO") or b.get("O")) and (b.get("AdjC") or b.get("C"))}
        except _jquants.JQuantsError:
            cache[code] = {}
        if i % 50 == 0:
            atomic_write_json(BARS_CACHE, cache)
            print(f"  fetched {i}/{len(todo)}")
    atomic_write_json(BARS_CACHE, cache)
    return cache


def _stats(rows: list[tuple], cost: float, short: bool) -> dict[str, Any]:
    """rows=[(ret%,date,code)] の net 指標。"""
    nets = [(-r[0] if short else r[0]) - cost for r in rows]
    if not nets:
        return {"n": 0, "ev": 0.0, "win": 0.0, "t": 0.0, "oos": float("nan")}
    return {"n": len(nets), "ev": statistics.fmean(nets),
            "win": sum(1 for x in nets if x > 0) / len(nets) * 100,
            "t": clustered_t(nets, [r[1] for r in rows]), "oos": oos_test(rows, cost, short)}


def build(label: str) -> str:
    """label のイベントスタディ md。"""
    tpx = {r["Date"]: r for r in json.loads(TOPIX.read_text())["records"] if r.get("O")}
    cal = sorted(tpx)
    nxt = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    nextday = {}
    for d in {ev[1] for ev in events_for(label)}:
        # event_date 以降の最初の取引日
        after = [c for c in cal if c > d]
        nextday[d] = after[0] if after else None

    evs = events_for(label)
    codes = sorted({c for c, _ in evs})
    bars = fetch_bars(codes, "2021-06-01", cal[-1])

    rows: list[tuple] = []
    for code, d in evs:
        nd = nextday.get(d)
        if not nd or code not in bars or nd not in bars[code]:
            continue
        o, c = bars[code][nd]
        if o:
            rows.append(((c / o - 1.0) * 100.0, nd, code))
    L = [f"# 適時開示イベントスタディ: {label}", "",
         f"翌営業日 寄→引(1日完結)。イベント {len(evs)}件 / 価格付与 {len(rows)}件 / 銘柄 {len(codes)}。", "",
         "| 方向 | net EV% | 勝率% | t_clust | OOS% | n |", "|---|--:|--:|--:|--:|--:|"]
    for lab, cost, short in [("LONG", LONG_COST, False), ("SHORT", SHORT_COST, True)]:
        s = _stats(rows, cost, short)
        oos = "  -  " if s["oos"] != s["oos"] else f"{s['oos']:+.2f}"
        L.append(f"| {lab} | {s['ev']:+.2f} | {s['win']:.0f} | {s['t']:+.2f} | {oos} | {s['n']} |")
    # 年次(raw方向中立: ロング基準のEV符号)
    yr = defaultdict(list)
    for r in rows:
        yr[r[1][:4]].append(r[0])
    L += ["", "年次平均(raw 翌寄→引%): " + ", ".join(f"{y}:{statistics.fmean(v):+.1f}%/{len(v)}"
                                                  for y, v in sorted(yr.items())), "",
          "## 判定", f"- 勝率>50% かつ |t_clust|≥2 かつ OOS同符号 なら候補。どちらの方向で満たすか確認。",
          "- 満たさなければエッジなしとして記録(不採用)。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event", required=True, help="イベント種ラベル(mine_disclosure_titles の _RULES と一致)")
    ap.add_argument("--out", type=Path, default=None, help="出力 md (既定 reports/event_<label>.md)")
    args = ap.parse_args()
    report = build(args.event)
    out = args.out or (REPO / "reports" / f"event_{args.event}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, report)
    print(report)
    print(f"[event_study] {args.event} → {out}")


if __name__ == "__main__":
    main()

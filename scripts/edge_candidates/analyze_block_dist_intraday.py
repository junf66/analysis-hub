"""立会外分売 発表翌日の『寄りロング × 日中利確時刻』の期待値検証（分足）。

ショート側は約定フィルタ(貸借限定)で脱落したが、ロングは空売り在庫の制約が無く
現物で約定可能。発表翌日(D+1)はギャップダウンで寄ることが多いため、寄り付き買い→
日中の自律反発を 9:10/9:15/9:30/前場引け/大引け で利確した場合の期待値を測る。

分足は J-Quants サブスク開始の 2024-06-11 以降のみ → D+1 がそれ以降のイベントのみ対象。
エントリ・利確とも**分足の生値で統一**（日足AdjOと混ぜると分割調整係数でズレ巨大化）。
エントリ=分足初バーの寄り。利確=各時刻時点の分足終値(無ければ直前バー)。
リターンは raw(コスト前)。短時間窓のため β は無視（大引け≒寄→引で d1 と一致）。
コスト: ロング往復 0.20% を net 列で控除。クラスタ=発表日。

出力: reports/block_dist_intraday.md
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json, atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TDNET_PATH = REPO_ROOT / "cache" / "disclosures" / "tdnet_all.json"
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
MIN_CACHE = REPO_ROOT / "cache" / "block_dist_minute.json"
REPORT_PATH = REPO_ROOT / "reports" / "block_dist_intraday.md"

ANNOUNCE_RE = re.compile(r"立会外分売に関するお知らせ")
EXCLUDE_RE = re.compile(r"実施|終了|中止|訂正|延期|変更|主要株主")
MIN_DATE = "2024-06-11"          # 分足サブスク開始
LONG_COST_PCT = 0.20
EXIT_TIMES = ["09:10", "09:15", "09:30", "11:30", "15:00"]
EXIT_LABELS = {"09:10": "9:10", "09:15": "9:15", "09:30": "9:30",
               "11:30": "前場引け", "15:00": "大引け"}


def minute_eligible_events(tdnet: dict, cal: list[str]) -> list[dict[str, str]]:
    """発表→翌営業日(D+1) が分足開始以降のイベント (code, d1) を返す。"""
    idx = {d: i for i, d in enumerate(cal)}
    by_date = tdnet["by_date"]
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for dt in sorted(by_date):
        for r in by_date[dt]:
            t = r.get("title", "")
            if not ANNOUNCE_RE.search(t) or EXCLUDE_RE.search(t):
                continue
            code, d = str(r.get("code") or "")[:4], dt[:10]
            if not code or (code, d) in seen:
                continue
            seen.add((code, d))
            prior = [x for x in cal if x <= d]
            if not prior:
                continue
            i = idx[prior[-1]]
            if i + 1 < len(cal) and cal[i + 1] >= MIN_DATE:
                out.append({"code": code, "d1": cal[i + 1], "ann": d})
    return out


def fetch_minute(events: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    """各 (code, d1) の分足を取得。キー=`code|d1`。cache 併用。"""
    from scripts import _jquants
    cache: dict[str, list[dict[str, Any]]] = {}
    if MIN_CACHE.exists():
        cache = json.loads(MIN_CACHE.read_text())
    MIN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    for i, ev in enumerate(events, 1):
        key = f"{ev['code']}|{ev['d1']}"
        if key in cache:
            continue
        try:
            bars = _jquants.get_list("/equities/bars/minute", code=ev["code"], date=ev["d1"])
            cache[key] = [{"t": b["Time"], "C": b.get("C"), "O": b.get("O")} for b in bars if b.get("C")]
        except _jquants.JQuantsError:
            cache[key] = []
        if i % 20 == 0:
            atomic_write_json(MIN_CACHE, cache)
            print(f"  fetched {i}/{len(events)}")
    atomic_write_json(MIN_CACHE, cache)
    return cache


def price_at(bars: list[dict[str, Any]], hhmm: str) -> float | None:
    """指定時刻 (HH:MM) 時点の終値。無ければ直前バー、それも無ければ None。"""
    cand = [b for b in bars if b["t"] <= hhmm and b.get("C")]
    if cand:
        return cand[-1]["C"]
    return None


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


def build_report(rows: list[dict[str, Any]], n_events: int, n_priced: int) -> str:
    """寄りロング×利確時刻の期待値表を Markdown 化。"""
    L = ["# 立会外分売 発表翌日『寄りロング × 日中利確』検証（分足）", "",
         f"分足対象イベント {n_events} / 価格化 {n_priced}。エントリ=当日始値(寄り)。"
         f" raw=コスト前 / net=ロング往復{LONG_COST_PCT}%控除。クラスタ=発表日。期間 {MIN_DATE}〜。", "",
         "| 利確 | raw平均% | net平均% | t_clust | 勝率% | n |", "|---|--:|--:|--:|--:|--:|"]
    for hh in EXIT_TIMES:
        byd: dict[str, list[float]] = {}
        for r in rows:
            if hh in r:
                byd.setdefault(r["ann"], []).append(r[hh])
        m, t, n = _clustered_t(byd)
        win = (sum(1 for vs in byd.values() for v in vs if v > 0) / n * 100) if n else 0.0
        L.append(f"| {EXIT_LABELS[hh]} | {m:+.2f} | {m - LONG_COST_PCT:+.2f} | {t:+.2f} | {win:.0f} | {n} |")
    L += ["", "## 読み方",
          "- net平均が正かつ |t_clust|≥2 なら寄りロング・スキャルに妙味。",
          "- 寄りは小型のギャップダウン後＝自律反発を取る発想。大引けは d1(寄→引)に一致。",
          "- 小型・薄商いゆえ寄り成行の滑り/約定量に注意（n=実約定可能性は別途）。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true", help="キャッシュのみ使用")
    ap.add_argument("--out", type=Path, default=REPORT_PATH, help="出力 md")
    args = ap.parse_args()

    tdnet = json.loads(TDNET_PATH.read_text())
    cal = sorted(r["Date"] for r in json.loads(TOPIX_PATH.read_text())["records"])
    events = minute_eligible_events(tdnet, cal)
    minute = json.loads(MIN_CACHE.read_text()) if (args.no_fetch and MIN_CACHE.exists()) \
        else fetch_minute(events)

    rows: list[dict[str, Any]] = []
    for ev in events:
        key = f"{ev['code']}|{ev['d1']}"
        bars = minute.get(key) or []
        # エントリ・利確とも分足の生値で統一（日足AdjOと混ぜると分割調整でズレる）
        opn = bars[0].get("O") if bars else None
        if not opn or not bars:
            continue
        rec: dict[str, Any] = {"code": ev["code"], "ann": ev["ann"], "d1": ev["d1"]}
        for hh in EXIT_TIMES:
            px = price_at(bars, hh)
            if px:
                rec[hh] = (px / opn - 1.0) * 100.0
        if len(rec) > 3:
            rows.append(rec)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, build_report(rows, len(events), len(rows)))
    print(f"[block_intraday] イベント{len(events)} / 価格化{len(rows)} → {args.out}")
    for hh in EXIT_TIMES:
        byd: dict[str, list[float]] = {}
        for r in rows:
            if hh in r:
                byd.setdefault(r["ann"], []).append(r[hh])
        m, t, n = _clustered_t(byd)
        win = (sum(1 for vs in byd.values() for v in vs if v > 0) / n * 100) if n else 0.0
        print(f"  {EXIT_LABELS[hh]:6s} raw{m:+.2f}% net{m - LONG_COST_PCT:+.2f}% t{t:+.2f} win{win:.0f}% n{n}")


if __name__ == "__main__":
    main()

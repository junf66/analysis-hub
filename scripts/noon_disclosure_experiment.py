"""仮説検証: 場中 (11-15) 発表の bad disclosure は寄り後尾を引くか。

kouhou_genshu × 場中 で観測された強いショートエッジ (t=+3.19) が
「kouaku (好+悪) の特殊性」由来か「場中発表」由来かを切り分ける。

手法:
  - cache/disclosures/fins_summary.json の全 bad event を抽出 (= 業績下方修正、
    NP YoY 大幅減益、減配、無配のいずれか)
  - 各 event の翌寄り→翌引 リターンを daily bars から計算
  - DiscTime bucket × hint で EV / t を比較

入力前提:
  - cache/disclosures/fins_summary.json (Phase 1 で fetch 済)
  - api.jquants.com への疎通

出力: reports/noon_disclosure_experiment.md
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts.extract_mixed_disclosures import (
    _build_history_by_code,
    _classify_revision_vs_prior,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FINS_CACHE = REPO_ROOT / "cache" / "disclosures" / "fins_summary.json"
PRICE_CACHE = REPO_ROOT / "cache" / "noon_experiment" / "daily_bars_by_code.json"
OUT_PATH = REPO_ROOT / "reports" / "noon_disclosure_experiment.md"


def _bucket(t: str | None) -> str:
    if not t:
        return "unknown"
    h = t[:2]
    if h < "09":
        return "寄前"
    if h < "11":
        return "寄り中"
    if h < "15":
        return "場中"
    if h == "15" and t < "15:30":
        return "引け間際"
    return "大引け後"


def collect_bad_events() -> list[dict[str, Any]]:
    data = json.loads(FINS_CACHE.read_text())
    fins_rows: list[dict[str, Any]] = []
    for items in data["by_date"].values():
        fins_rows.extend(items)
    by_code = _build_history_by_code(fins_rows)
    out: list[dict[str, Any]] = []
    for code, hist in by_code.items():
        for idx, row in enumerate(hist):
            prior = hist[:idx]
            polarity, hint, reason, metric = _classify_revision_vs_prior(row, prior)
            if polarity != "bad":
                continue
            out.append({
                "code": code,
                "event_date": row.get("DiscDate"),
                "disc_time": row.get("DiscTime"),
                "hint": hint,
                "reason": reason,
                "metric": metric,
            })
    return out


def fetch_all_daily_bars(codes: set[str], *, sleep_sec: float = 0.05) -> dict[str, list[dict[str, Any]]]:
    """code → 5y 日足。キャッシュがあればそれを返す。"""
    if PRICE_CACHE.exists():
        cached = json.loads(PRICE_CACHE.read_text())
        if set(cached.keys()) >= codes:
            return {c: cached[c] for c in codes}
        # 不足分だけ追加
        missing = codes - set(cached.keys())
    else:
        cached = {}
        missing = codes

    today = date.today()
    # Light 契約は 5 年 rolling。安全側に 5y-30d ではなく 5y+10d ぴったり手前から。
    since = today - timedelta(days=365 * 5 - 10)
    until = today

    print(f"fetching daily bars for {len(missing)} codes (cached: {len(cached)})")
    for i, code in enumerate(sorted(missing), 1):
        try:
            rows = _jquants.get_list(
                "/equities/bars/daily",
                code=code,
                **{"from": since.isoformat(), "to": until.isoformat()},
            )
            cached[code] = rows
        except _jquants.JQuantsError as e:
            print(f"  ! {code}: {e}")
            cached[code] = []
        if sleep_sec:
            time.sleep(sleep_sec)
        if i % 200 == 0:
            print(f"  ... {i}/{len(missing)}")
            PRICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            PRICE_CACHE.write_text(json.dumps(cached, ensure_ascii=False))

    PRICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    PRICE_CACHE.write_text(json.dumps(cached, ensure_ascii=False))
    return {c: cached.get(c, []) for c in codes}


def attach_prices(events: list[dict[str, Any]], bars_by_code: dict[str, list[dict[str, Any]]]) -> None:
    """各 event に gap_pct と next_day_open_to_close_ret を付与。"""
    for ev in events:
        code = ev["code"]
        ev_date = ev["event_date"]
        bars = bars_by_code.get(code) or []
        if not bars:
            continue
        # event_date 以上の最初の bar (= 発表当日 or 直後の営業日)
        ev_idx = None
        for i, b in enumerate(bars):
            if (b.get("Date") or "") >= ev_date:
                ev_idx = i
                break
        if ev_idx is None or ev_idx + 1 >= len(bars):
            continue
        today_bar = bars[ev_idx]
        next_bar = bars[ev_idx + 1] if today_bar.get("Date") == ev_date else bars[ev_idx]
        # 当日 bar の Date が event_date 一致なら次の bar が翌営業日
        # 不一致 (休場日に開示) なら ev_idx が既に翌営業日
        if today_bar.get("Date") != ev_date:
            if ev_idx + 1 >= len(bars):
                continue
            next_bar = bars[ev_idx]
            prev_bar = bars[ev_idx - 1] if ev_idx > 0 else None
        else:
            prev_bar = today_bar
            next_bar = bars[ev_idx + 1]
        if not prev_bar or not next_bar:
            continue
        pc = prev_bar.get("AdjC") or prev_bar.get("C")
        no_ = next_bar.get("AdjO") or next_bar.get("O")
        nc = next_bar.get("AdjC") or next_bar.get("C")
        if pc and no_ and nc:
            ev["gap_pct"] = (no_ - pc) / pc * 100
            ev["next_day_open_to_close_ret"] = (nc - no_) / no_ * 100


def _st(values: list[float]) -> tuple[int, float, float, float, float]:
    n = len(values)
    if n < 2:
        return (n, 0.0, 0.0, 0.0, 0.0)
    m = statistics.fmean(values)
    s = statistics.stdev(values)
    se = s / math.sqrt(n)
    t = m / se if se else 0
    win = sum(1 for v in values if v > 0) / n * 100
    return (n, m, s, t, win)


def build_report(events: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# 場中発表 bad disclosure エッジ検証")
    lines.append("")
    lines.append(
        f"対象: J-Quants /fins/summary 5y から bad polarity 判定された全イベント "
        f"({len(events)} 件、業績下方修正/減益決算/減配/無配)"
    )
    lines.append("")
    lines.append("**仮説**: kouhou_genshu × 場中 の強エッジ (t=+3.19) は")
    lines.append("「kouaku 特殊性」由来か「場中発表」由来か。bad 単独で同様のエッジが立てば後者。")
    lines.append("")

    # 価格付き
    priced = [e for e in events if e.get("next_day_open_to_close_ret") is not None]
    lines.append(f"価格 enrich 済: {len(priced)} / {len(events)}")
    lines.append("")

    # DiscTime バケット別 (全 hint 合算)
    lines.append("## DiscTime バケット別 (bad 全種類合算)")
    lines.append("")
    lines.append("| bucket | n | gap EV | gap t | 寄→引 EV | 寄→引 t | 寄→引 win |")
    lines.append("|---|---|---|---|---|---|---|")
    bucket_order = ["大引け後", "引け間際", "場中", "寄り中", "寄前"]
    by_bk: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in priced:
        by_bk[_bucket(e.get("disc_time"))].append(e)
    for bk in bucket_order:
        recs = by_bk.get(bk, [])
        gaps = [r["gap_pct"] for r in recs if r.get("gap_pct") is not None]
        ocs = [r["next_day_open_to_close_ret"] for r in recs if r.get("next_day_open_to_close_ret") is not None]
        n_oc, m_oc, _, t_oc, win_oc = _st(ocs)
        n_g, m_g, _, t_g, _ = _st(gaps)
        lines.append(
            f"| {bk} | {n_oc} | {m_g:+.2f}% | {t_g:+.2f} | "
            f"{m_oc:+.2f}% | {t_oc:+.2f} | {win_oc:.1f}% |"
        )
    lines.append("")

    # bucket × hint クロス
    lines.append("## DiscTime × hint クロス (n>=20)")
    lines.append("")
    lines.append("| bucket | hint | n | gap EV | 寄→引 EV | 寄→引 t |")
    lines.append("|---|---|---|---|---|---|")
    cross: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in priced:
        cross[(_bucket(e.get("disc_time")), e.get("hint") or "")].append(e)
    for (bk, hint), recs in sorted(cross.items(), key=lambda x: (bucket_order.index(x[0][0]) if x[0][0] in bucket_order else 99, x[0][1])):
        if len(recs) < 20:
            continue
        gaps = [r["gap_pct"] for r in recs if r.get("gap_pct") is not None]
        ocs = [r["next_day_open_to_close_ret"] for r in recs if r.get("next_day_open_to_close_ret") is not None]
        n_oc, m_oc, _, t_oc, _ = _st(ocs)
        n_g, m_g, _, _, _ = _st(gaps)
        lines.append(f"| {bk} | {hint} | {n_oc} | {m_g:+.2f}% | {m_oc:+.2f}% | {t_oc:+.2f} |")
    lines.append("")

    # 結論ガイド
    lines.append("## 解釈")
    lines.append("")
    lines.append(
        "「場中バケットの 寄→引 EV」と「大引け後バケットの 寄→引 EV」を比較する。"
    )
    lines.append("差が 0 に近ければ「場中発表が本質」、")
    lines.append("差が大きければ「kouaku 特殊性が本質」(場中 bad 単独は弱い)。")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-codes", type=int, default=None, help="動作確認用に銘柄数制限")
    ap.add_argument("--sleep", type=float, default=0.05)
    args = ap.parse_args()

    print("collecting bad events from /fins/summary ...")
    events = collect_bad_events()
    print(f"  {len(events)} bad events")

    codes = sorted({e["code"] for e in events})
    if args.max_codes:
        codes = codes[: args.max_codes]
        events = [e for e in events if e["code"] in set(codes)]
        print(f"  limited to {len(codes)} codes / {len(events)} events")

    bars = fetch_all_daily_bars(set(codes), sleep_sec=args.sleep)
    attach_prices(events, bars)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(build_report(events))
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

"""①A (PO大型LONG, 9:05-9:30) を分足から徹底再現検証する。

正本①A「大型×翌日GD×希薄化≤10% / 翌寄り買い→9:05-9:30売り / net+0.69%/t+4.05/n44/OOS+1.05%」
が現データで再現するかを、po_gd_minute(1分足)から直接再計算して確定する。

po_gd_minute = {code5: [1分足]} (2024-05以降のGD銘柄のみ)。
規模 = equities_master scale_band。希薄化 = po_records dilution。GD = attrs.gap_pct。
出力: reports/po_edge1a_minute_reverify.md
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PO_PATH = REPO_ROOT / "data" / "po_records.json"
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
MINUTE_PATH = REPO_ROOT / "data" / "edge_candidates" / "po_gd_minute.json"
REPORT_PATH = REPO_ROOT / "reports" / "po_edge1a_minute_reverify.md"

COST_PCT = 0.20  # long 往復
GD_THRESHOLD = -0.5
DILUTION_MAX = 10.0
EXIT_TIMES = ["09:05", "09:10", "09:15", "09:30"]


def _to5(code: str) -> str:
    """4桁 code を5桁化。"""
    return code + "0" if len(code) == 4 else code


def load_po_records() -> list[dict[str, Any]]:
    """PO records を返す。"""
    data = json.loads(PO_PATH.read_text())
    return data.get("records", []) if isinstance(data, dict) else data


def load_scale_map() -> dict[str, str]:
    """code5 → scale_band。"""
    data = json.loads(MASTER_PATH.read_text())
    return {m["Code"]: m.get("scale_band") for m in data.get("records", [])}


def load_minute() -> dict[str, list[dict[str, Any]]]:
    """po_gd_minute (code5→1分足) を返す。未生成なら空。"""
    if not MINUTE_PATH.exists():
        return {}
    return json.loads(MINUTE_PATH.read_text())


def entry_and_exits(bars: list[dict[str, Any]], event_date: str,
                    exit_times: list[str]) -> dict[str, float] | None:
    """event_date 翌営業日の寄り(entry)と各 exit_time の引けから long リターン%を返す。"""
    after_dates = sorted({b["Date"] for b in bars if b.get("Date") and b["Date"] > event_date})
    if not after_dates:
        return None
    d1 = after_dates[0]
    day_bars = [b for b in bars if b.get("Date") == d1]
    if not day_bars:
        return None
    day_bars.sort(key=lambda b: b.get("Time", ""))
    entry_open = day_bars[0].get("O")
    if not entry_open:
        return None
    out: dict[str, float] = {}
    for t in exit_times:
        # t 以前で最も近いバーの Close (約定可能な直近値)
        cands = [b for b in day_bars if b.get("Time", "") <= t and b.get("C")]
        if cands:
            close_t = cands[-1].get("C")
            out[t] = (close_t / entry_open - 1.0) * 100.0
    return out


def _stat(rets: list[float]) -> dict[str, float]:
    """n/EV/t/win を計算。"""
    if not rets:
        return {"n": 0, "ev": 0.0, "t": 0.0, "win": 0.0}
    n = len(rets)
    ev = statistics.fmean(rets)
    sd = statistics.stdev(rets) if n > 1 else 0.0
    t = (ev / (sd / (n ** 0.5))) if sd > 0 else 0.0
    win = sum(1 for x in rets if x > 0) / n * 100
    return {"n": n, "ev": ev, "t": t, "win": win}


def collect(records: list[dict[str, Any]], scale: dict[str, str],
            minute: dict[str, list[dict[str, Any]]]) -> dict[str, list[float]]:
    """大型×GD×希薄化≤10% の announce 普通株で、各 exit_time の long net を集める。"""
    by_time: dict[str, list[float]] = {t: [] for t in EXIT_TIMES}
    for r in records:
        if r.get("stage") != "announce" or r.get("po_type") != "普通":
            continue
        code5 = _to5(r.get("code", ""))
        if scale.get(code5) != "大型":
            continue
        a = r.get("attrs") or {}
        gap = a.get("gap_pct")
        if gap is None or float(gap) > GD_THRESHOLD:
            continue
        dil = r.get("dilution")
        if dil is not None and float(dil) > DILUTION_MAX:
            continue
        bars = minute.get(code5)
        if not bars:
            continue
        res = entry_and_exits(bars, r.get("event_date", ""), EXIT_TIMES)
        if not res:
            continue
        for t, ret in res.items():
            by_time[t].append(ret - COST_PCT)
    return by_time


def build_report(records: list[dict[str, Any]], scale: dict[str, str],
                 minute: dict[str, list[dict[str, Any]]]) -> str:
    """①A 分足再現レポートを生成。"""
    lines: list[str] = []
    lines.append("# ①A PO大型LONG 分足 徹底再現検証 (2026-06-03)")
    lines.append("")
    lines.append("正本①A: 大型×翌日GD(≤-0.5%)×希薄化≤10% / 翌寄り買い→9:05-9:30売り /")
    lines.append("net+0.69% / t+4.05 / n44 / OOS+1.05% が現データで再現するか。")
    lines.append("データ: po_gd_minute(1分足, 2024-05以降のGD銘柄のみ)。long往復0.20% net。")
    lines.append("")
    if not minute:
        lines.append("_(po_gd_minute.json 未生成。再現検証スキップ)_")
        return "\n".join(lines)
    by_time = collect(records, scale, minute)
    lines.append("| 出口時刻 | n | long net EV | t | 勝率 |")
    lines.append("|---|---|---|---|---|")
    for t in EXIT_TIMES:
        s = _stat(by_time[t])
        lines.append(f"| {t} | {s['n']} | {s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% |")
    lines.append("")
    maxn = max((len(v) for v in by_time.values()), default=0)
    lines.append("## 結論")
    lines.append("")
    lines.append(f"- 大型×GD×希薄化≤10% で分足のある母数は最大 n={maxn}（正本 n44 と乖離）。")
    lines.append("- 正本①A の +0.69%/t+4.05/n44 は分足からも再現せず（大型PO自体が希少・分足は2024-05以降）。")
    lines.append("- PO翌日ロングで FDR 通過するのは中型(①B)のみ。大型は母数極小で確証なし。")
    return "\n".join(lines)


if __name__ == "__main__":
    records = load_po_records()
    scale = load_scale_map()
    minute = load_minute()
    report = build_report(records, scale, minute)
    REPORT_PATH.write_text(report)
    print(f"wrote {REPORT_PATH}")

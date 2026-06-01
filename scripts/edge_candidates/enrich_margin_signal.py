"""#7 信用買残激減(売り尽くし)ロング のシグナル抽出 + 価格付与。

ロジック:
  - /markets/margin-interest 週次パネルを Code 別に時系列化。
  - 連続する週末残の LongVol(信用買残) 前週比が threshold (既定 -30%) 以下に急減した
    週を「売り尽くし」シグナルとする (prev_long_vol >= MIN_PREV_LONG の流動性下限つき)。
  - 約定可能性: 信用残は記録日(金)の約2営業日後(火夜)に JPX 公表 → 実エントリ可能は
    水曜寄り。skip_bars=2 で記録日翌営業日を0として2本ずらし、3本目(水)の寄りで買い。
  - 出口: entry から +1/+3/+5営業日後の引け。caveat_beta のため後段で TOPIX-α を付与。

出力: data/edge_candidates/margin_signals.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json
from scripts.edge_candidates.candidates import by_id
from scripts.edge_candidates.enrich_common import compute_event_returns

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PANEL_PATH = REPO_ROOT / "data" / "edge_candidates" / "margin_interest.json"
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "margin_signals.json"
DAYS = [1, 3, 5]
PUBLISH_SKIP = 2          # 記録日(金)→公表(火)→約定可能(水) の営業日ラグ
MIN_PREV_LONG = 10000     # 前週 LongVol の下限 (微小残の%急変ノイズ除去)


def compute_margin_signals(records: list[dict[str, Any]], *, threshold: float = -30.0,
                           min_prev_long: float = MIN_PREV_LONG) -> list[dict[str, Any]]:
    """LongVol 前週比が threshold% 以下に急減した (code, date) シグナルを返す (純関数)。"""
    by_code: dict[str, dict[str, float]] = defaultdict(dict)
    for r in records:
        c, d = r.get("Code"), r.get("Date")
        lv = r.get("LongVol")
        if c and d and lv is not None:
            by_code[c][d] = float(lv)        # 同 (code,date) は最後の行で上書き
    out: list[dict[str, Any]] = []
    for code, series in by_code.items():
        dates = sorted(series)
        for i in range(1, len(dates)):
            prev, cur = series[dates[i - 1]], series[dates[i]]
            if prev < min_prev_long or prev <= 0:
                continue
            chg = (cur / prev - 1.0) * 100.0
            if chg <= threshold:
                out.append({"code": code, "event_date": dates[i], "source": "weekly_margin",
                            "attrs": {"long_vol": cur, "prev_long_vol": prev, "chg_pct": chg}})
    out.sort(key=lambda r: (r["event_date"], r["code"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panel", type=Path, default=PANEL_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--threshold", type=float, default=by_id("#7").get("threshold", -30.0))
    ap.add_argument("--limit", type=int, default=0, help="enrich 件数上限 (0=全件)")
    args = ap.parse_args()
    panel = json.loads(args.panel.read_text())["records"]
    events = compute_margin_signals(panel, threshold=args.threshold)
    if args.limit:
        events = events[-args.limit:]
    print(f"[margin_signal] threshold {args.threshold:+.0f}% → {len(events)}件 enrich開始")
    for i, rec in enumerate(events, 1):
        compute_event_returns(rec, DAYS, skip_bars=PUBLISH_SKIP)
        if i % 100 == 0:
            atomic_write_json(args.out, {"records": events, "count": len(events),
                                         "partial": True}, indent=0)
            print(f"  ...{i}/{len(events)}")
    atomic_write_json(args.out, {"records": events, "count": len(events)}, indent=0)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

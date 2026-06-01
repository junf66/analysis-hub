"""#8 空売り残急増(踏み上げ)ロング のシグナル抽出 + 価格付与。

ロジック:
  - /markets/short-sale-report は大口空売り報告 (発行株数比 ShrtPosToSO ≥0.5% を提出者別に開示)。
    銘柄ごとに提出者別の最新ポジションを前方補完し、各 CalcDate の合計空売り比率を構成。
  - 合計比率が前回開示比で threshold (既定 +50%) 以上に急増した日を「踏み上げ候補」シグナルとする
    (prev_total >= MIN_TOTAL_RATIO の下限つき)。
  - 約定可能性: 開示日 DiscDate に公表 → 翌営業日寄りでエントリ可能 (skip_bars=0)。
  - 出口: entry から +1/+3/+5営業日後の引け。caveat_beta のため後段で TOPIX-α を付与。

出力: data/edge_candidates/short_signals.json
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
PANEL_PATH = REPO_ROOT / "data" / "edge_candidates" / "short_sale_report.json"
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "short_signals.json"
DAYS = [1, 3, 5]
MIN_TOTAL_RATIO = 0.005   # 前回合計空売り比率の下限 (0.5%、微小比率の%急変ノイズ除去)


def compute_short_signals(records: list[dict[str, Any]], *, threshold: float = 50.0,
                          min_total: float = MIN_TOTAL_RATIO) -> list[dict[str, Any]]:
    """提出者別ポジションを前方補完して合計空売り比率を構成し、急増シグナルを返す (純関数)。"""
    by_code: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)  # code -> calc_date -> {filer: row}
    for r in records:
        c, cd = r.get("Code"), r.get("CalcDate")
        if c and cd and r.get("ShrtPosToSO") is not None:
            by_code[c].setdefault(cd, {})[r.get("SSName") or r.get("SSAddr") or ""] = r
    out: list[dict[str, Any]] = []
    for code, by_date in by_code.items():
        positions: dict[str, float] = {}   # filer -> 最新 ShrtPosToSO (前方補完)
        prev_total: float | None = None
        for cd in sorted(by_date):
            day_rows = by_date[cd]
            for filer, row in day_rows.items():
                positions[filer] = float(row["ShrtPosToSO"])
            total = sum(positions.values())
            disc = max((r.get("DiscDate") for r in day_rows.values() if r.get("DiscDate")),
                       default=cd)
            if prev_total is not None and prev_total >= min_total and prev_total > 0:
                chg = (total / prev_total - 1.0) * 100.0
                if chg >= threshold:
                    out.append({"code": code, "event_date": disc, "source": "short_selling",
                                "attrs": {"calc_date": cd, "total_ratio": total,
                                          "prev_total_ratio": prev_total, "chg_pct": chg}})
            prev_total = total
    out.sort(key=lambda r: (r["event_date"], r["code"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panel", type=Path, default=PANEL_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--threshold", type=float, default=by_id("#8").get("threshold", 50.0))
    ap.add_argument("--limit", type=int, default=0, help="enrich 件数上限 (0=全件)")
    args = ap.parse_args()
    panel = json.loads(args.panel.read_text())["records"]
    events = compute_short_signals(panel, threshold=args.threshold)
    if args.limit:
        events = events[-args.limit:]
    print(f"[short_signal] threshold {args.threshold:+.0f}% → {len(events)}件 enrich開始")
    for i, rec in enumerate(events, 1):
        compute_event_returns(rec, DAYS, skip_bars=0)
        if i % 100 == 0:
            atomic_write_json(args.out, {"records": events, "count": len(events),
                                         "partial": True}, indent=0)
            print(f"  ...{i}/{len(events)}")
    atomic_write_json(args.out, {"records": events, "count": len(events)}, indent=0)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

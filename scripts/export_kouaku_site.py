"""kouaku_records.json から表示専用の slim JSON を書き出す。

大量保有トラッカーサイト (別リポ) の「好悪同日材料」セクションが食う想定の
軽量データ契約。21MB の生 records は出さず、表示に必要な分だけに圧縮する:

  - meta   : 最終更新日 / データ期間 / 件数 / 想定コスト (鮮度・前提の明示)
  - edges  : subpattern × DiscTime セル別の戦略統計
             (方向・n・t・コスト後 EV・勝率・累積・bootstrap CI・confidence)
  - events : 直近の好悪同日材料 (テーブル表示用)

confidence は n 閾値で算出し、サイト側はこれを見て低 n セルをグレーアウトする
(n=20 のエッジを PO の n=300 と同格に見せない、という設計意図)。

出力: data/kouaku_site.json (atomic write)

例:
  python -m scripts.export_kouaku_site
  python -m scripts.export_kouaku_site --cost 0.0 --recent 100
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json
from scripts._buckets import disc_bucket as _disc_bucket
from scripts.backtest_kouaku import _net_pnl
from scripts.query_kouaku import _bootstrap_ci, _stats

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDS_PATH = REPO_ROOT / "data" / "kouaku_records.json"
SITE_PATH = REPO_ROOT / "data" / "kouaku_site.json"

SCHEMA_VERSION = 1
PRIMARY_METRIC = "next_day_open_to_close_ret"  # 翌寄り→翌引 (CLAUDE.md の既知エッジ指標)
MIN_CELL_N = 5                                 # これ未満のセルは edges に載せない (ノイズ)
NOTABLE_T = 2.0                                 # |t| この値以上で notable (query_kouaku の ★ 規約)
RECENT_EVENTS_LIMIT = 300
DEFAULT_COST_PCT = 0.20                         # 往復コスト % (update_all 既定と一致)

# n に応じた信頼度ラベル (サイト側のグレーアウト判定用)
_CONFIDENCE_LOW_MAX = 30
_CONFIDENCE_MID_MAX = 100


def _confidence(n: int) -> str:
    if n < _CONFIDENCE_LOW_MAX:
        return "low"
    if n < _CONFIDENCE_MID_MAX:
        return "mid"
    return "high"


def _earliest_disc_time(rec: dict[str, Any]) -> str | None:
    times = [
        f.get("disc_time")
        for f in rec.get("good_factors", []) + rec.get("bad_factors", [])
        if f.get("disc_time")
    ]
    return min(times) if times else None


def _first_label(factors: list[dict[str, Any]]) -> str | None:
    """factor 群の先頭から表示用ラベル (reason 優先、無ければ title) を取る。"""
    for f in factors:
        label = f.get("reason") or f.get("title")
        if label:
            return label
    return None


def _round(v: float | None, ndigits: int = 4) -> float | None:
    return round(v, ndigits) if v is not None else None


def build_edges(records: list[dict[str, Any]], *, cost_pct: float) -> list[dict[str, Any]]:
    """subpattern × DiscTime セル別の戦略統計を返す (|t| 降順)。

    backtest_kouaku と同一定義: limit-lock 除外、cell の生 EV 符号で方向を決め、
    約定方向のコスト控除後損益 (net) で t / win / cumul を計算する。
    これによりサイト表示が reports/kouaku_backtest.md と完全一致する。
    """
    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in records:
        attrs = r.get("attrs") or {}
        if attrs.get("limit_locked"):
            continue
        v = attrs.get(PRIMARY_METRIC)
        if v is None:
            continue
        cells[(r.get("subpattern", "other"), _disc_bucket(r))].append(float(v))

    edges: list[dict[str, Any]] = []
    for (subpattern, bucket), raw_vals in cells.items():
        if len(raw_vals) < MIN_CELL_N:
            continue
        raw_mean = sum(raw_vals) / len(raw_vals)
        direction = "short" if raw_mean < 0 else "long"
        nets = [_net_pnl(v, cost_pct, direction) for v in raw_vals]
        st = _stats(nets)                  # ev/t/win/cumul は net ベース
        lo, hi = _bootstrap_ci(nets)
        edges.append({
            "subpattern": subpattern,
            "disc_time_bucket": bucket,
            "metric": PRIMARY_METRIC,
            "direction": direction,
            "n": st["n"],
            "t": _round(st["t"], 2),                  # net t (= backtest)
            "ev_pct": _round(st["ev"] + cost_pct),    # 約定方向 EV (cost 前, 参考)
            "ev_net_pct": _round(st["ev"]),           # コスト控除後 EV (= backtest)
            "win_pct": _round(st["win"], 1),
            "cumul_pct": _round(st["cumul"], 2),      # net 累積 (= backtest)
            "ci95": [_round(lo), _round(hi)],         # net P&L の bootstrap 95% CI
            "raw_ev_pct": _round(raw_mean),           # long 視点の符号付き生 EV (参考)
            "confidence": _confidence(st["n"]),
            "notable": st["n"] >= MIN_CELL_N and abs(st["t"]) >= NOTABLE_T,
        })

    edges.sort(key=lambda e: (e["t"] if e["t"] is not None else 0.0), reverse=True)
    return edges


def build_events(records: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """直近の好悪同日材料 (event_date 降順) を表示用に整形して返す。"""
    ordered = sorted(records, key=lambda r: r["event_date"], reverse=True)[:limit]
    events: list[dict[str, Any]] = []
    for r in ordered:
        attrs = r.get("attrs") or {}
        events.append({
            "date": r["event_date"],
            "code": r["code"],
            "subpattern": r.get("subpattern", "other"),
            "disc_time_bucket": _disc_bucket(r),
            "disc_time": _earliest_disc_time(r),
            "good": _first_label(r.get("good_factors", [])),
            "bad": _first_label(r.get("bad_factors", [])),
            "gap_pct": _round(attrs.get("gap_pct")),
            "next_day_open_to_close_ret_pct": _round(attrs.get("next_day_open_to_close_ret")),
            "next_day_full_ret_pct": _round(attrs.get("next_day_full_ret")),
            "limit_locked": bool(attrs.get("limit_locked")),
        })
    return events


def build_site_payload(
    payload: dict[str, Any],
    *,
    cost_pct: float = DEFAULT_COST_PCT,
    recent_limit: int = RECENT_EVENTS_LIMIT,
    last_updated: str | None = None,
) -> dict[str, Any]:
    """kouaku_records payload → サイト表示用 slim payload。"""
    records = payload.get("records", [])
    dates = [r["event_date"] for r in records if r.get("event_date")]
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "last_updated": last_updated or _dt.date.today().isoformat(),
            "data_window": [min(dates), max(dates)] if dates else [None, None],
            "total_events": len(records),
            "primary_metric": PRIMARY_METRIC,
            "cost_assumption_pct": cost_pct,
            "min_cell_n": MIN_CELL_N,
        },
        "edges": build_edges(records, cost_pct=cost_pct),
        "events": build_events(records, limit=recent_limit),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=RECORDS_PATH, help="kouaku_records.json のパス")
    ap.add_argument("--out", type=Path, default=SITE_PATH, help="出力先 (既定 data/kouaku_site.json)")
    ap.add_argument("--cost", type=float, default=DEFAULT_COST_PCT, help="往復コスト %% (既定 0.20)")
    ap.add_argument("--recent", type=int, default=RECENT_EVENTS_LIMIT, help="events に載せる直近件数 (既定 300)")
    args = ap.parse_args()

    payload = json.loads(args.path.read_text())
    site = build_site_payload(payload, cost_pct=args.cost, recent_limit=args.recent)
    atomic_write_json(args.out, site)
    print(
        f"wrote {args.out}  "
        f"edges={len(site['edges'])}  events={len(site['events'])}  "
        f"total={site['meta']['total_events']}"
    )


if __name__ == "__main__":
    main()

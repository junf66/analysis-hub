"""月次クロスセクション・モメンタム スクリーナー（『今月の買い候補』生成）。

過去12か月の騰落率（直近1か月を除外＝12-1モメンタム）で 大型+中型 を順位付けし、
200日移動平均線の上にある上位N銘柄を出力する。月次リバランスで運用する想定。

検証(scripts 内 ad-hoc / broad universe 493銘柄 + walk-forward OOS):
  上位20銘柄 × 12-1 × 200日線上 × 1か月保有 = 対TOPIX α net +1.13%/月(全, t2.33)・
  test(2024-) +2.30%/月・勝月70%・t2.45。短期テクニカル(イントラデイ全敗)と違い、
  広域＋OOSを生き残った本物のモメンタム・プレミアム。

⚠️ α は対TOPIX超過。買い持ち＝市場βは取る(地合いが悪ければ絶対値は負け得る)。
   モメンタム・クラッシュ局面(2018-2020等)では弱化する。200日線フィルタが保険。

出力: reports/momentum_screen.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json, atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
REPORT_PATH = REPO_ROOT / "reports" / "momentum_screen.md"
LOOKBACK = 252   # 形成期間(営業日, ~12か月)
SKIP = 21        # 直近スキップ(~1か月, 短期反転を避ける)
TREND_N = 200    # トレンドフィルタの移動平均
DEFAULT_TOP = 30


def _c4(code: str) -> str:
    code = str(code)
    return code[:-1] if len(code) == 5 and code.endswith("0") else code


def rank_momentum(closes: dict[str, dict[str, float]], cal: list[str], asof_idx: int,
                  names: dict[str, str], *, lookback: int = LOOKBACK, skip: int = SKIP,
                  trend_n: int = TREND_N, top_n: int = DEFAULT_TOP) -> list[dict[str, Any]]:
    """asof_idx 時点の 12-1 モメンタム上位（200日線上のみ）を返す（純関数・検証可能）。

    closes: code → {date: close}。cal: 取引カレンダー(昇順)。asof_idx: cal 上の基準日 index。
    """
    if asof_idx < lookback or asof_idx >= len(cal):
        return []
    d, fe, fs = cal[asof_idx], cal[asof_idx - skip], cal[asof_idx - lookback]
    rows: list[dict[str, Any]] = []
    for code, m in closes.items():
        if not (d in m and fe in m and fs in m and m[fs]):
            continue
        # トレンドフィルタ: 基準日終値 ≥ 直近 trend_n 本の平均
        hist = [m[cal[k]] for k in range(max(0, asof_idx - trend_n), asof_idx) if cal[k] in m]
        if len(hist) < trend_n * 0.75 or m[d] < statistics.fmean(hist):
            continue
        mom = (m[fe] / m[fs] - 1.0) * 100.0
        rows.append({"code": _c4(code), "name": names.get(code, "?"), "mom_pct": mom, "close": m[d]})
    rows.sort(key=lambda r: r["mom_pct"], reverse=True)
    return rows[:top_n]


def load_universe(master_path: Path = MASTER_PATH) -> dict[str, str]:
    """大型+中型(TOPIX Core30/Large70/Mid400)の code5 → 社名。"""
    recs = json.loads(master_path.read_text())["records"]
    return {r["Code"]: (r.get("CoName") or "?") for r in recs if r.get("scale_band") in ("大型", "中型")}


def fetch_closes(codes: list[str], frm: str, to: str, cache_path: Path | None = None) -> dict[str, dict[str, float]]:
    """各 code の日次 調整後終値 {date: AdjC} を取得（cache 併用・resume 可）。"""
    from scripts import _jquants
    cache: dict[str, dict[str, float]] = {}
    if cache_path and cache_path.exists():
        cache = json.loads(cache_path.read_text())
    for i, code in enumerate(codes, 1):
        if code in cache:
            continue
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=code, **{"from": frm, "to": to})
            cache[code] = {b["Date"]: (b.get("AdjC") or b.get("C")) for b in bars if (b.get("AdjC") or b.get("C"))}
        except _jquants.JQuantsError:
            cache[code] = {}
        if cache_path and i % 100 == 0:
            atomic_write_json(cache_path, cache)
    if cache_path:
        atomic_write_json(cache_path, cache)
    return cache


def build_report(rows: list[dict[str, Any]], asof: str, n_pass: int) -> str:
    """上位モメンタム候補を Markdown 表にする。"""
    L = [f"# 月次モメンタム・スクリーン（今月の買い候補） 基準日 {asof}", "",
         f"12-1モメンタム(過去12か月・直近1か月除外)上位・200日線上のみ。200日線通過 {n_pass} 銘柄中の上位{len(rows)}。",
         "運用: 月初に等加重で買い→1か月保有→翌月 再ランクで乗り換え。αは対TOPIX超過(β=市場は取る)。", "",
         "| 順 | コード | 銘柄 | 12-1騰落% | 終値 |", "|--:|---|---|--:|--:|"]
    for i, r in enumerate(rows, 1):
        L.append(f"| {i} | {r['code']} | {r['name']} | {r['mom_pct']:+.0f}% | {r['close']:,.0f} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=DEFAULT_TOP, help="出力する上位銘柄数 (既定 30)")
    ap.add_argument("--bars", type=Path, help="事前取得した日次終値キャッシュ(JSON: code→{date:close})。無ければ J-Quants から取得")
    ap.add_argument("--out", type=Path, default=REPORT_PATH, help="出力 md (既定 reports/momentum_screen.md)")
    args = ap.parse_args()
    names = load_universe()
    topix_path = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
    cal = sorted(r["Date"] for r in json.loads(topix_path.read_text())["records"])
    if args.bars and args.bars.exists():
        raw = json.loads(args.bars.read_text())
        # 受理形式: code→{date:close} もしくは code→[{D,C}]
        closes = {c: (v if isinstance(v, dict) else {b["D"]: b["C"] for b in v}) for c, v in raw.items()}
    else:
        today = date.today()
        closes = fetch_closes(list(names), (today - timedelta(days=500)).isoformat(), today.isoformat(),
                              REPO_ROOT / "data" / "edge_candidates" / "momentum_bars.json")
    asof_idx = len(cal) - 1
    rows = rank_momentum(closes, cal, asof_idx, names, top_n=args.top)
    n_pass = len(rank_momentum(closes, cal, asof_idx, names, top_n=10**6))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, build_report(rows, cal[asof_idx], n_pass))
    print(f"[momentum] 基準日{cal[asof_idx]} / 200日線上{n_pass}銘柄 / 上位{len(rows)} → {args.out}")
    for i, r in enumerate(rows[:args.top], 1):
        print(f"  {i:2d}. {r['code']:5s} {r['name'][:14]:14s} {r['mom_pct']:+6.0f}%")


if __name__ == "__main__":
    main()

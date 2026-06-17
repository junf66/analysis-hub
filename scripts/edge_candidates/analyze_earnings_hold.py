"""決算持ち越しロング × 時価総額 の検証（小型は機関が入れず非効率という仮説）。

仮説: 時価総額が小さい(機関/玄人が入れない)銘柄ほど決算をまたぐと非効率な α が残る。
方法: 引け後(15:00+)発表の決算イベントを、当日引け(発表直前)で持ち→翌日引けで手仕舞い。
      時価総額 = ShOutFY(発行済株式数) × 当日引け株価。対TOPIX α(close-to-close)。
データ: /equities/bars/daily を date 指定で全市場取得(上場廃止銘柄も当日分は返る=生存バイアス無)。
      決算 = cache/disclosures/fins_summary.json(公式 /fins/summary)。
評価: 時価総額バケット別 α / 発表日クラスタ頑健 t / OOS。コストは小型スプレッドを段階控除。

出力: reports/earnings_hold.md / 中間: cache/market_close.json
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json, atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FINS_PATH = REPO_ROOT / "cache" / "disclosures" / "fins_summary.json"
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
MC_PATH = REPO_ROOT / "cache" / "market_close.json"          # {date: {code: AdjC}}
REPORT_PATH = REPO_ROOT / "reports" / "earnings_hold.md"
FRM = "2021-06-01"


def fetch_market_close() -> dict[str, dict[str, float]]:
    """全営業日の全市場 終値 {date:{code:AdjC}} をストリーミング取得(resume可)。"""
    from scripts import _jquants
    topix = {r["Date"] for r in json.loads(TOPIX_PATH.read_text())["records"]}
    cal = [d for d in sorted(topix) if d >= FRM]
    mc: dict[str, dict[str, float]] = json.loads(MC_PATH.read_text()) if MC_PATH.exists() else {}
    for i, d in enumerate(cal):
        if d in mc:
            continue
        try:
            bars = _jquants.get_list("/equities/bars/daily", date=d)
            mc[d] = {str(b["Code"]): (b.get("AdjC") or b.get("C")) for b in bars if (b.get("AdjC") or b.get("C"))}
        except Exception:   # noqa: BLE001  通信途中切れ(IncompleteRead)等も含め当日skip・継続
            mc[d] = {}
        if i % 50 == 0:
            atomic_write_json(MC_PATH, mc)
            print(f"  {d} ({i}/{len(cal)})", flush=True)
    atomic_write_json(MC_PATH, mc)
    return mc


def _clustered_t(byd: dict[str, list[float]]) -> tuple[float, float, int]:
    """発表日クラスタ頑健 t。"""
    allv = [v for vs in byd.values() for v in vs]
    n = len(allv)
    if n < 2:
        return (statistics.fmean(allv) if allv else 0.0), 0.0, n
    mean = statistics.fmean(allv)
    num = sum(sum(x - mean for x in vs) ** 2 for vs in byd.values())
    se = math.sqrt(num) / n
    return mean, (mean / se if se else 0.0), n


def analyze(mc: dict[str, dict[str, float]]) -> str:
    """決算持ち越しを時価総額バケット別に集計。"""
    tpx = {r["Date"]: r["C"] for r in json.loads(TOPIX_PATH.read_text())["records"] if r.get("C")}
    cal = sorted(tpx)
    nxt = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    fins = json.loads(FINS_PATH.read_text())["by_date"]

    # バケット (時価総額 億円): 名前→(下限,上限)
    buckets = [("<30億", 0, 30), ("30-50億", 30, 50), ("50-100億", 50, 100),
               ("100-300億", 100, 300), ("300-1000億", 300, 1000), ("≥1000億", 1000, 1e9)]
    data: dict[str, list[tuple[str, float]]] = {b[0]: [] for b in buckets}
    for dt in fins:
        for r in fins[dt]:
            if "FinancialStatements" not in r.get("DocType", "") or r.get("DiscTime", "") < "15:00":
                continue
            code, d = str(r.get("Code") or ""), r.get("DiscDate")
            try:
                shout = float(r.get("ShOutFY") or 0)
            except ValueError:
                continue
            if not code or not d or shout <= 0 or d not in mc or d not in nxt:
                continue
            d1 = nxt[d]
            c0, c1 = mc[d].get(code), (mc.get(d1) or {}).get(code)
            if not c0 or not c1:
                continue
            mktcap = shout * c0 / 1e8   # 億円
            ta, tb = tpx.get(d), tpx.get(d1)
            tx = (tb / ta - 1.0) * 100.0 if (ta and tb) else 0.0
            alpha = (c1 / c0 - 1.0) * 100.0 - tx
            for name, lo, hi in buckets:
                if lo <= mktcap < hi:
                    data[name].append((d, alpha))
                    break

    L = ["# 決算持ち越しロング × 時価総額 検証", "",
         "引け後発表の決算を当日引けで持ち→翌日引け売り。対TOPIX α(close-to-close)。"
         "時価総額=ShOutFY×当日引け。発表日クラスタt。OOS=2024split。上場廃止込み(生存バイアス無)。", "",
         "| 時価総額 | gross α% | t_clust | 勝率% | n | net(小型コスト控除) | OOS |",
         "|---|--:|--:|--:|--:|--:|---|"]
    for name, lo, hi in buckets:
        rows = data[name]
        byd: dict[str, list[float]] = {}
        for d, a in rows:
            byd.setdefault(d, []).append(a)
        m, t, n = _clustered_t(byd)
        win = (sum(1 for _, a in rows if a > 0) / len(rows) * 100) if rows else 0.0
        bte: dict[str, list[float]] = {}
        for d, a in rows:
            if d >= "2024-01":
                bte.setdefault(d, []).append(a)
        mte, tte, _ = _clustered_t(bte)
        # 小型ほどスプレッド大: <100億は往復1.0%, 100-300億0.5%, それ以上0.3%
        cost = 1.0 if hi <= 100 else (0.5 if hi <= 300 else 0.3)
        L.append(f"| {name} | {m:+.2f} | {t:+.2f} | {win:.0f} | {n} | {m - cost:+.2f} | {mte:+.2f}(t{tte:+.1f}) |")
    L += ["", "## 読み方",
          "- gross α が小型ほど大きく t も有意 → 仮説支持(機関不在の非効率)。",
          "- ただし **net(小型スプレッド往復控除後)** が正で残るかが実弾判定。<100億は往復1%超が普通。",
          "- close-to-close = 引け持ち→翌引け。寄りで反応が出る分は別途(寄りエントリーは更にコスト)。"]
    return "\n".join(L) + "\n"


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-fetch", action="store_true", help="キャッシュのみで集計")
    args = ap.parse_args()
    mc = json.loads(MC_PATH.read_text()) if (args.no_fetch and MC_PATH.exists()) else fetch_market_close()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(REPORT_PATH, analyze(mc))
    print(f"[earnings_hold] 日数{len(mc)} → {REPORT_PATH}")


if __name__ == "__main__":
    main()

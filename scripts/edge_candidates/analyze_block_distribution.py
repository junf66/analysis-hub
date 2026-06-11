"""立会外分売（off-auction block distribution）に PO 類似のエッジがあるかの初回検証。

立会外分売＝大株主/自己株が市場外でブロックを一般に売り出す＝『新規の売り供給増』。
PO と同じ需給悪化構造なので、発表後に短期の下押し(=ショート)が出るかを測る。

イベント抽出: TDnet(yanoshin) の『株式の立会外分売に関するお知らせ』(発表, 実施/終了/中止/訂正は除外)。
発表は引け後が多い → シグナルは発表日 D の引けで確定、行動は D+1 寄りから。

測定(調整後 O/C, α=対TOPIX β=1):
  gap     : 発表→翌寄り (close[D]→open[D+1])           … 翌朝のギャップ
  d1_io   : 翌日日中 (open[D+1]→close[D+1])             … 当日ショート/ロング
  to_3 /5 : 翌寄り→+3/+5営業日引け (open[D+1]→close[D+k]) … 数日スイング
コスト: short 0.15% / long 0.20%(片道控除は呼出側で解釈)。クラスタ=発表日, OOS=2024split。

出力: reports/block_distribution.md
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
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
CACHE_PATH = REPO_ROOT / "cache" / "block_dist_bars.json"
REPORT_PATH = REPO_ROOT / "reports" / "block_distribution.md"

ANNOUNCE_RE = re.compile(r"立会外分売に関するお知らせ")
EXCLUDE_RE = re.compile(r"実施|終了|中止|訂正|延期|変更|主要株主")
OOS_SPLIT = "2024-01-01"


def extract_events(tdnet: dict[str, Any]) -> list[dict[str, str]]:
    """発表イベント(code, date) を抽出（同一 code+date は1件に集約）。"""
    by_date = tdnet["by_date"]
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for dt in sorted(by_date):
        for r in by_date[dt]:
            t = r.get("title", "")
            if not ANNOUNCE_RE.search(t) or EXCLUDE_RE.search(t):
                continue
            code = str(r.get("code") or "")[:4]
            d = dt[:10]
            if not code or (code, d) in seen:
                continue
            seen.add((code, d))
            out.append({"code": code, "date": d})
    return out


def fetch_bars(codes: list[str], frm: str, to: str) -> dict[str, dict[str, dict[str, float]]]:
    """各 code の調整後 {date:{O,C}} を取得(cache 併用・resume 可)。"""
    from scripts import _jquants
    cache: dict[str, dict[str, dict[str, float]]] = {}
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text())
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    for i, code in enumerate(codes, 1):
        if code in cache:
            continue
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=code, **{"from": frm, "to": to})
            cache[code] = {b["Date"]: {"O": b.get("AdjO") or b.get("O"), "C": b.get("AdjC") or b.get("C")}
                           for b in bars if (b.get("AdjC") or b.get("C"))}
        except _jquants.JQuantsError:
            cache[code] = {}
        if i % 25 == 0:
            atomic_write_json(CACHE_PATH, cache)
            print(f"  fetched {i}/{len(codes)}")
    atomic_write_json(CACHE_PATH, cache)
    return cache


def enrich(events: list[dict[str, str]], bars: dict, topix: dict[str, dict[str, float]],
           cal: list[str]) -> list[dict[str, Any]]:
    """各イベントに gap / d1_io / to_3 / to_5 と対TOPIX α を付与。"""
    idx = {d: i for i, d in enumerate(cal)}
    out: list[dict[str, Any]] = []
    for ev in events:
        b = bars.get(ev["code"]) or {}
        d = ev["date"]
        if d not in idx:
            # 発表日が非取引日(土日)なら直前営業日に寄せる
            prior = [x for x in cal if x <= d]
            if not prior:
                continue
            d = prior[-1]
        i0 = idx[d]
        if i0 + 5 >= len(cal):
            continue
        cD, dates = b.get(d, {}).get("C"), [cal[i0 + k] for k in range(0, 6)]
        o1 = b.get(dates[1], {}).get("O")
        c1 = b.get(dates[1], {}).get("C")
        c3 = b.get(dates[3], {}).get("C")
        c5 = b.get(dates[5], {}).get("C")
        if not (cD and o1):
            continue

        def tx(a: str, bb: str) -> float:
            ta, tb = topix.get(a, {}).get("C"), topix.get(bb, {}).get("C")
            return (tb / ta - 1.0) * 100.0 if (ta and tb) else 0.0

        rec: dict[str, Any] = {"code": ev["code"], "date": ev["date"]}
        rec["gap"] = (o1 / cD - 1.0) * 100.0 - tx(d, dates[1])
        if c1:
            rec["d1_io"] = (c1 / o1 - 1.0) * 100.0 - tx(dates[1], dates[1])
        if c3:
            rec["to_3"] = (c3 / o1 - 1.0) * 100.0 - tx(dates[1], dates[3])
        if c5:
            rec["to_5"] = (c5 / o1 - 1.0) * 100.0 - tx(dates[1], dates[5])
        out.append(rec)
    return out


def _clustered_t(vals_by_date: dict[str, list[float]]) -> tuple[float, float, int, int]:
    """発表日クラスタ頑健 t。(mean, t, n, n_clusters)。"""
    allv = [v for vs in vals_by_date.values() for v in vs]
    n = len(allv)
    if n < 2:
        return (statistics.fmean(allv) if allv else 0.0), 0.0, n, len(vals_by_date)
    mean = statistics.fmean(allv)
    num = sum(sum(v - mean for v in vs) ** 2 for vs in vals_by_date.values())
    se = math.sqrt(num) / n
    return mean, (mean / se if se else 0.0), n, len(vals_by_date)


def _metric_stats(recs: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """1メトリクスの全体/OOS 統計。"""
    def grp(sub: list[dict[str, Any]]) -> dict[str, list[float]]:
        g: dict[str, list[float]] = {}
        for r in sub:
            if key in r:
                g.setdefault(r["date"], []).append(r[key])
        return g
    full = grp(recs)
    m, t, n, gc = _clustered_t(full)
    win = (sum(1 for vs in full.values() for v in vs if v > 0) / n * 100) if n else 0.0
    tr = grp([r for r in recs if r["date"] < OOS_SPLIT])
    te = grp([r for r in recs if r["date"] >= OOS_SPLIT])
    mtr, ttr, ntr, _ = _clustered_t(tr)
    mte, tte, nte, _ = _clustered_t(te)
    return {"mean": m, "t": t, "n": n, "clusters": gc, "win": win,
            "tr": (mtr, ttr, ntr), "te": (mte, tte, nte)}


def build_report(recs: list[dict[str, Any]], n_events: int, n_codes: int) -> str:
    """全メトリクスの全体/OOS を Markdown 表にまとめる。"""
    L = ["# 立会外分売 初回検証（PO類似の需給エッジ探索）", "",
         f"TDnet発表 {n_events}件 / 価格取得できた {len(recs)}件 / {n_codes}銘柄。"
         f" α=対TOPIX(β=1)。クラスタ=発表日。OOS分割 {OOS_SPLIT[:7]}。", "",
         "シグナル: 発表日Dの引けで確定 → D+1寄りから。**正値=上昇**(ショートなら符号反転で読む)。", "",
         "| メトリクス | 意味 | α平均% | t_clust | 勝率% | n | train→test |",
         "|---|---|--:|--:|--:|--:|---|"]
    labels = {"gap": "発表→翌寄り(overnight)", "d1_io": "翌日日中(寄→引)",
              "to_3": "翌寄→+3日引", "to_5": "翌寄→+5日引"}
    for key, lab in labels.items():
        s = _metric_stats(recs, key)
        mtr, ttr, ntr = s["tr"]
        mte, tte, nte = s["te"]
        L.append(f"| {lab} | {key} | {s['mean']:+.2f} | {s['t']:+.2f} | {s['win']:.0f} | {s['n']} | "
                 f"{mtr:+.2f}(t{ttr:+.1f})→{mte:+.2f}(t{tte:+.1f}) |")
    L += ["", "## 読み方",
          "- α平均が負＝発表後に下押し＝**ショート余地**(往復コスト short0.15%/long0.20%を別途控除)。",
          "- |t_clust|≥2 かつ OOS test も同符号で残れば PO 類似エッジ候補。次段で売出率/時価総額/信用区分で層別。",
          "- 立会外分売は小型銘柄が多く流動性が薄い → 実約定可能性(出来高)は別途要確認。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true", help="キャッシュのみ使用")
    ap.add_argument("--out", type=Path, default=REPORT_PATH, help="出力 md")
    args = ap.parse_args()

    tdnet = json.loads(TDNET_PATH.read_text())
    events = extract_events(tdnet)
    topix = {r["Date"]: {"C": r["C"]} for r in json.loads(TOPIX_PATH.read_text())["records"] if r.get("C")}
    cal = sorted(topix)
    frm = max(cal[0], "2016-06-13")
    codes = sorted({e["code"] for e in events})
    bars = json.loads(CACHE_PATH.read_text()) if (args.no_fetch and CACHE_PATH.exists()) \
        else fetch_bars(codes, frm, cal[-1])

    recs = enrich(events, bars, topix, cal)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, build_report(recs, len(events), len(codes)))
    print(f"[block_dist] 発表{len(events)} / 価格化{len(recs)} / {len(codes)}銘柄 → {args.out}")
    for key in ("gap", "d1_io", "to_3", "to_5"):
        s = _metric_stats(recs, key)
        mte, tte, nte = s["te"]
        print(f"  {key:6s} α{s['mean']:+.2f}% t{s['t']:+.2f} win{s['win']:.0f}% n{s['n']}"
              f"  test {mte:+.2f}(t{tte:+.1f})")


if __name__ == "__main__":
    main()

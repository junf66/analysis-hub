"""ロックアップ解除ショートの精緻化（どのIPOの90日解除を売るか）。

確定昇格候補「ロックアップ解除翌日からショート（90日が本命）」(docs/edge_playbook.md)を、
手元データ（96ut評価/初値GU + 規模 + 信用区分）で層別し『効きどころ』を特定する。

仮説（#4株式分割・IPO2日目フェードと同系）: 初値で派手に跳ねた(高GU=個人の過熱フロー)IPOほど、
ロックアップ解除でVC/既存株主の売りオーバーハングが顕在化し、解除直後ショートが効く。
逆に初値が冷めた(低/マイナスGU)IPOは巻き戻す玉が小さくショートが弱い。

軸: (1)初値GU帯 (2)96ut評価 (3)規模(equities_master scale_band) (4)信用区分(貸借/信用=執行性)。
対象は 90日解除（本命）の +3日/+7日 出口（正本のEVピーク）。
方向別 net cost（short 0.15%）・解除月クラスタ頑健t・walk-forward OOS はインライン。

出力: reports/lockup_short_detailed.md
"""
from __future__ import annotations

import argparse
import bisect
import datetime
import json
import statistics
from collections import defaultdict
from pathlib import Path

from scripts._atomic import atomic_write_text
from scripts.edge_candidates.analyze_archive_regime import clustered_t, oos_test

REPO = Path(__file__).resolve().parent.parent.parent
RATINGS = REPO / "data" / "edge_candidates" / "ipo_96ut_ratings.json"
BARS = REPO / "cache" / "ipo_bars_raw.json"          # code -> [[date,O,C]]
TOPIX = REPO / "data" / "edge_candidates" / "topix_daily.json"
MASTER = REPO / "data" / "edge_candidates" / "equities_master.json"
REPORT = REPO / "reports" / "lockup_short_detailed.md"

SHORT_COST = 0.15          # 方向別 net（ショート＝楽天滑りのみ）
LOCK_DAYS = 90             # 本命解除（正本: 90日 >> 180日非有意）
MIN_N = 8                  # セル最小観測（小サンプルはノイズ）


def _c5(code: str) -> str:
    code = str(code)
    return code if len(code) == 5 else code + "0"


def _load_cal() -> tuple[dict, list]:
    tpx = {r["Date"]: [r.get("O"), r.get("C")]
           for r in json.loads(TOPIX.read_text())["records"] if r.get("O")}
    return tpx, sorted(tpx)


def _onafter(cal: list, dstr: str) -> str | None:
    i = bisect.bisect_left(cal, dstr)
    return cal[i] if i < len(cal) else None


def _nth(cal: list, d: str, k: int) -> str | None:
    i = bisect.bisect_left(cal, d) + k
    return cal[i] if 0 <= i < len(cal) else None


def _addcal(dstr: str, n: int) -> str:
    y, m, dd = map(int, dstr.split("-"))
    return (datetime.date(y, m, dd) + datetime.timedelta(days=n)).isoformat()


def _short_excess(bk: dict, tpx: dict, E: str, X: str) -> float | None:
    """解除翌日Eの寄り→出口Xの引け、対TOPIX超過の short net（符号反転＋コスト）。"""
    if E not in bk or X not in bk or E not in tpx or X not in tpx:
        return None
    eo = bk[E][0]; to = tpx[E][0]; ec = bk[X][1]; tc = tpx[X][1]
    if not eo or not to:
        return None
    return -((ec / eo - 1) * 100 - (tc / to - 1) * 100) - SHORT_COST


def build_listing(ratings: list[dict], bars: dict) -> dict:
    """各IPOの上場日（初値≈始値の最初の足）と日足dictを返す。"""
    out = {}
    for r in ratings:
        code = r["code"]; h = r.get("hatsune")
        rows = bars.get(code) or []
        if not h or not rows:
            continue
        ld = next((d for d, o, c in rows if o and abs(o - h) / h < 0.015), None)
        if ld:
            out[code] = (ld, {d: (o, c) for d, o, c in rows})
    return out


def trades(listing: dict, ratings: dict, master: dict, tpx: dict, cal: list,
           exitN: int) -> list[dict]:
    """90日解除→翌日寄り→+exitN日引けの short net 1トレード/IPO。属性付き。"""
    out = []
    for code, (ld, bk) in listing.items():
        D = _onafter(cal, _addcal(ld, LOCK_DAYS - 1))   # 解除日（暦日カウント）
        if not D:
            continue
        E = _nth(cal, D, 1)                              # 解除翌営業日（寄りで売り）
        X = _nth(cal, E, exitN - 1) if E else None       # 出口（+exitN日引け）
        if not E or not X:
            continue
        s = _short_excess(bk, tpx, E, X)
        if s is None:
            continue
        meta = ratings[code]; m = master.get(_c5(code), {})
        out.append({"net": s, "month": E[:7], "date": E, "gu": meta.get("gu_pct"),
                    "rank": meta.get("rank"), "scale": m.get("scale_band"),
                    "mrgn": m.get("MrgnNm")})
    return out


def _fmt(rows: list[dict]) -> str:
    if len(rows) < MIN_N:
        return f"n{len(rows)}（小サンプル・判定不可）"
    v = [r["net"] for r in rows]
    t = clustered_t(v, [r["month"] for r in rows])
    win = sum(1 for x in v if x > 0) / len(v) * 100
    return f"EV{statistics.fmean(v):+.2f}% 勝{win:.0f}% t_clust{t:+.1f} n{len(v)}"


def _gu_band(g) -> str:
    if g is None:
        return "?"
    if g < 0:
        return "a:冷(GU<0)"
    if g < 20:
        return "b:微(0-20%)"
    if g < 50:
        return "c:中(20-50%)"
    if g < 100:
        return "d:高(50-100%)"
    return "e:爆(>100%)"


def report(exits=(3, 7)) -> str:
    ratings_list = json.loads(RATINGS.read_text())["records"]
    ratings = {r["code"]: r for r in ratings_list}
    bars = json.loads(BARS.read_text())
    master = {str(r["Code"]): r for r in json.loads(MASTER.read_text())["records"]}
    tpx, cal = _load_cal()
    listing = build_listing(ratings_list, bars)

    L = ["# ロックアップ解除ショート 精緻化（90日解除・どのIPOを売るか）", "",
         f"対象IPO {len(listing)}社（上場日検出済）。short net cost {SHORT_COST}%・解除月クラスタ頑健t。",
         "解除日＝上場+89暦日、翌営業日寄りで売り→+N日引けで買戻。", ""]

    for exitN in exits:
        rows = trades(listing, ratings, master, tpx, cal, exitN)
        L += [f"## 出口 +{exitN}日（解除翌寄→+{exitN}日引け）  全体: {_fmt(rows)}", ""]
        if len(rows) >= MIN_N:
            L.append(f"- OOS(walk-forward 0.7): test net {oos_test([(r['net'], r['date']) for r in rows], 0.0, short=False):+.2f}%（netは算入済）")
        for axis, keyf, order in [
            ("初値GU帯", lambda r: _gu_band(r["gu"]), None),
            ("96ut評価", lambda r: r["rank"] or "?", ["A", "B", "C", "D"]),
            ("規模", lambda r: r["scale"] or "?", ["大型", "中型", "小型"]),
            ("信用区分(執行性)", lambda r: r["mrgn"] or "?", ["貸借", "信用"]),
        ]:
            grp = defaultdict(list)
            for r in rows:
                grp[keyf(r)].append(r)
            keys = order if order else sorted(grp)
            L += [f"### {axis}"]
            for k in keys:
                if k in grp:
                    L.append(f"- **{k}**: {_fmt(grp[k])}")
            L.append("")
    L += ["---",
          "注: GU帯が高い（初値で派手に跳ねた）IPOほど解除時の売りオーバーハングが大きい仮説の検証。",
          "執行: 貸借＝制度信用で売建可。信用＝楽天一般信用在庫依存（いちにち信用はデイ限定ゆえ+N日スイング不可）。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPORT)
    args = ap.parse_args()
    body = report()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, body)
    print(body)
    print(f"[lockup_short] → {args.out}")


if __name__ == "__main__":
    main()

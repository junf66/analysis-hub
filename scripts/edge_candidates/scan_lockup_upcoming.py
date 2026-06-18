"""直近〜今後に『90日ロック解除』が来る90日ロック保有IPOを抽出（ロックアップ短期ショートの実弾リスト）。

ロックアップ解除ショート候補(90日マーク)の中で、EDINET実条項で『90日ロック保有』が確認できた
IPOに絞り、解除日(上場+89暦日)が [today-back, today+ahead] の窓に入るものを列挙する。
解除翌営業日=エントリー(寄り)。出口はデイトレ/+3日/+7日(正本の出口ラダー参照)。

note: 営業日は土日のみ考慮の近似(JP祝日は未考慮ゆえ最終的に取引所カレンダーで要確認)。
出力: 標準出力（実弾候補リスト）。
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
TERMS = REPO / "data" / "edge_candidates" / "ipo_lockup_terms.json"
RATINGS = REPO / "data" / "edge_candidates" / "ipo_96ut_ratings.json"
MASTER = REPO / "data" / "edge_candidates" / "equities_master.json"
BARS = (REPO / "data" / "edge_candidates" / "ipo_bars_raw.json"
        if (REPO / "data" / "edge_candidates" / "ipo_bars_raw.json").exists()
        else REPO / "cache" / "ipo_bars_raw.json")
_WD = ["月", "火", "水", "木", "金", "土", "日"]


def _c5(code: str) -> str:
    code = str(code)
    return code if len(code) == 5 else code + "0"


def _next_bizday(d: datetime.date) -> datetime.date:
    """翌営業日(土日スキップのみ・祝日未考慮)。"""
    d += datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d += datetime.timedelta(days=1)
    return d


def _add_bizdays(d: datetime.date, n: int) -> datetime.date:
    """n営業日後(土日スキップのみ)。"""
    for _ in range(n):
        d = _next_bizday(d)
    return d


def scan(today: datetime.date, back: int, ahead: int) -> list[dict]:
    """解除日が [today-back, today+ahead] の 90日ロック保有IPO を返す。"""
    terms = json.loads(TERMS.read_text())
    master = {str(r["Code"]): r for r in json.loads(MASTER.read_text())["records"]}
    ratings = {r["code"]: r for r in json.loads(RATINGS.read_text())["records"]}
    bars = json.loads(BARS.read_text())
    out = []
    for code, t in terms.items():
        if t.get("status") != "ok" or 90 not in (t.get("lockup_days") or []):
            continue
        rows = bars.get(code) or []
        if not rows:
            continue
        ld = min(d for d, *_ in rows)
        y, m, dd = map(int, ld.split("-"))
        release = datetime.date(y, m, dd) + datetime.timedelta(days=89)   # 90日目=上場+89暦日(最終ロック日)
        if not (today - datetime.timedelta(days=back) <= release <= today + datetime.timedelta(days=ahead)):
            continue
        # 最初に売れるのは上場+90暦日以降の最初の営業日(=エントリ寄り)。週末/祝日でズレないよう+90から営業日化。
        entry = datetime.date(y, m, dd) + datetime.timedelta(days=90)
        while entry.weekday() >= 5:
            entry += datetime.timedelta(days=1)
        out.append({
            "code": code, "name": master.get(_c5(code), {}).get("CoName", "?"),
            "mkt": master.get(_c5(code), {}).get("MktNm"),
            "mrgn": master.get(_c5(code), {}).get("MrgnNm"),
            "listing": ld, "lockup_days": t["lockup_days"], "gu": ratings.get(code, {}).get("gu_pct"),
            "release": release.isoformat(), "entry": entry.isoformat(),
            "exit_day": entry.isoformat(),
            "exit_3": _add_bizdays(entry, 2).isoformat(),
            "exit_7": _add_bizdays(entry, 6).isoformat(),
        })
    return sorted(out, key=lambda x: x["release"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="基準日(既定=今日)")
    ap.add_argument("--back", type=int, default=7, help="過去何日まで含めるか")
    ap.add_argument("--ahead", type=int, default=30, help="先何日まで含めるか")
    args = ap.parse_args()
    today = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    rows = scan(today, args.back, args.ahead)
    print(f"# 直近90日ロック解除 候補（基準 {today} / 窓 −{args.back}〜+{args.ahead}日）", "\n")
    print("解除翌営業日の寄りでショートIN。出口=デイ(+1.22%)/+3日(+3.03%)/+7日(+5.96%)（90日ロック保有・正本）。")
    print("⚠営業日は土日のみ考慮(祝日未)・売建可否(信用在庫/売り禁)は要確認・候補(FDR未通過)。\n")
    if not rows:
        print("該当なし。")
        return
    print("| コード | 銘柄 | 市場/信用 | 上場 | 条項 | 初値GU | 解除日 | IN(寄) | OUT+3 | OUT+7 |")
    print("|---|---|---|---|---|--:|---|---|---|---|")
    for r in rows:
        print(f"| {r['code']} | {r['name'][:12]} | {r['mkt']}/{r['mrgn']} | {r['listing']} | "
              f"{r['lockup_days']} | {r['gu']}% | {r['release']} | {r['entry']} | {r['exit_3']} | {r['exit_7']} |")


if __name__ == "__main__":
    main()

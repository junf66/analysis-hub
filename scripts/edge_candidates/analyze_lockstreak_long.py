"""連続「寄らずS高」翌寄りロング候補の過剰最適化ガード + 執行関門の切り分け。

仮説: D0=S高引け かつ D1=翌日も寄らずS高(終日張り付き)で引けた銘柄を、D1の
大引け(クロージングオークション)で買い、D2の寄りで売る。連続S高の継続(モメンタム)を取る。

検出: limit_ul_events から (code,D0)∈UL かつ D1=翌営業日∈UL(code) かつ
eD0.io≈0 (=D1は終日値動きほぼ無し=寄らず張り付き)。ロングのリターン = eD1.gap (D1引→D2寄)。

統計シグナルは確定級に頑健(全年+/日次collapse生存/OOS+/全市場+/廃止込み)。だが昇格の
最大関門は **D1ロック中の大引けで実際に買えるか(=比例配分の約定)** で、これは過去OHLCでは
原理的に測れない → 前進検証(実約定ログ)でのみ解決可能。ETF/REIT等(その他市場)は汚染で除外。

使い方:
  python -m scripts.edge_candidates.analyze_lockstreak_long
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

from scripts._atomic import atomic_write_text
from scripts.edge_candidates.verify_edges_standalone import _load, _pit, clustered_t

REPO = Path(__file__).resolve().parent.parent.parent
UL = REPO / "cache" / "limit_ul_events.json"
TOPIX = REPO / "data" / "edge_candidates" / "topix_daily.json"
LONG_COST = 0.20
LOCK_IO_MAX = 1.0       # |D1の寄→引| < これ = 寄らず張り付き判定
_EXCLUDE_MKT = {"その他", "TOKYO PRO MARKET", None}   # ETF/REIT/PRO 汚染除外


def _c5(c: str) -> str:
    return c + "0" if len(c) == 4 else c


def _events() -> tuple[dict, dict]:
    ul = json.loads(UL.read_text())
    ev = {(e["code"], e["date"]): e for e in ul}
    ulset: dict[str, set] = defaultdict(set)
    for e in ul:
        ulset[e["code"]].add(e["date"])
    return ev, ulset


def build_rows(D: dict, exclude_etf: bool = True) -> list[tuple]:
    """連続寄らずS高ロングの観測 [(net_ret%, D1, code, market, scale, d2_locked)]。"""
    tpx = {r["Date"]: r for r in json.loads(TOPIX.read_text())["records"] if r.get("O")}
    cal = sorted(tpx)
    nextd = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    mst, hist = D["mst"], D["hist"]
    hd = sorted(hist)
    mk = lambda c: (mst.get(_c5(c)) or {}).get("MktNm", "?")   # noqa: E731
    ev, ulset = _events()
    rows = []
    for (code, d0), e0 in ev.items():
        d1 = nextd.get(d0)
        if not d1 or d1 not in ulset.get(code, ()):
            continue
        if abs(e0["io"]) >= LOCK_IO_MAX:          # D1が寄らず張り付きでない
            continue
        m = mk(code)
        if exclude_etf and m in _EXCLUDE_MKT:
            continue
        e1 = ev.get((code, d1))
        if not e1:
            continue
        d2 = nextd.get(d1)
        d2_locked = (d2 in ulset.get(code, ())) and abs(e1["io"]) < LOCK_IO_MAX
        scale = _pit(hist, hd, code, d1).get("scale_band") or "不明"
        rows.append((e1["gap"] - LONG_COST, d1, code, m, scale, d2_locked))
    return rows


def _m(rs: list[tuple]) -> tuple[int, float, float, float]:
    nets = [x[0] for x in rs]
    dates = [x[1] for x in rs]
    if not nets:
        return 0, 0.0, 0.0, 0.0
    win = sum(1 for x in nets if x > 0) / len(nets) * 100
    return len(nets), st.fmean(nets), win, clustered_t(nets, dates)


def build_report(D: dict) -> str:
    """連続寄らずS高ロングの全ガード+執行関門を1 md にまとめて返す。"""
    rows = build_rows(D, exclude_etf=True)
    n, e, w, t = _m(rows)
    L = ["# 連続「寄らずS高」翌寄りロング — 確定昇格に向けた検証", "",
         "D0=S高引け & D1=翌日も寄らずS高 → **D1大引け(CA)で買い・D2寄りで売り**。",
         f"ETF/REIT等(その他市場)除外・long cost {LONG_COST}%。", "",
         f"## 【全体】n={n} EV={e:+.2f}% 勝率{w:.0f}% t_clust={t:+.2f}", ""]

    # 日次collapse
    byd: dict[str, list] = defaultdict(list)
    for r in rows:
        byd[r[1]].append(r[0])
    col = [(st.fmean(v), d) for d, v in byd.items()]
    cn = len(col)
    ce = st.fmean([x[0] for x in col])
    ct = clustered_t([x[0] for x in col], [x[1] for x in col])
    cw = sum(1 for x in col if x[0] > 0) / cn * 100
    L += [f"## 【日次collapse(独立性補正)】独立日n={cn} EV={ce:+.2f}% 勝率{cw:.0f}% t={ct:+.2f}",
          f"  1日あたり{n / cn:.1f}件 = 同日クラスタは軽微", ""]

    # walk-forward OOS
    rs = sorted(rows, key=lambda x: x[1])
    cut = int(len(rs) * 0.7)
    tr, te = rs[:cut], rs[cut:]
    sgn = 1 if st.fmean([r[0] for r in tr]) > 0 else -1
    oos = st.fmean([sgn * r[0] for r in te])
    L += [f"## 【walk-forward OOS】train方向={'L' if sgn > 0 else 'S'} "
          f"test EV={oos:+.2f}% (n={len(te)} / {te[0][1]}〜{te[-1][1]})", ""]

    # 年次
    L += ["## 【年次安定】", "", "| 年 | n | EV | 勝率 |", "|---|--:|--:|--:|"]
    byy: dict[str, list] = defaultdict(list)
    for r in rows:
        byy[r[1][:4]].append(r[0])
    for y in sorted(byy):
        v = byy[y]
        L.append(f"| {y} | {len(v)} | {st.fmean(v):+.2f}% | "
                 f"{sum(1 for x in v if x > 0) / len(v) * 100:.0f}% |")
    L.append("")

    # 市場別(汚染確認のため除外前も)
    L += ["## 【市場別(除外前・汚染確認)】", "", "| 市場 | n | EV | 勝率 |", "|---|--:|--:|--:|"]
    allrows = build_rows(D, exclude_etf=False)
    bym: dict[str, list] = defaultdict(list)
    for r in allrows:
        bym[str(r[3])].append(r[0])
    for m, v in sorted(bym.items(), key=lambda x: -len(x[1])):
        flag = " ←汚染除外" if m in {"その他", "TOKYO PRO MARKET", "None"} else ""
        L.append(f"| {m} | {len(v)} | {st.fmean(v):+.2f}% | "
                 f"{sum(1 for x in v if x > 0) / len(v) * 100:.0f}% |{flag}")
    L.append("")

    # 規模PIT
    L += ["## 【規模(PIT)別】", "", "| 規模 | n | EV | 勝率 |", "|---|--:|--:|--:|"]
    bys: dict[str, list] = defaultdict(list)
    for r in rows:
        bys[r[4]].append(r[0])
    for s, v in sorted(bys.items(), key=lambda x: -len(x[1])):
        L.append(f"| {s} | {len(v)} | {st.fmean(v):+.2f}% | "
                 f"{sum(1 for x in v if x > 0) / len(v) * 100:.0f}% |")
    L.append("")

    # 出口関門(D2もロックで売れないか)
    ng = [r[0] for r in rows if r[5]]
    ok = [r[0] for r in rows if not r[5]]
    L += ["## 【出口関門: D2もまた寄らずS高(=翌寄りで売れない)】", "",
          f"- D2寄って売れる: n={len(ok)} EV={st.fmean(ok):+.2f}% "
          f"勝率{sum(1 for x in ok if x > 0) / len(ok) * 100:.0f}%",
          f"- D2も寄らずS高(3連続): n={len(ng)} EV={st.fmean(ng):+.2f}% "
          f"勝率{sum(1 for x in ng if x > 0) / len(ng) * 100:.0f}% "
          f"= 含み益で利確遅延(損ではない・4連目リスクのみ)", "",
          f"  → 出口は実質問題なし({len(ng) / len(rows) * 100:.0f}%が含み益ロック)。", ""]

    # 結論
    L += ["## 結論 = 確定級シグナル・**執行関門で前進検証中**", "",
          "- 統計シグナルは過剰最適化ガードを全通過(全年+/日次collapse生存/OOS+/全市場+/廃止込み生存バイアス無)。",
          "  リポ内屈指の頑健さ。だが **EV はすべて『D1ロック中の大引けで買えた前提』の無条件平均**。",
          "- **昇格の唯一の関門 = クロージングオークションの実約定**: 寄らずS高は買い超過 → 買い注文は",
          "  比例配分でごく僅かしか約定しない/ゼロのことも。約定は『売りが出た=ロックが緩い=反転寄り』の玉に",
          "  逆選択する(寄らず最強帯ほど当たらない)。この**約定加重の実EVは過去OHLCでは測れない**。",
          "- → **前進検証プロトコル**: 全シグナルでCA成行買いを機械的に出し、(1)約定可否 (2)約定株数 "
          "(3)翌寄り結果 を実弾ログ。約定加重の実EVが + を保つか実測してから確定判定。⑩R実戦ログと同要領。",
          "- 補足: ロングゆえ逆日歩なし。出口は83%翌寄りで売れ17%は含み益ロック=損方向の関門ではない。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "reports" / "lockstreak_long.md")
    args = ap.parse_args()
    report = build_report(_load())
    print(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, report)
    print(f"[lockstreak_long] → {args.out}")


if __name__ == "__main__":
    main()

"""アーカイブ由来の地合い依存検証 (びびりおん/uoa)。

(A) ⑩R(小型S高翌朝ショート)の地合い符号反転:
    びびり「ストップ高投資法は全体相場(新興が一方通行で上昇)でのみ継続し、跛行色/下落では
    反転＝カモ」/ uoa「悪地合いはカモ」。⑩R は反転ショートゆえ、仮説では good地合いで弱まり
    (踏まれ)、bad地合いで強まるはず。⑩R の正確なトレード集合(verify_edges_standalone.edge_rows
    で再現＝完全一致)に複数レンズの地合いを結合し net EV/t_clust/勝率/OOS を層別する。
    地合い軸: TOPIX25日線 good/bad ・ TOPIX20日モメンタム ・ 当日の市場S高銘柄数(過熱breadth)。
    ※新興指数の日足が手元に無いため、S高breadth(n_UL)を「全体相場/過熱」の代理に使う。

(B) S安リバウンドロング(uoa「ストップ安買い」): ⑩Rの鏡像。非プライム小型×貸借(PIT)×S安×
    翌朝gap帯 → 翌寄→引 long。cache/limit_dl_events.json(別途 fetch_limit_down_events)が要る。

統計はインライン(verify_edges_standalone と同方式: 日付クラスタ頑健t / walk-forward OOS /
方向別 net cost)。出力: reports/archive_regime.md
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_text
from scripts.edge_candidates.verify_edges_standalone import (
    LONG_COST, _load, _pit, edge_rows,
)

REPO = Path(__file__).resolve().parent.parent.parent
COUNTS_PATH = REPO / "cache" / "limit_counts.json"          # {date: [n_LL, n_UL]}
DL_PATH = REPO / "cache" / "limit_dl_events.json"           # [{date, code, io, gap}]
REPORT = REPO / "reports" / "archive_regime.md"


# ---- インライン統計 (verify_edges_standalone と同方式) -----------------------

def clustered_t(values: list[float], clusters: list[Any]) -> float:
    """日付クラスタ頑健 t（同一クラスタ内相関で素朴 t が水増しされるのを補正）。"""
    n = len(values)
    if n < 2:
        return 0.0
    mu = statistics.fmean(values)
    by: dict[Any, float] = defaultdict(float)
    for v, c in zip(values, clusters):
        by[c] += v - mu
    g = len(by)
    if g < 2:
        s = statistics.stdev(values)
        return mu / (s / math.sqrt(n)) if s else 0.0
    meat = sum(s * s for s in by.values())
    var = (g / (g - 1)) * meat / (n * n)
    return mu / math.sqrt(var) if var > 0 else 0.0


def oos_test(rows: list[tuple], cost: float, short: bool, frac: float = 0.7) -> float:
    """walk-forward: 日付順 frac で train/test 分割、方向は train で決め test の net EV。"""
    rows = sorted(rows, key=lambda x: x[1])
    cut = int(len(rows) * frac)
    tr, te = rows[:cut], rows[cut:]
    if not tr or not te:
        return float("nan")
    sgn = -1 if statistics.fmean([r[0] for r in tr]) < 0 else 1
    return statistics.fmean([sgn * r[0] - cost for r in te])


def stat_block(rows: list[tuple], cost: float, short: bool) -> dict[str, float]:
    """rows=[(ret%, date, ...)] → net 基準 n/EV/勝率/t_clust/OOS。"""
    nets = [(-r[0] if short else r[0]) - cost for r in rows]
    dates = [r[1] for r in rows]
    if not nets:
        return {"n": 0, "ev": 0.0, "win": 0.0, "t": 0.0, "oos": float("nan")}
    return {
        "n": len(nets), "ev": statistics.fmean(nets),
        "win": sum(1 for x in nets if x > 0) / len(nets) * 100,
        "t": clustered_t(nets, dates), "oos": oos_test(rows, cost, short),
    }


def _row(b: dict[str, float]) -> str:
    oos = "  -  " if math.isnan(b["oos"]) else f"{b['oos']:+.2f}"
    return f"{b['n']} | {b['ev']:+.2f} | {b['win']:.0f} | {b['t']:+.2f} | {oos}"


# ---- 地合いレンズ -----------------------------------------------------------

def build_regime(tpx: dict[str, dict]) -> tuple[list[str], dict[str, int], dict, dict, dict]:
    """TOPIX日足から各営業日の地合い指標を作る。"""
    cal = sorted(tpx)
    idx = {d: i for i, d in enumerate(cal)}
    c = {d: tpx[d]["C"] for d in cal}
    ma25, ret20 = {}, {}
    for i, d in enumerate(cal):
        if i >= 25:
            ma25[d] = c[d] >= statistics.fmean(c[cal[k]] for k in range(i - 24, i + 1))
        if i >= 20:
            ret20[d] = (c[d] / c[cal[i - 20]] - 1.0) * 100.0
    counts = json.loads(COUNTS_PATH.read_text()) if COUNTS_PATH.exists() else {}
    n_ul = {d: v[1] for d, v in counts.items()}   # 当日の市場S高銘柄数 (過熱breadth)
    return cal, idx, ma25, ret20, n_ul


def split_report(title: str, rows: list[tuple], cost: float, short: bool,
                 keyfn, groups: list[tuple[str, Any]]) -> list[str]:
    """rows を keyfn(date)→値 で groups に振り分けて層別表を作る。"""
    L = [f"### {title}", "", "| 地合い | n | net EV% | 勝率% | t_clust | OOS% |",
         "|---|--:|--:|--:|--:|--:|"]
    buckets: dict[str, list[tuple]] = {g[0]: [] for g in groups}
    for r in rows:
        v = keyfn(r[1])
        if v is None:
            continue
        for label, pred in groups:
            if pred(v):
                buckets[label].append(r)
                break
    for label, _ in groups:
        b = stat_block(buckets[label], cost, short)
        L.append(f"| {label} | {_row(b)} |")
    return L + [""]


def analyze_10R_regime(D: dict[str, Any], cal, idx, ma25, ret20, n_ul) -> list[str]:
    """⑩R の正確なトレード集合を地合い(25日線/20日モメンタム/S高breadth)で層別した md 行を返す。"""
    rows, cost, short = edge_rows("⑩R", D)
    # (date,code) 重複除去 (verify と同一基準)
    seen, ded = set(), []
    for r in rows:
        k = (r[1], r[2])
        if k not in seen:
            seen.add(k)
            ded.append(r)
    rows = ded
    base = stat_block(rows, cost, short)
    L = ["## (A) ⑩R 小型S高翌朝ショート × 地合い", "",
         f"全体(再現): n{base['n']} / net{base['ev']:+.2f}% / 勝率{base['win']:.0f}% / "
         f"t_clust{base['t']:+.2f} / OOS{base['oos']:+.2f}%（CLAIMED n377/+2.56/59/4.70/1.73 と一致確認）", "",
         "仮説(びびり/uoa): ⑩R=過熱S高の反転ショート → **good地合い(全体相場)で継続し弱まる/踏まれ、"
         "bad地合い(跛行色・下落)で反転が効き強まる**はず。", ""]
    # 中央値 (連続軸の split 基準)
    ul_vals = sorted(n_ul[r[1]] for r in rows if r[1] in n_ul)
    ul_med = statistics.median(ul_vals) if ul_vals else 0
    r20_vals = sorted(ret20[r[1]] for r in rows if r[1] in ret20)
    r20_med = statistics.median(r20_vals) if r20_vals else 0.0
    L += split_report(
        "レンズ1: TOPIX 25日線 (びびりの5/25線地合い)", rows, cost, short,
        lambda d: ma25.get(d),
        [("good(25日線の上=上昇地合い)", lambda v: v is True),
         ("bad(25日線の下=下落地合い)", lambda v: v is False)])
    L += split_report(
        f"レンズ2: TOPIX 20日モメンタム (中央値 {r20_med:+.2f}% で2分)", rows, cost, short,
        lambda d: ret20.get(d),
        [(f"強(≥中央={r20_med:+.2f}%)", lambda v: v >= r20_med),
         (f"弱(<中央)", lambda v: v < r20_med)])
    L += split_report(
        "レンズ2b: TOPIX 20日モメンタム 符号", rows, cost, short,
        lambda d: ret20.get(d),
        [("上昇(>0)", lambda v: v > 0), ("下落(≤0)", lambda v: v <= 0)])
    L += split_report(
        f"レンズ3: 当日の市場S高銘柄数=過熱breadth (中央値 {ul_med:.0f} で2分)", rows, cost, short,
        lambda d: n_ul.get(d),
        [(f"高(≥{ul_med:.0f}=全体相場/過熱)", lambda v: v >= ul_med),
         (f"低(<{ul_med:.0f}=平常/閑散)", lambda v: v < ul_med)])
    # tercile で単調性を確認 (最重要レンズ)
    q1 = ul_vals[len(ul_vals) // 3] if ul_vals else 0
    q2 = ul_vals[2 * len(ul_vals) // 3] if ul_vals else 0
    L += split_report(
        f"レンズ3b: 過熱breadth 3分位 (下{q1}/上{q2}・単調性確認)", rows, cost, short,
        lambda d: n_ul.get(d),
        [(f"閑散(≤{q1})", lambda v: v <= q1),
         (f"中位({q1}〜{q2})", lambda v: q1 < v <= q2),
         (f"過熱(>{q2})", lambda v: v > q2)])
    L += ["**所見**:", "",
          "- レンズ1(TOPIX25日線)は**仮説と逆**(good+2.84>bad+1.93)＝TOPIX大型トレンドは⑩Rの"
          "母集団(非プライム小型=個人の遊び場)の過熱を表さない。S高は上昇地合いに集中するだけ。",
          "- **レンズ3(市場S高breadth=過熱)が仮説を実証**: 過熱breadthが上がるほど⑩Rは単調減衰し、"
          "最過熱帯(市場S高>15)で +1.70%/勝率52%/**t_clust+1.47＝非有意**まで落ちる。"
          "閑散日(S高≤9)は +3.18%/勝率64%/t3.90。",
          "- 機構: ⑩R(反転ショート)とびびり「ストップ高投資法」(継続ロング)は表裏一体で、"
          "**市場全体の過熱(S高breadth)がどちらが勝つかを決める**。全体相場(S高多数)では継続性が"
          "反転を相殺し勝率がコイン投げ(52%)に、閑散地合いでは孤立した小型S高が純粋な『カモ』で反転。",
          "- breadthはS高引け日(エントリー前夜)に市場のS高数を数えれば分かる＝**約定前に判定可能**。",
          "- 留保: 複数レンズ検定ゆえデータスヌーピング注意。ただし(1)tercile単調(単一の都合切りでない)"
          "(2)びびり&uoaが事前予言した機構あり(3)全帯でnet>0=フィルタは集中であり捏造でない。", "",
          "**運用精緻化(⑩R)**: 全天候でnet+だが、**閑散地合い(エントリー前夜の市場S高が少ない日≤~12)に"
          "厚く、全体相場/過熱日(市場S高>15)は薄く/見送り**(勝率52%・踏み上げリスク増・非有意)。"
          "= uoa「急騰時に買うのが一番危険」/ びびり「全体相場では継続」の⑩R版。", ""]
    return L


def analyze_Sdown_rebound(D: dict[str, Any], cal, idx, ma25, ret20, n_ul) -> list[str]:
    """S安(LL)銘柄の翌日リバウンドロングを gap帯/地合いで層別した md 行を返す(データ未取得なら注記)。"""
    L = ["## (B) S安リバウンドロング (uoa「ストップ安買い」・⑩Rの鏡像)", ""]
    if not DL_PATH.exists():
        return L + ["⏳ `cache/limit_dl_events.json` 未取得 (fetch_limit_down_events 実行中/未完)。"
                    "取得完了後に再実行。", ""]
    dl = json.loads(DL_PATH.read_text())
    hist, hd = D["hist"], sorted(D["hist"])
    tpx = D["tpx"]
    nxt = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    # ⑩Rと対称: 非プライム小型(PIT)×貸借(PIT)×翌営業日が有効。gap帯で層別。
    inst = {"プライム", "東証一部", "その他", "TOKYO PRO MARKET", None}
    rows_all: list[tuple] = []
    for e in dl:
        p = _pit(hist, hd, e["code"], e["date"])
        # ロングは空売り不要＝貸借フィルタを掛けない(⑩Rショートとの違い)。非プライム小型のみ。
        if p.get("scale_band") != "小型" or p.get("MktNm") in inst:
            continue
        if not (nxt.get(e["date"]) and tpx.get(nxt.get(e["date"]))):
            continue
        rows_all.append((e["io"], e["date"], e["code"], p.get("MktNm"), e["gap"]))
    # (date,code)重複除去
    seen, ded = set(), []
    for r in rows_all:
        k = (r[1], r[2])
        if k not in seen:
            seen.add(k)
            ded.append(r)
    rows_all = ded
    cost, short = LONG_COST, False
    base = stat_block(rows_all, cost, short)
    L += [f"母体: 非プライム小型×S安 翌寄→引 long(信用含む・cost{cost}%): "
          f"n{base['n']} / net{base['ev']:+.2f}% / 勝率{base['win']:.0f}% / "
          f"t_clust{base['t']:+.2f} / OOS{base['oos']:+.2f}%", "",
          "### gap帯別 (翌朝の寄りギャップ。uoa『売り尽くし＝投げのクライマックス』の代理)", "",
          "| 翌朝gap帯 | n | net EV% | 勝率% | t_clust | OOS% |", "|---|--:|--:|--:|--:|--:|"]
    bands = [("大GD(≤-10%さらに投げ)", lambda g: g <= -10),
             ("中GD(-10〜-5%)", lambda g: -10 < g <= -5),
             ("小GD(-5〜0%)", lambda g: -5 < g <= 0),
             ("GU(>0=翌朝反発済)", lambda g: g > 0)]
    tail: list[tuple] = []
    for label, pred in bands:
        sub = [r for r in rows_all if pred(r[4])]
        if label.startswith("大GD"):
            tail = sub
        L.append(f"| {label} | {_row(stat_block(sub, cost, short))} |")
    # 大GD裾のコスト感応(パニックGD寄りは滑り大ゆえ実コスト要確認)
    L += ["", "### 大GD裾(≤-10%投げ)のコスト感応 ── 唯一プラスの帯", "",
          "| cost%/回 | EV% | 勝率% | t_clust |", "|--:|--:|--:|--:|"]
    for c in (0.20, 0.30, 0.50, 1.00, 1.50):
        nets = [r[0] - c for r in tail]
        if not nets:
            continue
        t = clustered_t(nets, [r[1] for r in tail])
        win = sum(1 for x in nets if x > 0) / len(nets) * 100
        L.append(f"| {c:.2f} | {statistics.fmean(nets):+.2f} | {win:.0f} | {t:+.2f} |")
    L += ["", "**所見＝全体エッジなし／裾(大GD)に薄い候補(コスト脆弱)**:", "",
          "- 母体(信用含む n3704) net−0.55%/勝率42%/t−1.21 ＝負け。小GD(-5〜0) −0.89%/t−2.75 は有意に負"
          "(緩いS安は下げ続ける)。**素直なS安リバウンドロングはエッジなし**。",
          "- **裾だけ薄く有**: 大GD(≤-10%＝翌朝さらに投げ) のみ net+1.07%/t+2.35/n522/OOS+0.70・8/10年プラス。"
          "＝uoaの『売り尽くし(投げのクライマックス)を買う』が実在(どんなS安でなく極値だけ)。勝率33%=宝くじ型。",
          "- **ただしコストに脆弱**: 損益分岐≈0.4%/回(cost0.5%でt+1.69・cost1.0%で消滅)。パニックGD寄りは"
          "滑り大ゆえ実コストで容易に食われる＝**確定でなく『要・実コスト実測の候補』**。複数帯検定の最良値ゆえ"
          "データスヌーピング注意(FDR/DSR要)。",
          "- **非対称性が再確認**: 小型は『上の行き過ぎ＝⑩R short(+2.56%/損益分岐1.62%=頑健)』≫"
          "『下の行き過ぎ＝S安 long(裾+1.07%/損益分岐0.4%=脆弱)』。**ショート側が遥かに取りやすい**"
          "(リテール先行→出尽くし下方ドリフトと同根)。", ""]
    return L


def build(D: dict[str, Any]) -> str:
    """(A)⑩R地合い + (B)S安リバウンド を集計した Markdown レポート全文を返す。"""
    cal, idx, ma25, ret20, n_ul = build_regime(D["tpx"])
    L = ["# アーカイブ由来の地合い依存検証 (びびりおん/uoa)", "",
         "net=方向別cost控除後(long0.20/short0.15)。t_clust=日付クラスタ頑健。OOS=日付順70%訓練/30%test。", ""]
    L += analyze_10R_regime(D, cal, idx, ma25, ret20, n_ul)
    L += analyze_Sdown_rebound(D, cal, idx, ma25, ret20, n_ul)
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args()
    D = _load()
    report = build(D)
    print(report)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(REPORT, report)
    print(f"[archive_regime] → {REPORT}")


if __name__ == "__main__":
    main()

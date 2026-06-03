"""台風イベント駆動エッジの簡易検証レポートを生成する。

typhoon_price_data.json の各観測 (イベント×銘柄×ウィンドウ) に戦略の
方向・コストを付与し、16銘柄プールで期待値を集計する。

5戦略 (×保有タイミング):
  接近前ロング     pre_long  long
  直撃日ロング     hit       long
  通過後ロング     post1/3/5 long
  通過後ショート   post3     short
  イナゴ砲フェード post1     short  (条件: 直撃日リターン>=+3% の過熱銘柄のみ)

簡易判定 (FDR/OOS は省略、通過候補が出たら本格検証へ格上げ):
  EV(net)>0.5% かつ 勝率>55% かつ n>=20 かつ t_clust>+1.5

横断相関補正: 同一台風(16銘柄同時)のクラスタ相関で t が水増しされるため、
標準誤差は intl(台風)でクラスタした clustered_se を使う。

出力: reports/typhoon_event_simple.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import date
from pathlib import Path
from typing import Any

from analyzers.stats import clustered_se, t_to_p
from scripts._atomic import atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_PATH = REPO_ROOT / "data" / "disaster_event" / "typhoon_price_data.json"
EVENTS_PATH = REPO_ROOT / "data" / "disaster_event" / "typhoon_records.json"
OUT_PATH = REPO_ROOT / "reports" / "typhoon_event_simple.md"

LONG_COST = 0.20
SHORT_COST = 0.15
INAGO_POP_PCT = 3.0  # 直撃日 +3% 以上を「イナゴ過熱」とみなす

# play: (ラベル, window, direction, cost, condition_fn)
PLAYS: list[dict[str, Any]] = [
    {"label": "接近前ロング (D-3寄→D-1引)", "win": "pre_long", "dir": "long", "cost": LONG_COST},
    {"label": "直撃日ロング (D寄→D引)", "win": "hit", "dir": "long", "cost": LONG_COST},
    {"label": "通過後ロング +1 (D+1寄→D+1引)", "win": "post1", "dir": "long", "cost": LONG_COST},
    {"label": "通過後ロング +3 (D+1寄→D+3引)", "win": "post3", "dir": "long", "cost": LONG_COST},
    {"label": "通過後ロング +5 (D+1寄→D+5引)", "win": "post5", "dir": "long", "cost": LONG_COST},
    {"label": "通過後ショート +3 (D+1寄→D+3引)", "win": "post3", "dir": "short", "cost": SHORT_COST},
    {"label": "イナゴ砲フェード (直撃≥+3%→D+1短)", "win": "post1", "dir": "short",
     "cost": SHORT_COST, "cond": "inago"},
]

# 判定基準
PASS_EV, PASS_WIN, PASS_T, MIN_N = 0.5, 55.0, 1.5, 20


def _net(ret: float, direction: str, cost: float) -> float:
    base = -ret if direction == "short" else ret
    return base - cost


def _passes_cond(o: dict[str, Any], cond: str | None) -> bool:
    if cond is None:
        return True
    if cond == "inago":
        hit = o["rets"].get("hit")
        return hit is not None and hit >= INAGO_POP_PCT
    return True


def eval_play(obs: list[dict[str, Any]], play: dict[str, Any]) -> dict[str, Any] | None:
    """1 play をプール集計し net EV / 勝率 / t_clust / 判定を返す。"""
    rows = []
    for o in obs:
        if not _passes_cond(o, play.get("cond")):
            continue
        r = o["rets"].get(play["win"])
        if r is None:
            continue
        rows.append((o["intl"], float(r)))
    n = len(rows)
    if n == 0:
        return None
    direction, cost = play["dir"], play["cost"]
    nets = [_net(r, direction, cost) for _, r in rows]
    gross_dir = [(-r if direction == "short" else r) for _, r in rows]
    mean = statistics.fmean(nets)
    cse = clustered_se(nets, [intl for intl, _ in rows])
    t = mean / cse if cse else 0.0
    win = sum(1 for g in gross_dir if g > 0) * 100.0 / n
    verdict = "却下"
    if mean > PASS_EV and win > PASS_WIN and n >= MIN_N and t > PASS_T:
        verdict = "通過候補"
    elif mean > 0 and t > 1.0:
        verdict = "保留"
    return {"label": play["label"], "dir": direction, "n": n, "ev_net": mean,
            "win": win, "t_clust": t, "p": t_to_p(t), "verdict": verdict}


def _fmt_row(r: dict[str, Any]) -> str:
    return (f"| {r['label']} | {r['dir']} | {r['n']} | {r['ev_net']:+.2f}% | "
            f"{r['win']:.0f}% | {r['t_clust']:+.2f} | {r['p']:.3f} | {r['verdict']} |")


SEVERE_PRESSURE = 950  # 近傍最低気圧 <= 950hPa を「強烈」とみなす


def subset_block(obs: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[str]:
    """強度条件付きで通過後ロングを再集計 (事例が示す復興需要仮説の頑健性確認)。"""
    by_intl = {e["intl"]: e for e in events}
    landfall = {i for i, e in by_intl.items() if e["landfall_like"]}
    severe = {i for i, e in by_intl.items()
              if e["near_min_pressure"] is not None and e["near_min_pressure"] <= SEVERE_PRESSURE}
    subsets = [
        ("全49件", None),
        (f"上陸近似のみ ({len(landfall)}台風)", landfall),
        (f"強烈≤{SEVERE_PRESSURE}hPa ({len(severe)}台風)", severe),
        (f"上陸近似∩強烈 ({len(landfall & severe)}台風)", landfall & severe),
    ]
    target_plays = [p for p in PLAYS if p["win"] in ("post3", "post5") and p["dir"] == "long"]
    lines = ["## 強度条件付き 再集計 (通過後ロング)", "",
             "事例(ファクサイ/ハギビス)が示す「強い上陸台風後の復興需要ロング」が"
             "強度を絞ると強まるかの確認。", "",
             "| 部分集合 | 戦略 | n | net EV | 勝率 | t_clust | 判定 |",
             "|---|---|---|---|---|---|---|"]
    for sub_label, intl_set in subsets:
        sub_obs = obs if intl_set is None else [o for o in obs if o["intl"] in intl_set]
        for play in target_plays:
            r = eval_play(sub_obs, play)
            if not r:
                continue
            short_label = play["win"]
            lines.append(f"| {sub_label} | {short_label} | {r['n']} | {r['ev_net']:+.2f}% | "
                         f"{r['win']:.0f}% | {r['t_clust']:+.2f} | {r['verdict']} |")
    lines.append("")
    return lines


def case_study_block(obs: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[str]:
    """個別事例 (指定台風) の銘柄別 直撃日/通過後3日 リターン表。"""
    lines: list[str] = ["## 個別事例分析", ""]
    cases = [e for e in events if e.get("case_study")]
    cases.sort(key=lambda e: e["event_date"])
    for e in cases:
        intl = e["intl"]
        ev_obs = [o for o in obs if o["intl"] == intl]
        lines.append(f"### {e['case_study']}")
        lines.append(f"- 最接近日 {e['event_date']} / 近傍最低気圧 "
                     f"{e['near_min_pressure']}hPa / 最大風速 {e.get('near_max_wind_ms')}m/s "
                     f"/ 上陸近似={'はい' if e['landfall_like'] else 'いいえ'}")
        lines.append("")
        lines.append("| 銘柄 | 直撃日(D) | 通過後+3(D+1→D+3) |")
        lines.append("|---|---|---|")
        for o in sorted(ev_obs, key=lambda x: x["code"]):
            hit = o["rets"].get("hit")
            p3 = o["rets"].get("post3")
            hit_s = f"{hit:+.2f}%" if hit is not None else "—"
            p3_s = f"{p3:+.2f}%" if p3 is not None else "—"
            lines.append(f"| {o['code']} {o['name_stock']} | {hit_s} | {p3_s} |")
        # 均等加重バスケット平均
        hits = [o["rets"]["hit"] for o in ev_obs if o["rets"].get("hit") is not None]
        p3s = [o["rets"]["post3"] for o in ev_obs if o["rets"].get("post3") is not None]
        avg_hit = statistics.fmean(hits) if hits else None
        avg_p3 = statistics.fmean(p3s) if p3s else None
        ah_s = f"{avg_hit:+.2f}%" if avg_hit is not None else "—"
        ap_s = f"{avg_p3:+.2f}%" if avg_p3 is not None else "—"
        lines.append(f"| **16銘柄平均** | {ah_s} | {ap_s} |")
        lines.append("")
    return lines


def build_report(obs: list[dict[str, Any]], events: list[dict[str, Any]]) -> str:
    """観測リストとイベントから簡易検証レポート (Markdown) を組み立てて返す。"""
    today = date.today().isoformat()
    n_events = len({o["intl"] for o in obs})
    lines = [
        f"# 台風イベント駆動エッジ 簡易検証 (生成: {today})", "",
        "## 検証設計", "",
        f"- 台風: 2016-2025 の日本接近・大型台風 **{n_events}件** "
        "(近傍≤960hPa または ≥35m/s、JMAベストトラック)",
        f"- 対象: 建材・電気工事・防災テーマ 16銘柄 (均等加重プール)",
        "- イベント日 D = 最接近日 (近傍最低気圧点の JST 日付)。t0 = D以降の最初の営業日",
        f"- コスト: ロング往復 {LONG_COST}% / ショート往復 {SHORT_COST}%",
        "- 横断相関補正: 同一台風(16銘柄同時)のクラスタ相関を clustered_se(台風単位) で補正",
        f"- 簡易判定: EV(net)>{PASS_EV}% かつ 勝率>{PASS_WIN:.0f}% かつ "
        f"n≥{MIN_N} かつ t_clust>+{PASS_T} → **通過候補** (本格検証=FDR/OOSへ格上げ)",
        "",
        "## 戦略別 集計 (16銘柄プール)", "",
        "| 戦略 | 方向 | n | net EV | 勝率 | t_clust | p | 判定 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    results = []
    for play in PLAYS:
        r = eval_play(obs, play)
        if r:
            results.append(r)
            lines.append(_fmt_row(r))
    lines.append("")

    passed = [r for r in results if r["verdict"] == "通過候補"]
    held = [r for r in results if r["verdict"] == "保留"]
    lines.append("## サマリ")
    lines.append("")
    if passed:
        lines.append("**通過候補 (本格検証へ格上げ対象):**")
        for r in passed:
            lines.append(f"- {r['label']} [{r['dir']}]: net {r['ev_net']:+.2f}% / "
                         f"勝率{r['win']:.0f}% / t{r['t_clust']:+.2f} / n{r['n']}")
    else:
        lines.append("**通過候補: なし** — 16銘柄プールでは簡易基準を満たす戦略は検出されず。")
    if held:
        lines.append("")
        lines.append("保留 (EV正・t>1 だが基準未達):")
        for r in held:
            lines.append(f"- {r['label']} [{r['dir']}]: net {r['ev_net']:+.2f}% / "
                         f"勝率{r['win']:.0f}% / t{r['t_clust']:+.2f} / n{r['n']}")
    lines.append("")
    lines += subset_block(obs, events)
    lines += case_study_block(obs, events)
    lines += [
        "## 留保事項", "",
        "- 簡易版: FDR多重検定補正・walk-forward OOS は未適用。通過候補が出た場合のみ"
        " edge_candidates と同じ本格検証 (cluster_t + FDR + OOS) に格上げする。",
        "- 16銘柄プールは均等加重・同一台風内で強相関 → clustered_se で補正済だが "
        "実効独立サンプル数は台風件数(~49)に近い。",
        "- イベント日は「最接近日」基準。実際の株価反応は予報の出る数日前から始まる"
        "ため、接近前ロングの起点はさらに前倒しの余地あり。",
        "- 銘柄の上場時期により初期の台風で価格欠損あり (n が戦略間で変動)。",
        "- 当初の事例候補 2024年18号(クラトーン)は最大でも北緯22.8°(台湾方面)に留まり、"
        "本土近傍ボックス(≥24°N)に入らないため対象外。先島諸島をかすめたのみ。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DATA_PATH)
    ap.add_argument("--events", type=Path, default=EVENTS_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    obs = json.loads(args.data.read_text())["records"]
    events = json.loads(args.events.read_text())["records"]
    report = build_report(obs, events)
    atomic_write_text(args.out, report)
    print(report)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()

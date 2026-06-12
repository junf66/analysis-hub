"""確定エッジの生存・DD分布を Monte Carlo trade-bootstrap で出す。

validate_edges は α/t/FDR/OOS まで。本スクリプトはその先の「実際に張ったとき
どれだけ食らうか」を埋める。資金管理/ケリー/ファットテール連載の指摘どおり、
低勝率・高ペイオフ型と βフル戦略は平均αでなく DD分布・破産確率で可否が決まる。

対象 (per-trade net 損益列を渡して analyzers.survival.bootstrap_survival):
  ⑩ 中型S高×翌寄→大引け long : 現物/S株(無ヘッジ)で felt は raw io。net = io - long_cost。
                               勝率54%の宝くじ型 → 連敗分布・資金半減率を直視する。
  月次12-1モメンタム (大型+中型) : 長期ロング・バスケット(βフル)。equity は絶対 port 月次。
                               モメンタム・クラッシュの最大DD分布を出す。

賭け比率 f を複数振って非線形な破滅感応度を見る。

出力: reports/edge_survival.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from analyzers.survival import bootstrap_survival
from scripts._atomic import atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
LIMIT_UL_PATH = REPO_ROOT / "cache" / "limit_ul_events.json"
MOM_CACHE = REPO_ROOT / "cache" / "universe_bars.json"
REPORT_PATH = REPO_ROOT / "reports" / "edge_survival.md"

LONG_COST = 0.20  # ロング往復コスト%(日興込み安全側、validate_edges と整合)


def midcap_s_high_returns() -> list[float]:
    """⑩ 中型S高: 翌寄→大引け raw io から long コストを引いた per-trade net%(無ヘッジ)。"""
    if not (LIMIT_UL_PATH.exists() and MASTER_PATH.exists()):
        return []
    scale = {m["Code"]: m.get("scale_band")
             for m in json.loads(MASTER_PATH.read_text()).get("records", [])}
    out: list[float] = []
    for e in json.loads(LIMIT_UL_PATH.read_text()):
        code5 = e["code"] + "0" if len(e["code"]) == 4 else e["code"]
        if scale.get(code5) != "中型":
            continue
        io = e.get("io")
        if io is None:
            continue
        out.append(float(io) - LONG_COST)
    return out


def momentum_12_1_monthly() -> tuple[list[float], list[float]]:
    """月次12-1: (絶対 port 月次%列, α月次%列) を cache から再現して返す。"""
    if not (MOM_CACHE.exists() and MASTER_PATH.exists() and TOPIX_PATH.exists()):
        return [], []
    from scripts.edge_candidates.analyze_momentum_variants import (
        _rebalance_idx, metric_12_1, run_selection)
    recs = json.loads(MASTER_PATH.read_text())["records"]
    codes_band = {r["Code"]: r.get("scale_band") for r in recs}
    topix = {r["Date"]: r["C"] for r in json.loads(TOPIX_PATH.read_text())["records"] if r.get("C")}
    cal = sorted(topix)
    rebs = _rebalance_idx(cal)
    closes = json.loads(MOM_CACHE.read_text())
    closes = {c: m for c, m in closes.items()
              if m and codes_band.get(c) in ("大型", "中型")}
    months = run_selection(closes, cal, rebs, topix, metric_12_1, use_trend=True, top_n=20)
    return [x["port"] for x in months], [x["alpha"] for x in months]


def _fmt_block(title: str, note: str, rets: list[float], fs: list[float],
               n_paths: int) -> list[str]:
    """1 系列について複数の賭け比率 f で生存統計表を作る。"""
    L = [f"## {title}", "", note, ""]
    if len(rets) < 2:
        return L + ["(データ不足)", ""]
    L += ["| f(賭比) | 最大DD中央 | 最大DD worst5% | P(DD≥30%) | P(資金半減) | 最大連敗中央 | 最大連敗worst5% | 終端中央 | P(損失) |",
          "|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for f in fs:
        s = bootstrap_survival(rets, f=f, n_paths=n_paths)
        L.append(
            f"| {f:.2f} | {s['mdd_median']*100:.0f}% | {s['mdd_p95']*100:.0f}% | "
            f"{s['p_dd30']*100:.0f}% | {s['p_ruin_half']*100:.1f}% | "
            f"{s['streak_median']:.0f} | {s['streak_p95']:.0f} | "
            f"{s['end_median']:.2f}x | {s['p_loss']*100:.0f}% |")
    s0 = bootstrap_survival(rets, f=fs[0], n_paths=n_paths)
    L += ["", f"per-trade: 平均 net {s0['per_trade_mean']:+.2f}% / 勝率 {s0['per_trade_win']*100:.0f}% "
          f"/ n={s0['n_trades']} / horizon={s0['horizon']}トレード(=1巡)。", ""]
    return L


def build_report(*, n_paths: int) -> str:
    """⑩ と月次モメンタムの生存分析 md を返す。"""
    L = ["# 確定エッジ 生存・DD分布 (Monte Carlo trade-bootstrap)", "",
         f"per-trade net 損益を IID 復元抽出して合成エクイティを {n_paths} 経路生成。",
         "1経路 horizon = 母集団1巡分。最大DD/連敗は経路横断分布、P(…)は到達確率。",
         "**平均αでなく『生き残れるか』の指標。低勝率・βフルほど f を絞る判断材料。**", ""]
    midcap = midcap_s_high_returns()
    L += _fmt_block(
        "⑩ 中型S高×翌寄→大引け long (現物/S株・無ヘッジ raw io)",
        "勝率54%の宝くじ型。1トレード=1銘柄。f=資本に対する1銘柄の比率。"
        "全張り(f=1)は単名集中=危険、分散運用は実効 f を小さくする。",
        midcap, [1.0, 0.5, 0.25, 0.10], n_paths)
    port, alpha = momentum_12_1_monthly()
    L += _fmt_block(
        "月次12-1モメンタム (大型+中型・絶対リターン=βフル)",
        "1ステップ=1か月の絶対 port 月次%。equity=Π(1+port)。モメンタム・クラッシュの DD を直視。"
        " f=1.0 がフル投資。最大連敗=連続マイナス月。",
        port, [1.0, 0.5], n_paths)
    if len(alpha) >= 2:
        L += _fmt_block(
            "月次12-1モメンタム (TOPIXヘッジ後 α=市場中立版・参考)",
            "β控除後 α 月次%。市場ベータを抜いた純戦略の揺れ。実運用でヘッジする場合の目安。",
            alpha, [1.0], n_paths)
    L += ["## 読み方",
          "- **P(資金半減)** が無視できない f は実運用不可 (回復に倍返しが要る/心理が折れる)。",
          "- ⑩は宝くじ型ゆえ最大連敗が長い → 連敗に耐える資金管理＋小さい実効 f が前提。",
          "- 月次モメンタムの最大DD worst5% が βフルの正体。ハーフ/クォーターで張る根拠。",
          "- これは『そのエッジを張ってよい上限サイズ』を決める材料 (ケリーの上限参照値と併用)。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paths", type=int, default=20000, help="MC 経路数 (既定 20000)")
    ap.add_argument("--out", type=Path, default=REPORT_PATH, help="出力 md")
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, build_report(n_paths=args.paths))
    print(f"[survival] → {args.out}")


if __name__ == "__main__":
    main()

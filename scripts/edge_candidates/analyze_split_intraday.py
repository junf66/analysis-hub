"""株式分割 #4 の「翌営業日(+1日)のどの時刻で入るのが最良か」を確定する分析。

split_multiday.json に enrich_split_intraday が付与した px_930 / px_1130 /
px_close と、entry_open + d{N}_ret から、エントリー時刻 4 種 × 保有日数 N の
リターンを算出して比較する (再フェッチ不要。intraday サブセット=分足契約
2024-06以降のみ)。

エントリー時刻:
  寄り  : entry_open (= 翌営業日 始値)
  9:30 : px_930
  11:30: px_1130 (前場引け)
  引け  : px_close (翌営業日 大引け)
出口は全て +N 営業日後の引け (close_N = entry_open*(1+d{N}/100) で復元)。

指標は方向=ロング・往復コスト LONG_COST 控除後の net。TOPIX(β=1)超過 α も併記:
  共通ベンチマーク = TOPIX(翌営業日始値→+N引け)。エントリー時刻は株式側のみ
  変えるので、同一ベンチマークに対する α の大小でエントリー時刻の優劣を見る。

出力: reports/edge_candidates_detail/split_intraday.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from analyzers.stats import benjamini_hochberg, clustered_se, t_to_p
from scripts._atomic import atomic_write_text
from scripts.edge_candidates.topix_adjust import load_topix, topix_return

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPLIT_PATH = REPO_ROOT / "data" / "edge_candidates" / "split_multiday.json"
OUT_PATH = REPO_ROOT / "reports" / "edge_candidates_detail" / "split_intraday.md"

LONG_COST = 0.20
DAYS = [3, 5, 10]
# (ラベル, 価格 attr キー or "open")
ENTRIES = [("寄り", "open"), ("9:30", "px_930"), ("11:30", "px_1130"), ("引け", "px_close")]


def _entry_ret(a: dict[str, Any], price_key: str, n: int) -> float | None:
    """エントリー時刻 price_key, 保有 n 日の生リターン% (entry→+N引け)。"""
    dn = a.get(f"d{n}_ret")
    eo = a.get("entry_open")
    if dn is None or not eo:
        return None
    if price_key == "open":
        return dn  # entry_open→+N引け = d{n}_ret そのもの
    px = a.get(price_key)
    if not px:
        return None
    close_n = eo * (1 + dn / 100.0)
    return (close_n / px - 1) * 100.0


def _stats(obs: list[tuple[str, float]], cost: float) -> dict[str, Any]:
    """(date, ret) 群の net EV / clustered_t / 勝率 / p / OOS を返す (ロング)。"""
    n = len(obs)
    nets = [v - cost for _, v in obs]
    mean = statistics.fmean(nets)
    cse = clustered_se(nets, [d for d, _ in obs])
    t = mean / cse if cse else 0.0
    win = sum(1 for _, v in obs if v > 0) * 100.0 / n if n else 0.0
    so = sorted(obs, key=lambda x: x[0])
    test = so[int(n * 0.7):]
    oos = statistics.fmean([v - cost for _, v in test]) if test else None
    return {"n": n, "net": mean, "t": t, "win": win, "p": t_to_p(t), "oos": oos}


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    """intraday サブセットでエントリー時刻×保有日数の raw/α 集計を返す。"""
    topix = load_topix()
    sub = [r for r in records if (r.get("attrs") or {}).get("px_930") is not None
           and (r.get("attrs") or {}).get("entry_open")]
    avail_days = [n for n in DAYS
                  if any((r["attrs"]).get(f"d{n}_ret") is not None for r in sub)]

    raw: dict[tuple[str, int], dict[str, Any]] = {}
    alpha: dict[tuple[str, int], dict[str, Any]] = {}
    all_p: list[float] = []
    keys: list[tuple[str, int]] = []
    for n in avail_days:
        for label, pk in ENTRIES:
            raw_obs: list[tuple[str, float]] = []
            a_obs: list[tuple[str, float]] = []
            for r in sub:
                a = r["attrs"]
                ed = a.get("entry_date")
                ret = _entry_ret(a, pk, n)
                if ret is None or not ed:
                    continue
                raw_obs.append((ed, ret))
                tr = topix_return(topix, ed, n)
                if tr is not None:
                    a_obs.append((ed, ret - tr))
            if not raw_obs:
                continue
            raw[(label, n)] = _stats(raw_obs, LONG_COST)
            alpha[(label, n)] = _stats(a_obs, LONG_COST)
            keys.append((label, n))
            all_p.append(alpha[(label, n)]["p"])
    fdr = dict(zip(keys, benjamini_hochberg(all_p))) if all_p else {}
    return {"n_subset": len(sub), "avail_days": avail_days,
            "raw": raw, "alpha": alpha, "fdr": fdr,
            "period": (min((r["attrs"]["entry_date"] for r in sub), default="?"),
                       max((r["attrs"]["entry_date"] for r in sub), default="?"))}


def build_report(res: dict[str, Any]) -> str:
    """analyze() の結果からエントリー時刻比較レポート (Markdown) を組み立てる。"""
    raw, alpha, fdr = res["raw"], res["alpha"], res["fdr"]
    days = res["avail_days"]
    lines = [
        "# 株式分割 #4 — 翌営業日のエントリー時刻比較 (intraday)", "",
        f"- サブセット: 分足契約 2024-06以降の分割イベント **{res['n_subset']}件** "
        f"(entry_date {res['period'][0]}〜{res['period'][1]})",
        f"- 指標: ロング net (往復コスト {LONG_COST}% 控除)。α=TOPIX(β=1, 翌寄→+N引)超過",
        "- クラスタ頑健 t は entry_date でクラスタ。FDR は α の p に適用 (★)",
        "- **注**: 全期間 #4α (n937, 2021-2026) とは母集団が違う (本表は直近2年の小標本)。"
        "目的は**エントリー時刻の相対優劣**の確定。",
        "",
    ]
    for n in days:
        lines.append(f"## +{n}日保有 (翌寄→+{n}引)")
        lines.append("")
        lines.append("| エントリー | n | raw net | α net | t_clust(α) | 勝率 | p(α) | FDR |")
        lines.append("|---|---|---|---|---|---|---|---|")
        rows = [(lab, raw[(lab, n)], alpha[(lab, n)]) for lab, _ in ENTRIES if (lab, n) in raw]
        for lab, rw, al in rows:
            mark = "★" if fdr.get((lab, n)) else ""
            lines.append(f"| {lab} | {rw['n']} | {rw['net']:+.2f}% | {al['net']:+.2f}% | "
                         f"{al['t']:+.2f} | {al['win']:.0f}% | {al['p']:.3f} | {mark} |")
        if rows:
            best = max(rows, key=lambda x: x[2]["net"])
            lines.append("")
            lines.append(f"→ **最良エントリー: {best[0]}** (α net {best[2]['net']:+.2f}% / "
                         f"t {best[2]['t']:+.2f})")
        lines.append("")
    # まとめ: 寄り vs 引け の差 (= 寄→引で失う/得る分)
    lines.append("## 所見")
    lines.append("")
    for n in days:
        if ("寄り", n) in alpha and ("引け", n) in alpha:
            diff = alpha[("寄り", n)]["net"] - alpha[("引け", n)]["net"]
            lines.append(f"- +{n}日: 寄り α {alpha[('寄り', n)]['net']:+.2f}% vs "
                         f"引け α {alpha[('引け', n)]['net']:+.2f}% "
                         f"(寄り−引け = {diff:+.2f}%)")
    lines.append("")
    lines.append("留保: β=1 近似。intraday サブセットは n が小さく直近2年偏重のため、"
                 "絶対水準より相対順位を信頼すること。全期間の絶対 α は #4α.md を参照。")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", type=Path, default=SPLIT_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    recs = json.loads(args.split.read_text())["records"]
    res = analyze(recs)
    report = build_report(res)
    atomic_write_text(args.out, report)
    print(report)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()

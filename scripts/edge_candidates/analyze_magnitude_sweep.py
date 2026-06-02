"""kouaku 全サブパターンの「程度の死角」掃討: magnitude 三分位で隠れエッジを炙り出す。

分類器がタグ内で magnitude を二値化(genshu=any<-10% 等)するため、程度差で効く/効かない
が潰れている死角を、各 (subpattern × 開示時刻) を埋め込み magnitude(NP YoY・来期修正・
配当修正%)の三分位に割って net EV / 日付クラスタ頑健t を算出。全セル横断 BH-FDR で
隠れエッジを判定。新規フェッチ不要(既存 kouaku_records の factors metric を使用)。

ロング net=EV-0.20% / ショート net=-EV-0.15% の有利側を採用。約定可能(大引け後/引け間際/寄前)。
出力: reports/magnitude_sweep.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from analyzers.stats import benjamini_hochberg, clustered_se, t_to_p
from scripts._atomic import atomic_write_text
from scripts._buckets import disc_bucket

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PO_PATH = REPO_ROOT / "data" / "kouaku_records.json"
OUT_PATH = REPO_ROOT / "reports" / "magnitude_sweep.md"
TRADABLE = {"大引け後", "引け間際", "寄前"}
LONG_COST, SHORT_COST = 0.20, 0.15
MIN_CELL_N = 30
MIN_SP_N = 90          # サブパターン×バケットの最小母数(三分位で各≥30確保の目安)


def primary_mag(r: dict[str, Any]) -> float | None:
    """bad→good の順で最初の %メトリクス(程度)を返す。"""
    for fac in (r.get("bad_factors") or []) + (r.get("good_factors") or []):
        for k, v in (fac.get("metric") or {}).items():
            if isinstance(v, (int, float)) and "pct" in k.lower():
                return float(v)
    return None


def _long_ret(r: dict[str, Any]) -> tuple[str, float] | None:
    a = r.get("attrs") or {}
    if a.get("limit_locked"):
        return None
    v = a.get("next_day_open_to_close_ret")
    d = r.get("event_date")
    return (d, float(v)) if v is not None and d else None


def cell_stats(recs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """有利方向(long/short)の net EV / クラスタt / 勝率 を返す。"""
    obs = [x for x in (_long_ret(r) for r in recs) if x]
    if len(obs) < MIN_CELL_N:
        return None
    longs = [v for _, v in obs]
    mean = statistics.fmean(longs)
    direction = "long" if mean >= 0 else "short"
    cost = LONG_COST if direction == "long" else SHORT_COST
    nets = [(v - cost) if direction == "long" else (-v - cost) for v in longs]
    net_ev = statistics.fmean(nets)
    cse = clustered_se(nets, [d for d, _ in obs])
    t = net_ev / cse if cse else 0.0
    win = sum(1 for x in nets if x > 0) * 100.0 / len(nets)
    return {"n": len(nets), "dir": direction, "net_ev": net_ev, "t_clust": t,
            "win": win, "p": t_to_p(t)}


def sweep(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """(subpattern × tradableバケット) を magnitude 三分位に割って全セルを集める。"""
    from collections import defaultdict
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in records:
        b = disc_bucket(r)
        if b in TRADABLE and r.get("subpattern") and primary_mag(r) is not None:
            groups[(r["subpattern"], b)].append(r)
    cells = []
    for (sp, bkt), recs in groups.items():
        if len(recs) < MIN_SP_N:
            continue
        recs.sort(key=primary_mag)
        n = len(recs)
        thirds = [recs[:n // 3], recs[n // 3:2 * n // 3], recs[2 * n // 3:]]
        labels = ["弱(magnitude小)", "中", "強(magnitude大)"]
        mags = [primary_mag(r) for r in recs]
        ranges = [(mags[0], mags[n // 3 - 1]), (mags[n // 3], mags[2 * n // 3 - 1]), (mags[2 * n // 3], mags[-1])]
        for part, lab, (lo, hi) in zip(thirds, labels, ranges):
            s = cell_stats(part)
            if s:
                s.update({"sp": sp, "bucket": bkt, "tercile": lab, "mag_range": f"{lo:+.0f}〜{hi:+.0f}%"})
                cells.append(s)
    if cells:
        for c, f in zip(cells, benjamini_hochberg([c["p"] for c in cells], 0.05)):
            c["fdr_significant"] = f
    return cells


def write_report(records: list[dict[str, Any]], *, out_path: Path = OUT_PATH) -> Path:
    """掃討結果を Markdown 出力。"""
    import datetime
    cells = sweep(records)
    survivors = [c for c in cells if c.get("fdr_significant") and c["net_ev"] > 0.4]
    strong = sorted([c for c in cells if c["net_ev"] > 0.4],
                    key=lambda c: -c["t_clust"])[:20]
    L = [f"# kouaku 程度の死角 掃討 (magnitude三分位) ({datetime.date.today()})", "",
         f"検証セル {len(cells)} (subpattern×開示時刻×magnitude三分位, 約定可能のみ, 各n≥{MIN_CELL_N})。"
         "有利方向net (long0.20%/short0.15%控除) / 日付クラスタ頑健t / 全セル横断BH-FDR。", "",
         f"## FDR生存セル ({len(survivors)}件) = 二値化で潰れていた隠れエッジ", ""]
    if survivors:
        L += ["| subpattern | 時刻 | 程度三分位 | 範囲 | 方向 | n | net EV | t_clust | 勝率 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for c in sorted(survivors, key=lambda c: -c["t_clust"]):
            L.append(f"| {c['sp']} | {c['bucket']} | {c['tercile']} | {c['mag_range']} | {c['dir']} | "
                     f"{c['n']} | {c['net_ev']:+.2f}% | {c['t_clust']:+.2f} | {c['win']:.0f}% |")
    else:
        L.append("(FDR生存なし)")
    L += ["", "## 強候補 上位20 (net>0.4%, t降順, FDR前)", "",
          "| subpattern | 時刻 | 三分位 | 範囲 | 方向 | n | net EV | t_clust | 勝率 | FDR |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for c in strong:
        mark = "★" if c.get("fdr_significant") else ""
        L.append(f"| {c['sp']} | {c['bucket']} | {c['tercile']} | {c['mag_range']} | {c['dir']} | "
                 f"{c['n']} | {c['net_ev']:+.2f}% | {c['t_clust']:+.2f} | {c['win']:.0f}% | {mark} |")
    L += ["", "## メモ",
          "- 程度三分位で『弱/中/強』に割り、二値化タグで潰れていた magnitude 依存を炙り出す掃討。",
          "- FDR生存セルのみ実弾水準候補。FDR前の強候補は過剰最適化注意(方向一貫性で判断)。",
          "- 既知: zouhai_kahou_nx×大引け後short は中程度magnitudeが芯(本掃討でも確認)。"]
    atomic_write_text(out_path, "\n".join(L))
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=PO_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    records = json.loads(args.path.read_text())["records"]
    out = write_report(records, out_path=args.out)
    print(f"[magnitude_sweep] n={len(records)} → wrote {out}")


if __name__ == "__main__":
    main()

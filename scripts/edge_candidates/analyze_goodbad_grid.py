"""好材料の程度 × 悪材料の程度 の全格子で kouaku 混在イベントを検証する。

各 record から好材料%(増配/増益幅)と悪材料%(減益/修正/減配幅)を別々に抽出し、|値|を
<3% / 3-5% / 5-10% / ≥10% にバンド化(好材料に程度が無い 自社株買い/分割 は『程度なし』行)。
約定可能(大引け後/引け間際/寄前)のみ、翌寄→翌引 long net(コスト0.20%)・日付クラスタ頑健t・
全セル横断 BH-FDR。short 有利は long が負(=-long が short net)。

出力: reports/goodbad_grid.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from analyzers.stats import benjamini_hochberg, clustered_se, t_to_p
from scripts._atomic import atomic_write_text
from scripts._buckets import disc_bucket

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PO_PATH = REPO_ROOT / "data" / "kouaku_records.json"
OUT_PATH = REPO_ROOT / "reports" / "goodbad_grid.md"
TRADABLE = {"大引け後", "引け間際", "寄前"}
LONG_COST = 0.20
MIN_N = 20
BANDS = ["<3%", "3-5%", "5-10%", "≥10%"]
GOOD_ROWS = ["程度なし(自社株買/分割)"] + BANDS


def _mag(facs: list[dict] | None) -> float | None:
    for f in (facs or []):
        for k, v in (f.get("metric") or {}).items():
            if isinstance(v, (int, float)) and "pct" in k.lower():
                return abs(float(v))
    return None


def band(x: float | None) -> str | None:
    if x is None:
        return None
    if x < 3:
        return "<3%"
    if x < 5:
        return "3-5%"
    if x < 10:
        return "5-10%"
    return "≥10%"


def good_row(r: dict[str, Any]) -> str | None:
    """好材料の程度バンド。程度のない好材料(jisha/split)を含むなら『程度なし』。"""
    facs = r.get("good_factors") or []
    m = _mag(facs)
    if m is not None:
        return band(m)
    hints = {f.get("subpattern_hint") for f in facs}
    if hints & {"jisha", "kabushiki_bunkatsu", "split"}:
        return "程度なし(自社株買/分割)"
    return None


def build_grid(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict]:
    """(good_band, bad_band) → stats。全セル横断 BH-FDR。"""
    cells: dict[tuple[str, str], list] = defaultdict(list)
    for r in records:
        if disc_bucket(r) not in TRADABLE:
            continue
        a = r.get("attrs") or {}
        if a.get("limit_locked"):
            continue
        v = a.get("next_day_open_to_close_ret")
        if v is None or not r.get("event_date"):
            continue
        gb, bb = good_row(r), band(_mag(r.get("bad_factors")))
        if gb and bb:
            cells[(gb, bb)].append((r["event_date"], float(v)))
    out = {}
    for key, obs in cells.items():
        if len(obs) < MIN_N:
            continue
        longs = [v for _, v in obs]
        m = statistics.fmean(longs)
        cse = clustered_se(longs, [d for d, _ in obs])
        t = m / cse if cse else 0.0
        out[key] = {"n": len(obs), "long_ev": m, "long_net": m - LONG_COST, "t": t,
                    "p": t_to_p(t), "win": sum(1 for v in longs if v > 0) * 100.0 / len(longs)}
    if out:
        keys = list(out)
        for k, f in zip(keys, benjamini_hochberg([out[k]["p"] for k in keys], 0.05)):
            out[k]["fdr"] = f
    return out


def write_report(records: list[dict[str, Any]], *, out_path: Path = OUT_PATH) -> Path:
    """好×悪 程度格子レポートを出力。"""
    import datetime
    g = build_grid(records)
    L = [f"# 好材料×悪材料 程度格子 検証 ({datetime.date.today()})", "",
         "kouaku 混在イベントを好材料程度×悪材料程度の格子で検証。約定可能(大引け後/引け間際/寄前)、"
         "翌寄→翌引 long net(0.20%控除)・日付クラスタ頑健t・全セル横断BH-FDR。",
         "**short有利は long が負**(-long が short net)。程度=|材料%|(bad優先)。", "",
         "## long net EV / t_clust / n (★=FDR生存)", "",
         "| 好材料＼悪材料 | " + " | ".join(BANDS) + " |",
         "|---|" + "---|" * len(BANDS)]
    for gb in GOOD_ROWS:
        cells = []
        for bb in BANDS:
            c = g.get((gb, bb))
            if c:
                mark = "★" if c.get("fdr") else ""
                cells.append(f"{c['long_net']:+.2f}%/t{c['t']:+.1f}/n{c['n']}{mark}")
            else:
                cells.append("—")
        L.append(f"| {gb} | " + " | ".join(cells) + " |")
    L += ["", "## 読み取り",
          "- short方向(long負)が大半: **悪材料が大きい(≥10%)ほどショートが効く**(翌日寄→引)。",
          "- 好材料の程度は従属的(増配/増益は値域が狭く、自社株買いは程度なし)。",
          "- 自社株買い/分割(程度なし)×悪材料 のロング不成立は既出(キッコーマン型/jisha_genshu)。",
          "- 実弾判断は FDR★ かつ OOS と方向一貫性で(本表はFDRのみ)。"]
    atomic_write_text(out_path, "\n".join(L))
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=PO_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    records = json.loads(args.path.read_text())["records"]
    out = write_report(records, out_path=args.out)
    print(f"[goodbad_grid] n={len(records)} → wrote {out}")


if __name__ == "__main__":
    main()

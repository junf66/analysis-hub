"""エッジ候補の検証ランナー。候補設定に従いレコードを用意→検証→Markdownレポート。

現状サポート: #2 自社株買い単独 (buyback_reuse)。既存 buyback_records.json(価格付与済) を
TDnet索引の「同日悪材料」と減益で絞り、悪材料完全なしの自社株買いを検証する。
#1/#3/#4/#5/#7/#8/#9 は価格 enrich 等の追加取得後に対応 (順次拡張)。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.edge_candidates import lib
from scripts.edge_candidates.candidates import by_id

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUYBACK_PATH = REPO_ROOT / "data" / "buyback_records.json"
INDEX_PATH = REPO_ROOT / "data" / "edge_candidates" / "tdnet_index.json"
REPORT_DIR = REPO_ROOT / "reports" / "edge_candidates_detail"


def bad_material_keys(index_records: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """TDnet索引から、悪材料(bad_*タグ)を含む (code, event_date) の集合を返す。"""
    out: set[tuple[str, str]] = set()
    for r in index_records:
        if any(str(t).startswith("bad_") for t in (r.get("tags") or [])):
            out.add((r.get("code"), r.get("event_date")))
    return out


def filter_no_bad_material(buyback: list[dict[str, Any]],
                           bad_keys: set[tuple[str, str]]) -> list[dict[str, Any]]:
    """自社株買いから「同日悪材料あり」「減益見通し(forecast_decline<0)」を除外する。

    増配・上方修正等の追加好材料はあってもよい (除外しない)。
    """
    out = []
    for r in buyback:
        if (r.get("code"), r.get("event_date")) in bad_keys:
            continue
        fdp = (r.get("attrs") or {}).get("forecast_decline_pct")
        if fdp is not None and fdp < 0:  # 減益見通し併発 = #2対象外(④/kouaku領域)
            continue
        out.append(r)
    return out


def run_jisha_single() -> dict[str, Any]:
    """#2 自社株買い単独(悪材料なし)を検証し、詳細レポートを書いてサマリ行を返す。"""
    cfg = by_id("#2")
    buyback = json.loads(BUYBACK_PATH.read_text())["records"]
    index = json.loads(INDEX_PATH.read_text())["records"]
    bad = bad_material_keys(index)
    recs = filter_no_bad_material(buyback, bad)
    results = lib.validate_candidate(recs, exits=cfg["exits"])
    verdict, reason, _ = lib.judge(results, caveat_beta=cfg["caveat_beta"])
    reason = f"(母集団 {len(recs)}/{len(buyback)}件) " + reason
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lib.write_candidate_report(cfg["cid"], cfg["name"], results, verdict, reason,
                               out_dir=REPORT_DIR, caveats=cfg["dedup"])
    return {"cid": cfg["cid"], "name": cfg["name"], "verdict": verdict, "reason": reason}


_RUNNERS = {"#2": run_jisha_single}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidate", default="#2", help="検証する候補ID (現状 #2 のみ対応)")
    args = ap.parse_args()
    runner = _RUNNERS.get(args.candidate)
    if runner is None:
        raise SystemExit(f"未対応の候補: {args.candidate} (対応済: {sorted(_RUNNERS)})")
    row = runner()
    print(f"{row['cid']} {row['name']}: {row['verdict']} — {row['reason']}")


if __name__ == "__main__":
    main()

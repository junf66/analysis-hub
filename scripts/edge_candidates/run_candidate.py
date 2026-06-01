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
ENRICHED_PATH = REPO_ROOT / "data" / "edge_candidates" / "enriched_events.json"
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


def load_enriched_by_tag(tags_any: set[str]) -> list[dict[str, Any]]:
    """enriched_events.json から tags にいずれか該当する record を返す (#1/#3/#5用)。"""
    if not ENRICHED_PATH.exists():
        return []
    recs = json.loads(ENRICHED_PATH.read_text())["records"]
    return [r for r in recs if any(t in tags_any for t in (r.get("tags") or []))]


def _run_index_candidate(cid: str, tags_any: set[str], exclude_bad: bool) -> dict[str, Any]:
    """索引イベント (enriched) から候補レコードを作り検証→レポート出力→サマリ行を返す。"""
    cfg = by_id(cid)
    recs = load_enriched_by_tag(tags_any)
    if exclude_bad:
        index = json.loads(INDEX_PATH.read_text())["records"]
        recs = filter_no_bad_material(recs, bad_material_keys(index))
    results = lib.validate_candidate(recs, exits=cfg["exits"])
    verdict, reason, _ = lib.judge(results, caveat_beta=cfg["caveat_beta"])
    reason = f"(n={len(recs)}) {reason}"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lib.write_candidate_report(cfg["cid"], cfg["name"], results, verdict, reason,
                               out_dir=REPORT_DIR, caveats=cfg.get("dedup", ""))
    return {"cid": cfg["cid"], "name": cfg["name"], "verdict": verdict, "reason": reason}


def run_kessan_up() -> dict[str, Any]:
    """#1 上方修正発表翌日ロング を検証 (索引から good_kessan_up を抽出)。"""
    return _run_index_candidate("#1", {"good_kessan_up"}, exclude_bad=False)


def run_zouhai_single() -> dict[str, Any]:
    """#3 増配単独(悪材料なし)ロング を検証 (good_zouhai + 同日悪材料/減益除外)。"""
    return _run_index_candidate("#3", {"good_zouhai"}, exclude_bad=True)


def run_teikei_juchu() -> dict[str, Any]:
    """#5 業務提携・大型受注ロング を検証 (good_teikei または good_juchu)。"""
    return _run_index_candidate("#5", {"good_teikei", "good_juchu"}, exclude_bad=False)


def run_stock_split() -> dict[str, Any]:
    """#4 株式分割発表ロング を検証 (split_multiday の +5/+10日リターン)。

    enrich_multiday で価格付与済みの events を読み、cfg["exits"] (+5/+10日) で検証。
    caveat_beta=True なので通過しても「保留・要TOPIX再検証」。
    """
    cfg = by_id("#4")
    SPLIT_PATH = REPO_ROOT / "data" / "edge_candidates" / "split_multiday.json"
    recs = json.loads(SPLIT_PATH.read_text())["records"]
    results = lib.validate_candidate(recs, exits=cfg["exits"])
    verdict, reason, _ = lib.judge(results, caveat_beta=cfg["caveat_beta"])
    reason = f"(n={len(recs)}) {reason}"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lib.write_candidate_report(cfg["cid"], cfg["name"], results, verdict, reason,
                               out_dir=REPORT_DIR, caveats=cfg.get("dedup", ""))
    return {"cid": cfg["cid"], "name": cfg["name"], "verdict": verdict, "reason": reason}


_RUNNERS = {"#1": run_kessan_up, "#2": run_jisha_single,
            "#3": run_zouhai_single, "#4": run_stock_split, "#5": run_teikei_juchu}


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

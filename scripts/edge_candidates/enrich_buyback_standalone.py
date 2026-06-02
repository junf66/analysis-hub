"""単独 自社株買い決定 → 翌寄ロングの +N日リターン付与 (株式分割と同枠組み)。

td_buyback_decisions.json (公式 DiscItems=11105「自己株式の取得に係る事項の決定」)
を event source に、株式分割 (enrich_split_axes) と同じ手法で各イベントへ:
  - 翌寄り(entry)→ +1/+3/+5/+10日引けの素リターン (d{N}_ret)
  - TOPIX-α (alpha_d{N}_ret)
  - gap_pct / entry_price / turnover_20
  - scale_band (equities_master) / mrgn (信用区分)
  - combo: 同日他材料の有無で「単独 / 複合」を分類 (tdnet_index 同日タグ)
  - disc_bucket: 開示時刻帯 (大引け後 / 後場 / 場中 等)
を付与する。

自社株買い = 好材料 (good_jisha) のため、株式分割と同じく LONG を想定。
出力: data/edge_candidates/buyback_standalone_enriched.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json
from scripts.edge_candidates import topix_adjust
from scripts.edge_candidates.enrich_common import returns_from_bars

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUYBACK_PATH = REPO_ROOT / "data" / "edge_candidates" / "td_buyback_decisions.json"
TDNET_PATH = REPO_ROOT / "data" / "edge_candidates" / "tdnet_index.json"
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "buyback_standalone_enriched.json"
DAYS = [1, 3, 5, 10]


def _code4(code: str) -> str:
    """5桁(末尾0)を4桁化。tdnet_index は4桁 code 採用。"""
    return code[:-1] if len(code) == 5 and code.endswith("0") else code


def disc_bucket(disc_time: str) -> str:
    """開示時刻 (HH:MM) を取引時間帯バケットへ。"""
    if not disc_time or ":" not in disc_time:
        return "?"
    hh, mm = disc_time.split(":")[:2]
    try:
        m = int(hh) * 60 + int(mm)
    except ValueError:
        return "?"
    if m >= 15 * 60:
        return "大引け後"
    if m >= 12 * 60 + 30:
        return "後場"
    if m >= 11 * 60 + 30:
        return "昼休み"
    if m >= 9 * 60:
        return "前場"
    return "寄り前"


def build_sameday_tags(tdnet: list[dict[str, Any]]) -> dict[tuple[str, str], set[str]]:
    """(code4, event_date) → その日の全開示タグ集合。"""
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in tdnet:
        key = (r.get("code"), r.get("event_date"))
        if key[0] and key[1]:
            out[key].update(r.get("tags") or [])
    return out


def combo_label(tags: set[str]) -> str:
    """同日タグ集合から単独/複合を判定 (good_jisha 自身は除外)。"""
    others = {t for t in tags if t != "good_jisha"}
    if not others:
        return "単独"
    bad = any(t.startswith("bad_") for t in others)
    good = any(t.startswith("good_") for t in others)
    if bad and not good:
        return "悪材料同時"
    if good and not bad:
        return "好材料同時"
    return "複合(好悪混在)"


def axis_fields_from_bars(bars: list[dict[str, Any]], event_date: str) -> dict[str, Any]:
    """日足から gap_pct / entry_price / turnover_20 を計算 (純関数)。"""
    rows = sorted([b for b in bars if b.get("Date") and b.get("C") is not None],
                  key=lambda b: b["Date"])
    after = [b for b in rows if b["Date"] > event_date]
    before = [b for b in rows if b["Date"] <= event_date]
    out: dict[str, Any] = {}
    if not after or not before:
        return out
    entry, prev_close, o = after[0], before[-1].get("C"), after[0].get("O")
    if prev_close and o:
        out["gap_pct"] = (o / prev_close - 1.0) * 100.0
    out["entry_price"] = o
    vols = [(b.get("C") or 0) * (b.get("Vo") or 0) for b in before[-20:]]
    if vols:
        out["turnover_20"] = statistics.fmean(vols)
    return out


def build_events(buyback: list[dict[str, Any]], sameday: dict[tuple[str, str], set[str]],
                 master: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """td_buyback_decisions を共通スキーマ event へ展開 (価格未付与)。"""
    out: list[dict[str, Any]] = []
    for r in buyback:
        code5 = r["Code"]
        code4 = _code4(code5)
        ed = r.get("DiscDate")
        if not ed:
            continue
        m = master.get(code5) or {}
        out.append({
            "code": code4,
            "event_date": ed,
            "event_type": "buyback_decision",
            "source": "td_buyback_decisions",
            "title": r.get("Title"),
            "attrs": {
                "disc_time": r.get("DiscTime"),
                "disc_bucket": disc_bucket(r.get("DiscTime", "")),
                "combo": combo_label(sameday.get((code4, ed), set())),
                "scale_band": m.get("scale_band"),
                "scale_cat": m.get("ScaleCat"),
                "mrgn": m.get("MrgnNm"),
                "s17": m.get("S17Nm"),
                "s33": m.get("S33Nm"),
            },
        })
    return out


def enrich(events: list[dict[str, Any]], *, out_path: Path = OUT_PATH,
           checkpoint_every: int = 50) -> None:
    """各 event に日足を取得しリターン+軸を付与、TOPIX-α を加えて保存。"""
    for i, rec in enumerate(events, 1):
        a = rec["attrs"]
        code, ed = rec["code"], rec["event_date"]
        code5 = code + "0" if len(code) == 4 else code
        ev = date.fromisoformat(ed)
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=code5,
                                     **{"from": (ev - timedelta(days=40)).isoformat(),
                                        "to": (ev + timedelta(days=30)).isoformat()})
        except _jquants.JQuantsError as e:
            a["price_error"] = str(e)
            bars = []
        if bars:
            a.update(returns_from_bars(bars, ed, DAYS, skip_bars=0))
            a.update(axis_fields_from_bars(bars, ed))
        if i % checkpoint_every == 0:
            atomic_write_json(out_path, {"records": events, "count": len(events),
                                         "partial": True}, indent=0)
            print(f"  ...{i}/{len(events)}")
    topix_adjust.enrich_with_alpha(events, DAYS)
    atomic_write_json(out_path, {"records": events, "count": len(events)}, indent=0)
    print(f"wrote {out_path} ({len(events)}件)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--buyback", type=Path, default=BUYBACK_PATH)
    ap.add_argument("--tdnet", type=Path, default=TDNET_PATH)
    ap.add_argument("--master", type=Path, default=MASTER_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    buyback = json.loads(args.buyback.read_text())["records"]
    sameday = build_sameday_tags(json.loads(args.tdnet.read_text())["records"])
    master = {m["Code"]: m for m in json.loads(args.master.read_text())["records"]}
    events = build_events(buyback, sameday, master)
    print(f"[buyback_standalone] {len(events)}件 enrich開始")
    enrich(events, out_path=args.out)


if __name__ == "__main__":
    main()

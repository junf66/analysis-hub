"""#4 株式分割発表→翌寄ロング の細分化検証用に、各 split イベントへ軸ラベルを付与する。

軸 (このプランで作成可能なもの):
  A 信用区分   : margin-interest IssType (1=信用 / 2=貸借 / 3=その他) を発表日以前の直近週で結合
  D 分割比率   : 発表後 ~90営業日以内の AdjFactor≠1 の足から ratio=1/AdjFactor を推定
  E 単独/複合 : 同日・同銘柄の TDnet 開示タグ (good_jisha=自社株買い 等) で分類
  F REIT       : 証券コード帯による近似 (8951-8999 等)。正確な銘柄種別は listed/info 契約外
  G 流動性     : 発表前20営業日の平均売買代金 (Close×Vo)
  H 株価帯     : entry_open (翌寄り価格)
  J 寄り方     : entry_open / 前日終値 - 1 の gap%
出口リターン: 翌寄り(entry)→+1/+3/+5/+10日引け (調整後)。後段で TOPIX-α (alpha_d{N}_ret) を付与。

B 時価総額・C 業種・I PER/PBR は listed/info・fins/statements が 403 (契約外) のため作成不可。
出力: data/edge_candidates/split_multiday_enriched.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json
from scripts.edge_candidates import topix_adjust
from scripts.edge_candidates.enrich_common import returns_from_bars

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TDNET_PATH = REPO_ROOT / "data" / "edge_candidates" / "tdnet_index.json"
MARGIN_PATH = REPO_ROOT / "data" / "edge_candidates" / "margin_interest.json"
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "split_multiday_enriched.json"
DAYS = [1, 3, 5, 10]

_ISSTYPE = {"1": "信用", "2": "貸借", "3": "その他"}
# E 複合の優先順位 (上から順に最初に該当したラベルを採用)
_COMBO_PRIORITY = [
    ("good_jisha", "自社株買い同時"), ("good_zouhai", "増配同時"),
    ("good_kessan_up", "上方修正同時"), ("good_div_rev", "配当予想修正同時"),
    ("good_teikei", "提携同時"), ("good_juchu", "受注同時"),
]


def select_split_events(tdnet: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """good_split を含むユニーク (code, event_date) を返す。"""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for r in tdnet:
        if "good_split" not in (r.get("tags") or []):
            continue
        key = (r.get("code"), r.get("event_date"))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        out.append({"code": key[0], "event_date": key[1], "event_type": "stock_split",
                    "source": "tdnet_index", "title": r.get("title"), "attrs": {}})
    return out


def build_sameday_tags(tdnet: list[dict[str, Any]]) -> dict[tuple[str, str], set[str]]:
    """(code, event_date) → その日の全開示タグ集合。"""
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in tdnet:
        key = (r.get("code"), r.get("event_date"))
        if key[0] and key[1]:
            out[key].update(r.get("tags") or [])
    return out


def combo_label(tags: set[str]) -> str:
    """同日タグ集合から E 軸ラベルを決める。"""
    bad = any(t.startswith("bad_") for t in tags)
    goods = [lbl for tag, lbl in _COMBO_PRIORITY if tag in tags]
    if bad and not goods:
        return "悪材料同時"
    if not goods:
        return "単独"
    if len(goods) == 1:
        return goods[0] + ("+悪材料" if bad else "")
    return "複合(その他)"


def build_isstype_index(margin: list[dict[str, Any]]) -> dict[str, list[tuple[str, str]]]:
    """code → [(date, IssType)] (date 昇順)。発表日以前の直近区分の検索用。"""
    idx: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in margin:
        c, d, it = r.get("Code"), r.get("Date"), r.get("IssType")
        if c and d and it is not None:
            idx[c].append((d, str(it)))
    for c in idx:
        idx[c].sort()
    return idx


def isstype_as_of(idx: dict[str, list[tuple[str, str]]], code: str, event_date: str) -> str | None:
    """event_date 以前の直近 IssType ラベルを返す。"""
    series = idx.get(code)
    if not series:
        return None
    pos = bisect_right([d for d, _ in series], event_date) - 1
    if pos < 0:
        return None
    return _ISSTYPE.get(series[pos][1], series[pos][1])


def is_reit_code(code: str) -> bool:
    """証券コード帯による J-REIT 近似 (8951-8999 / 3226-3309 / 2971-2989 等)。"""
    try:
        n = int(code[:4])
    except (ValueError, TypeError):
        return False
    return 8951 <= n <= 8999 or 3226 <= n <= 3309 or 2971 <= n <= 2989


def axis_fields_from_bars(bars: list[dict[str, Any]], event_date: str) -> dict[str, Any]:
    """日足から gap/turnover/entry_price/split_ratio を計算 (純関数)。"""
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
    for b in after:                      # D: 権利落ち日の AdjFactor から比率推定
        af = b.get("AdjFactor")
        if af and abs(af - 1.0) > 1e-9:
            out["split_factor"] = af
            out["split_ratio"] = round(1.0 / af, 3) if af > 0 else None
            out["ex_date"] = b["Date"]
            break
    return out


def enrich(events: list[dict[str, Any]], sameday: dict[tuple[str, str], set[str]],
           isstype_idx: dict[str, list[tuple[str, str]]], *, out_path: Path = OUT_PATH,
           checkpoint_every: int = 100) -> None:
    """各 event に日足を取得して軸ラベル+リターンを付与し保存する。"""
    for i, rec in enumerate(events, 1):
        a = rec["attrs"]
        code, ed = rec["code"], rec["event_date"]
        code5 = code + "0" if len(code) == 4 else code
        ev = date.fromisoformat(ed)
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=code5,
                                     **{"from": (ev - timedelta(days=40)).isoformat(),
                                        "to": (ev + timedelta(days=130)).isoformat()})
        except _jquants.JQuantsError as e:
            a["price_error"] = str(e)
            bars = []
        if bars:
            a.update(returns_from_bars(bars, ed, DAYS, skip_bars=0))
            a.update(axis_fields_from_bars(bars, ed))
        a["isstype"] = isstype_as_of(isstype_idx, code, ed)
        a["combo"] = combo_label(sameday.get((code, ed), set()))
        a["is_reit"] = is_reit_code(code)
        if i % checkpoint_every == 0:
            atomic_write_json(out_path, {"records": events, "count": len(events),
                                         "partial": True}, indent=0)
            print(f"  ...{i}/{len(events)}")
    topix_adjust.enrich_with_alpha(events, DAYS)
    atomic_write_json(out_path, {"records": events, "count": len(events)}, indent=0)
    print(f"wrote {out_path} ({len(events)}件)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tdnet", type=Path, default=TDNET_PATH)
    ap.add_argument("--margin", type=Path, default=MARGIN_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    tdnet = json.loads(args.tdnet.read_text())["records"]
    events = select_split_events(tdnet)
    sameday = build_sameday_tags(tdnet)
    isstype_idx = build_isstype_index(json.loads(args.margin.read_text())["records"])
    print(f"[split_axes] {len(events)}件 enrich開始")
    enrich(events, sameday, isstype_idx, out_path=args.out)


if __name__ == "__main__":
    main()

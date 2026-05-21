"""(code, event_date) ごとに 好材料 と 悪材料 が両方ある日を抽出する。

入力:
  cache/disclosures/share_buyback_tdnet.json  (Pro: 自社株買い、全件好材料)
  cache/disclosures/fins_summary.json         (or fins_summary_by_code.json)

ロジック:
  1. 自社株買い   → good (subpattern_hint=jisha)
  2. /fins/summary →
       - EarnForecastRevision: 同 FY の直前公表予想 (F* or NxF*) と新予想を比較
         * 売上 / 営業利益 / 経常利益 / 純利益 のいずれかが -3% 未満なら bad/kahou
         * +3% 超なら good/kouhou
       - DividendForecastRevision: 同 FY の直前公表予想 (FDiv* or NxFDiv*) と比較
         * 減額なら bad/genhai, 増額なら good/zouhai
       - FinancialStatements (決算短信): 前年同期 NP との比較
         * NP YoY -10% 未満なら bad/genshu
  3. (code, event_date) で集約し、good 1件以上 + bad 1件以上が同居する日を kouaku_record として出力

subpattern 確定:
  - good に jisha が含まれ bad に kahou があれば → jisha_kahou
  - good に jisha が含まれ bad に genshu が含まれれば → jisha_genshu
  - good に fukuhai + bad に genshu → fukuhai_genshu
  - good に zouhai + bad に genshu → zouhai_genshu
  - good に tokubai + bad に kahou → tokubai_kahou
  - 上記いずれにも該当しなければ → other

出力: data/kouaku_records.json (共通スキーマ準拠 + subpattern / good_factors / bad_factors)
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.classify_kouaku import (
    ClassifiedDisclosure,
    _code4,
    classify_buyback_record,
    classify_fins_record,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "cache" / "disclosures"
OUT_PATH = REPO_ROOT / "data" / "kouaku_records.json"

REVISION_BAD_THRESHOLD_PCT = -3.0
REVISION_GOOD_THRESHOLD_PCT = 3.0
NP_YOY_BAD_THRESHOLD_PCT = -10.0


# ---- ヘルパ ---------------------------------------------------------------

def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct_delta(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


# ---- /fins/summary 時系列ロジック ---------------------------------------

def _build_history_by_code(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """code → 開示日昇順の /fins/summary 履歴。"""
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_code[_code4(r.get("Code"))].append(r)
    for c in by_code:
        by_code[c].sort(key=lambda r: (r.get("DiscDate") or "", r.get("DiscTime") or ""))
    return by_code


def _classify_earn_revision(
    row: dict[str, Any], prior: list[dict[str, Any]]
) -> tuple[str, str | None, str, dict[str, float]]:
    """EarnForecastRevision を直近の同 FY 予想と比較し方向判定。"""
    new = {k: _f(row.get(f"F{k}")) for k in ("Sales", "OP", "OdP", "NP")}
    cur_fy = (row.get("CurFYSt"), row.get("CurFYEn"))
    old: dict[str, float | None] = {k: None for k in new}
    for prev in reversed(prior):
        if prev is row:
            continue
        if (prev.get("CurFYSt"), prev.get("CurFYEn")) == cur_fy:
            for k in new:
                if old[k] is None:
                    old[k] = _f(prev.get(f"F{k}"))
        if (prev.get("NxtFYSt"), prev.get("NxtFYEn")) == cur_fy:
            for k in new:
                if old[k] is None:
                    old[k] = _f(prev.get(f"NxF{k}"))
        if all(v is not None for v in old.values()):
            break

    deltas: dict[str, float] = {}
    for k in ("NP", "OP", "OdP", "Sales"):
        d = _pct_delta(new[k], old[k])
        if d is not None:
            deltas[k] = d
    if not deltas:
        return ("neutral", None, "EarnForecastRevision (prior不明)", {})
    primary = deltas.get("NP", next(iter(deltas.values())))
    metric = {f"{k}_revision_pct": v for k, v in deltas.items()}
    if primary <= REVISION_BAD_THRESHOLD_PCT:
        return ("bad", "kahou", f"EarnForecastRevision NP{primary:+.1f}%", metric)
    if primary >= REVISION_GOOD_THRESHOLD_PCT:
        return ("good", "kouhou", f"EarnForecastRevision NP{primary:+.1f}%", metric)
    return ("neutral", None, f"EarnForecastRevision NP{primary:+.1f}% (微修正)", metric)


def _classify_dividend_revision(
    row: dict[str, Any], prior: list[dict[str, Any]]
) -> tuple[str, str | None, str, dict[str, float]]:
    """DividendForecastRevision を直近の同 FY 配当予想と比較。"""
    new_div = _f(row.get("FDivAnn"))
    cur_fy = (row.get("CurFYSt"), row.get("CurFYEn"))
    old_div: float | None = None
    for prev in reversed(prior):
        if prev is row:
            continue
        if (prev.get("CurFYSt"), prev.get("CurFYEn")) == cur_fy:
            if old_div is None:
                old_div = _f(prev.get("FDivAnn")) or _f(prev.get("DivAnn"))
        if (prev.get("NxtFYSt"), prev.get("NxtFYEn")) == cur_fy:
            if old_div is None:
                old_div = _f(prev.get("NxFDivAnn"))
        if old_div is not None:
            break
    delta = _pct_delta(new_div, old_div)
    if delta is None:
        return ("neutral", None, "DividendForecastRevision (prior不明)", {})
    metric = {"Div_revision_pct": delta}
    if old_div == 0 and new_div and new_div > 0:
        return ("good", "fukuhai", "DividendForecastRevision (復配)", metric)
    if new_div == 0 and old_div and old_div > 0:
        return ("bad", "muhai", "DividendForecastRevision (無配)", metric)
    if delta >= REVISION_GOOD_THRESHOLD_PCT:
        return ("good", "zouhai", f"DividendForecastRevision Div{delta:+.1f}%", metric)
    if delta <= REVISION_BAD_THRESHOLD_PCT:
        return ("bad", "genhai", f"DividendForecastRevision Div{delta:+.1f}%", metric)
    return ("neutral", None, f"DividendForecastRevision Div{delta:+.1f}%", metric)


def _classify_financial_statements(
    row: dict[str, Any], prior: list[dict[str, Any]]
) -> list[tuple[str, str | None, str, dict[str, float]]]:
    """決算短信を前年同期 NP と配当 (DivAnn) で比較し、**複数の judgment** を返す。

    決算短信は NP 結果と同時に DivAnn (年間配当実績/予定) も内包するため、
    片方を見るだけだと「減益+増配」のような同一 row 内の好悪同居を取りこぼす。
    NP 判定と Div 判定をそれぞれ独立に評価し、検出ヒット全てをリストで返す。
    """
    cur_per_type = row.get("CurPerType")
    cur_per_st = row.get("CurPerSt") or ""
    cur_year = cur_per_st[:4] if cur_per_st else ""
    doctype = row.get("DocType") or ""
    # DocType 由来の追加情報を reason に埋め込む (REIT / IFRS / NonConsolidated 等の識別用)
    inst_tag = ""
    if "REIT" in doctype:
        inst_tag = " [REIT]"
    elif "IFRS" in doctype:
        inst_tag = " [IFRS]"
    elif "NonConsolidated" in doctype:
        inst_tag = " [NonConsol]"
    elif "Foreign" in doctype:
        inst_tag = " [Foreign]"
    judgments: list[tuple[str, str | None, str, dict[str, float]]] = []

    # 前年同期決算短信を探す
    prev_row = None
    for prev in reversed(prior):
        if prev is row:
            continue
        if prev.get("CurPerType") != cur_per_type:
            continue
        prev_st = prev.get("CurPerSt") or ""
        if not prev_st or not cur_year:
            continue
        if prev_st[:4] == str(int(cur_year) - 1):
            prev_row = prev
            break

    # --- NP YoY 判定 ---
    new_np = _f(row.get("NP"))
    old_np = _f(prev_row.get("NP")) if prev_row else None
    np_delta = _pct_delta(new_np, old_np)
    if np_delta is None:
        judgments.append(("neutral", "kessan", f"{cur_per_type}決算短信{inst_tag} (前年比不明)", {}))
    else:
        np_metric = {"NP_YoY_pct": np_delta}
        if np_delta <= NP_YOY_BAD_THRESHOLD_PCT:
            judgments.append(("bad", "genshu", f"{cur_per_type}決算短信{inst_tag} NP YoY{np_delta:+.1f}%", np_metric))
        elif np_delta >= -NP_YOY_BAD_THRESHOLD_PCT:
            judgments.append(("good", "kouhou", f"{cur_per_type}決算短信{inst_tag} NP YoY{np_delta:+.1f}%", np_metric))
        else:
            judgments.append(("neutral", "kessan", f"{cur_per_type}決算短信{inst_tag} NP YoY{np_delta:+.1f}%", np_metric))

    # --- Div YoY 判定 (DivAnn = 通期配当合計) ---
    new_div = _f(row.get("DivAnn"))
    old_div = _f(prev_row.get("DivAnn")) if prev_row else None
    if new_div is not None and old_div is not None:
        if old_div == 0 and new_div > 0:
            judgments.append(("good", "fukuhai", f"{cur_per_type}決算短信{inst_tag} (復配 0→{new_div})", {"DivAnn_YoY_pct": float("inf")}))
        elif new_div == 0 and old_div > 0:
            judgments.append(("bad", "muhai", f"{cur_per_type}決算短信{inst_tag} (無配 {old_div}→0)", {"DivAnn_YoY_pct": -100.0}))
        elif old_div > 0:
            div_delta = (new_div - old_div) / old_div * 100.0
            div_metric = {"DivAnn_YoY_pct": div_delta}
            if div_delta >= REVISION_GOOD_THRESHOLD_PCT:
                judgments.append(("good", "zouhai", f"{cur_per_type}決算短信{inst_tag} DivAnn YoY{div_delta:+.1f}%", div_metric))
            elif div_delta <= REVISION_BAD_THRESHOLD_PCT:
                judgments.append(("bad", "genhai", f"{cur_per_type}決算短信{inst_tag} DivAnn YoY{div_delta:+.1f}%", div_metric))

    # --- 来期予想 NxFNp vs 今期 NP 判定 (FYFinancialStatements のみ NxF* を持つ) ---
    nx_np = _f(row.get("NxFNp"))
    if nx_np is not None and new_np is not None and new_np != 0:
        nx_delta = (nx_np - new_np) / abs(new_np) * 100.0
        nx_metric = {"NxFNp_vs_NP_pct": nx_delta}
        if nx_delta <= NP_YOY_BAD_THRESHOLD_PCT:
            judgments.append(("bad", "kahou_nx", f"{cur_per_type}決算短信{inst_tag} 来期予想 NxFNp vs NP {nx_delta:+.1f}%", nx_metric))
        elif nx_delta >= -NP_YOY_BAD_THRESHOLD_PCT:
            judgments.append(("good", "kouhou_nx", f"{cur_per_type}決算短信{inst_tag} 来期予想 NxFNp vs NP {nx_delta:+.1f}%", nx_metric))

    return judgments


def _classify_revision_vs_prior(
    row: dict[str, Any],
    prior: list[dict[str, Any]],
) -> list[tuple[str, str | None, str, dict[str, float]]]:
    """業績/配当予想/決算短信を「前回公表 or 前年同期」と比較し方向判定。

    1 row から複数の好悪 judgment が出る (e.g. 決算短信は NP YoY と Div YoY の両方)。
    """
    doctype = row.get("DocType") or ""
    if "EarnForecastRevision" in doctype:
        return [_classify_earn_revision(row, prior)]
    if "DividendForecastRevision" in doctype:
        return [_classify_dividend_revision(row, prior)]
    if "FinancialStatements" in doctype:
        return _classify_financial_statements(row, prior)
    return []


# ---- 抽出本体 -------------------------------------------------------------

def classify_all(
    buyback_rows: list[dict[str, Any]],
    fins_rows: list[dict[str, Any]],
) -> list[ClassifiedDisclosure]:
    """自社株買い + /fins/summary を一括分類して polarity 付き ClassifiedDisclosure 列を返す。"""
    out: list[ClassifiedDisclosure] = []
    # 自社株買い (常に good/jisha)
    for r in buyback_rows:
        cd = classify_buyback_record(r)
        out.append(cd)

    # /fins/summary: code 履歴で時系列判定 (1 row が複数 judgment を返しうる)
    by_code = _build_history_by_code(fins_rows)
    for code, hist in by_code.items():
        for idx, row in enumerate(hist):
            prior = hist[:idx]
            for polarity, hint, reason, metric in _classify_revision_vs_prior(row, prior):
                if polarity == "neutral" and not hint:
                    continue
                cd = classify_fins_record(row)
                cd.polarity = polarity
                cd.subpattern_hint = hint
                cd.reason = reason
                cd.metric = metric
                out.append(cd)
    return out


# ---- サブパターン確定 ---------------------------------------------------

_POSITIVE_HINT_ORDER = [
    "jisha", "tob", "kouhou", "kouhou_nx", "zouhai", "fukuhai",
    "tokubai", "yutai_new", "kabushiki_bunkatsu",
]
_NEGATIVE_HINT_ORDER = [
    "kahou", "kahou_nx", "genshu", "genhai", "muhai", "seikyu", "yutai_end",
]


def decide_subpattern(good_hints: set[str], bad_hints: set[str]) -> str:
    """好/悪 hint の集合から `{pos}_{neg}` 形式の subpattern 名を決定する。

    優先度は `_POSITIVE_HINT_ORDER` / `_NEGATIVE_HINT_ORDER` の宣言順。
    既知 hint の組合せは全て命名され、未知 hint 同士なら `other`。
    """
    pos = next((h for h in _POSITIVE_HINT_ORDER if h in good_hints), None)
    neg = next((h for h in _NEGATIVE_HINT_ORDER if h in bad_hints), None)
    if pos and neg:
        return f"{pos}_{neg}"
    return "other"


# ---- 集約 -----------------------------------------------------------------

def aggregate_mixed(classified: list[ClassifiedDisclosure]) -> list[dict[str, Any]]:
    """(code, event_date) で集約し、好+悪が同居する日を kouaku_record に整形。"""
    by_key: dict[tuple[str, str], list[ClassifiedDisclosure]] = defaultdict(list)
    for cd in classified:
        if not cd.code or not cd.event_date:
            continue
        by_key[(cd.code, cd.event_date)].append(cd)

    records: list[dict[str, Any]] = []
    for (code, ev_date), items in sorted(by_key.items()):
        goods = [c for c in items if c.polarity == "good"]
        bads = [c for c in items if c.polarity == "bad"]
        if not goods or not bads:
            continue
        good_hints = {c.subpattern_hint for c in goods if c.subpattern_hint}
        bad_hints = {c.subpattern_hint for c in bads if c.subpattern_hint}
        subpattern = decide_subpattern(good_hints, bad_hints)
        rec = {
            "id": f"kouaku:{code}:{ev_date}",
            "code": code,
            "event_date": ev_date,
            "event_type": "kouaku_mixed",
            "source": "tdnet+fins",
            "ref_id": f"{code}_{ev_date}",
            "subpattern": subpattern,
            "good_factors": [_factor_dict(c) for c in goods],
            "bad_factors": [_factor_dict(c) for c in bads],
            "attrs": {},
        }
        records.append(rec)
    return records


def _factor_dict(cd: ClassifiedDisclosure) -> dict[str, Any]:
    return {
        "title": cd.title,
        "subpattern_hint": cd.subpattern_hint,
        "reason": cd.reason,
        "disc_no": cd.disc_no,
        "disc_time": cd.disc_time,
        "metric": cd.metric or {},
    }


# ---- I/O ------------------------------------------------------------------

def _load_buyback() -> list[dict[str, Any]]:
    p = CACHE_DIR / "share_buyback_tdnet.json"
    if not p.exists():
        print(f"  (warn) {p} not found - buyback empty")
        return []
    data = json.loads(p.read_text())
    rows: list[dict[str, Any]] = []
    for d, items in data.get("by_date", {}).items():
        for item in items:
            if "DiscDate" not in item:
                item["DiscDate"] = d
            rows.append(item)
    return rows


def _load_fins() -> list[dict[str, Any]]:
    p_by_date = CACHE_DIR / "fins_summary.json"
    p_by_code = CACHE_DIR / "fins_summary_by_code.json"
    rows: list[dict[str, Any]] = []
    if p_by_date.exists():
        data = json.loads(p_by_date.read_text())
        for items in data.get("by_date", {}).values():
            rows.extend(items)
    if p_by_code.exists():
        data = json.loads(p_by_code.read_text())
        for items in data.get("by_code", {}).values():
            rows.extend(items)
    return rows


def _merge_existing_attrs(new_records: list[dict[str, Any]], out_path: Path) -> int:
    """既存 kouaku_records.json があれば attrs (価格 enrich 等) を保持する。

    id で突合。新規レコードの attrs は空のままにし、既存 id のものは attrs を
    そのまま継承する。戻り値: 引き継いだ件数。
    """
    if not out_path.exists():
        return 0
    try:
        old = json.loads(out_path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    old_attrs = {r["id"]: r.get("attrs") or {} for r in old.get("records", [])}
    carried = 0
    for r in new_records:
        if r["id"] in old_attrs and old_attrs[r["id"]]:
            r["attrs"] = old_attrs[r["id"]]
            carried += 1
    return carried


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT_PATH, help="出力先 kouaku_records.json のパス")
    ap.add_argument("--reset-attrs", action="store_true", help="既存 attrs (価格 enrich) を引き継がず空で出力")
    args = ap.parse_args()

    buyback = _load_buyback()
    fins = _load_fins()
    print(f"loaded {len(buyback)} buyback rows, {len(fins)} fins/summary rows")

    classified = classify_all(buyback, fins)
    pol_count: dict[str, int] = defaultdict(int)
    for c in classified:
        pol_count[c.polarity] += 1
    print(f"classified: {dict(pol_count)}")

    records = aggregate_mixed(classified)
    sub_count: dict[str, int] = defaultdict(int)
    for r in records:
        sub_count[r["subpattern"]] += 1
    print(f"mixed records: {len(records)}  subpatterns: {dict(sub_count)}")

    if not args.reset_attrs:
        carried = _merge_existing_attrs(records, args.out)
        if carried:
            print(f"carried over enrich attrs from {carried} records")

    from scripts._atomic import atomic_write_json
    atomic_write_json(args.out, {
        "schema_version": 1,
        "event_type": "kouaku_mixed",
        "subpattern_counts": dict(sorted(sub_count.items())),  # alphabetical for stable diff
        "records": records,
    })
    print(f"saved → {args.out}")


if __name__ == "__main__":
    main()

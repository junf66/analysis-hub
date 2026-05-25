"""データ健全性チェック (探索開始前に走らせる)。

出力: stdout に色なし plain text サマリ + reports/data_health.md

確認内容:
  - kouaku_records.json: 件数、subpattern 分布、価格 enrich coverage、分足 coverage、limit-lock、price_error
  - cache/disclosures/fins_summary.json: 行数、日付範囲、欠損日
  - cache/disclosures/share_buyback_tdnet.json: 存在有無 (Pro 必要)
  - cache/noon_experiment/daily_bars_by_code.json: ユニーク銘柄数、サイズ

非ゼロ exit: critical な欠損を検出した場合 (--strict 時のみ)。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDS_PATH = REPO_ROOT / "data" / "kouaku_records.json"
PO_RECORDS_PATH = REPO_ROOT / "data" / "po_records.json"
PO_RAW_PATH = REPO_ROOT / "cache" / "po" / "po_records.json"
HOLDINGS_RECORDS_PATH = REPO_ROOT / "data" / "holdings_records.json"
HOLDINGS_RAW_PATH = REPO_ROOT / "cache" / "holdings" / "holdings.json"
FINS_PATH = REPO_ROOT / "cache" / "disclosures" / "fins_summary.json"
BUYBACK_PATH = REPO_ROOT / "cache" / "disclosures" / "share_buyback_tdnet.json"
TDNET_PATH = REPO_ROOT / "cache" / "disclosures" / "tdnet_all.json"
BARS_PATH = REPO_ROOT / "cache" / "noon_experiment" / "daily_bars_by_code.json"
HEALTH_MD = REPO_ROOT / "reports" / "data_health.md"


STALE_DAYS = 10  # last_updated がこれより古ければ STALE 警告


def _size_mb(p: Path) -> float:
    return p.stat().st_size / 1024 / 1024 if p.exists() else 0.0


def _freshness(lines: list[str], last_updated: str | None, *, label: str = "最終更新") -> int:
    """last_updated (ISO) の鮮度を 1 行追記し、STALE_DAYS 超なら 1 を返す (警告カウント用)。"""
    if not last_updated:
        lines.append(f"- {label}: 不明 (タイムスタンプなし)")
        return 0
    try:
        d = date.fromisoformat(str(last_updated)[:10])
    except ValueError:
        lines.append(f"- {label}: 解析不可 ({last_updated})")
        return 0
    age = (date.today() - d).days
    flag = "  ← STALE (要再取得)" if age > STALE_DAYS else ""
    lines.append(f"- {label}: {d.isoformat()} ({age} 日前){flag}")
    return 1 if age > STALE_DAYS else 0


def check_records(lines: list[str]) -> dict[str, int]:
    """data/kouaku_records.json の件数・coverage・subpattern 分布を lines に追記。"""
    if not RECORDS_PATH.exists():
        lines.append("- ❌ `data/kouaku_records.json` **missing** — `python -m scripts.extract_mixed_disclosures` を実行")
        return {"critical": 1}
    data = json.loads(RECORDS_PATH.read_text())
    recs = data.get("records", [])
    sub = Counter(r["subpattern"] for r in recs)
    enriched = sum(1 for r in recs if (r.get("attrs") or {}).get("next_open") is not None)
    minuted = sum(1 for r in recs if (r.get("attrs") or {}).get("next_open_900") is not None)
    locked = sum(1 for r in recs if (r.get("attrs") or {}).get("limit_locked"))
    errored = sum(1 for r in recs if (r.get("attrs") or {}).get("price_error"))
    dates = sorted({r["event_date"] for r in recs})
    lines.append("## kouaku_records.json")
    lines.append("")
    lines.append(f"- 件数: **{len(recs)}**  ({_size_mb(RECORDS_PATH):.2f} MB)")
    lines.append(f"- event_date 範囲: {dates[0]} 〜 {dates[-1]}" if dates else "- (空)")
    stale = _freshness(lines, data.get("last_updated"))
    lines.append(f"- 価格 enrich coverage: **{enriched}/{len(recs)}** ({enriched*100//max(len(recs),1)}%)")
    lines.append(f"- 分足 coverage: {minuted}/{len(recs)} ({minuted*100//max(len(recs),1)}%)  ※ J-Quants 分足は 2024-05-21 以降のみ")
    lines.append(f"- limit-lock (S 高/S 安全日ロック): {locked}")
    lines.append(f"- price_error (上場廃止等): {errored}")
    lines.append("")
    lines.append("### subpattern 分布")
    lines.append("")
    lines.append("| subpattern | n |")
    lines.append("|---|---|")
    for k in sorted(sub):
        lines.append(f"| {k} | {sub[k]} |")
    lines.append("")
    critical = 1 if enriched < len(recs) * 0.8 else 0  # 80% 未満を critical 扱い
    return {"critical": critical, "total": len(recs), "enriched": enriched, "minuted": minuted, "stale": stale}


def check_fins(lines: list[str]) -> dict[str, int]:
    """cache/disclosures/fins_summary.json のサイズ・日付範囲・重複・決算期変更を lines に追記。"""
    lines.append("## cache/disclosures/fins_summary.json")
    lines.append("")
    if not FINS_PATH.exists():
        lines.append("- ❌ **missing** — `python -m scripts.fetch_disclosures` を実行")
        return {"critical": 1}
    size = _size_mb(FINS_PATH)
    data = json.loads(FINS_PATH.read_text())
    by_date = data.get("by_date", {})
    dates = sorted(by_date)
    fins_rows: list[dict] = []
    for items in by_date.values():
        fins_rows.extend(items)
    total_rows = len(fins_rows)
    lines.append(f"- ファイルサイズ: {size:.1f} MB")
    lines.append(f"- 日付範囲: {dates[0]} 〜 {dates[-1]}  ({len(dates)} 営業日)")
    lines.append(f"- 行数合計: **{total_rows:,}**")
    # 0 行の日 (= rate-limit failure 跡)
    zero_days = [d for d in dates if not by_date[d]]
    if zero_days:
        lines.append(f"- ⚠ 0 行の営業日: {len(zero_days)} 件 ({zero_days[:5]}...)")
    # 最終日が今日からどれくらい前か
    last = date.fromisoformat(dates[-1])
    age = (date.today() - last).days
    lines.append(f"- 最終日 → 今日: {age} 日前")
    if age > 7:
        lines.append("- ⚠ 1 週間以上更新なし。`python -m scripts.fetch_disclosures` を検討")

    # データ品質サブセクション
    from collections import Counter as _C, defaultdict as _D
    lines.append("")
    lines.append("### データ品質")
    lines.append("")
    # 1. 重複 (Code, DiscDate, DocType)
    keys = _C((r.get("Code"), r.get("DiscDate"), r.get("DocType")) for r in fins_rows)
    dups = sum(1 for k, n in keys.items() if n > 1)
    lines.append(f"- 重複 (Code, DiscDate, DocType): {dups} 種類  (重複自体は同日複数Q公表等で実害なし)")
    # 2. DiscNo 重複
    dno = _C(r.get("DiscNo") for r in fins_rows if r.get("DiscNo"))
    dno_dups = sum(1 for n in dno.values() if n > 1)
    lines.append(f"- 重複 DiscNo: {dno_dups}  (0 が望ましい)")
    # 3. Code 長さ分布
    len_dist = _C(len(str(r.get("Code", ""))) for r in fins_rows)
    lines.append(f"- Code 長さ分布: {dict(len_dist)}  (5 桁末尾0 が標準)")
    # 4. 決算期変更銘柄
    by_code: dict[str, list[dict]] = _D(list)
    for r in fins_rows:
        if "FinancialStatements" in (r.get("DocType") or ""):
            by_code[r.get("Code")].append(r)
    fy_changed = 0
    for code, items in by_code.items():
        months = {(r.get("CurFYSt") or "")[5:7] for r in items if r.get("CurFYSt")}
        if len(months) > 1:
            fy_changed += 1
    lines.append(f"- 決算期変更銘柄 (CurFYSt 開始月が複数): {fy_changed}  ※prior 探索で neutral 化する可能性")
    # 5. 同日複数 disclosure (kouaku mixed 候補) と実際の比率
    multi_pairs: dict[tuple, set] = _D(set)
    for r in fins_rows:
        dt = r.get("DocType") or ""
        if "FinancialStatements" in dt or "EarnForecastRevision" in dt or "DividendForecastRevision" in dt:
            multi_pairs[(str(r.get("Code") or "")[:4], r.get("DiscDate"))].add(dt)
    n_multi = sum(1 for v in multi_pairs.values() if len(v) >= 2)
    lines.append(f"- 同日複数 disclosure (Code, DiscDate): {n_multi} 件  (kouaku 候補母集団)")
    lines.append("")
    return {"critical": 0, "rows": total_rows, "days": len(dates), "age": age, "dups": dups, "fy_changed": fy_changed}


def check_buyback(lines: list[str]) -> dict[str, int]:
    """share_buyback_tdnet キャッシュの有無を lines に追記。"""
    lines.append("## cache/disclosures/share_buyback_tdnet.json (Pro 専用)")
    lines.append("")
    if BUYBACK_PATH.exists():
        data = json.loads(BUYBACK_PATH.read_text())
        n = sum(len(v) for v in data.get("by_date", {}).values())
        lines.append(f"- ✅ 存在: {n:,} 行")
    else:
        lines.append("- (なし) — J-Quants Light 契約のため未取得。Pro 契約 + `api.jquants-pro.com` allowlist 追加で取得可")
    lines.append("")
    return {"critical": 0}


def check_tdnet(lines: list[str]) -> dict[str, int]:
    """cache/disclosures/tdnet_all.json (yanoshin) の有無・件数を lines に追記。"""
    lines.append("## cache/disclosures/tdnet_all.json (yanoshin TDnet 全タイトル)")
    lines.append("")
    if not TDNET_PATH.exists():
        lines.append("- (なし) — `python -m scripts.fetch_disclosures` (--skip-tdnet なしで実行) で生成")
        lines.append("")
        return {"critical": 0}
    size = _size_mb(TDNET_PATH)
    data = json.loads(TDNET_PATH.read_text())
    by_date = data.get("by_date", {})
    dates = sorted(by_date)
    total = sum(len(v) for v in by_date.values())
    lines.append(f"- ファイルサイズ: {size:.1f} MB")
    if dates:
        lines.append(f"- 日付範囲: {dates[0]} 〜 {dates[-1]}  ({len(dates)} 営業日)")
    lines.append(f"- 行数合計: **{total:,}**")
    lines.append("")
    return {"critical": 0, "rows": total, "days": len(dates)}


def check_po(lines: list[str]) -> dict[str, int]:
    """data/po_records.json (共通スキーマ展開済) + cache/po/po_records.json 生キャッシュを確認。"""
    lines.append("## data/po_records.json (PO 共通スキーマ)")
    lines.append("")
    if not PO_RECORDS_PATH.exists():
        lines.append("- ❌ **missing** — `python -m fetchers.po` → `python -m scripts.extract_po` を実行")
        lines.append("")
        return {"critical": 1}
    data = json.loads(PO_RECORDS_PATH.read_text())
    recs = data.get("records", [])
    by_stage = Counter(r.get("stage") for r in recs)
    by_type = Counter(r.get("po_type") for r in recs)
    legacy = sum(1 for r in recs if r.get("legacy_record"))
    concurrent = sum(1 for r in recs if r.get("concurrent_earnings"))
    split = sum(1 for r in recs if r.get("split_within_po_window"))
    # 価格 enrich coverage (各ステージで attrs に next_open or ref_open があるか)
    with_price = sum(
        1 for r in recs
        if (r.get("attrs") or {}).get("next_open") is not None
        or (r.get("attrs") or {}).get("ref_open") is not None
    )
    dates = sorted({r["event_date"] for r in recs if r.get("event_date")})
    lines.append(f"- 件数: **{len(recs)}**  ({_size_mb(PO_RECORDS_PATH):.2f} MB)")
    if dates:
        lines.append(f"- event_date 範囲: {dates[0]} 〜 {dates[-1]}")
    lines.append(f"- stage 分布: {dict(by_stage)}")
    lines.append(f"- po_type 分布: {dict(by_type)}")
    lines.append(f"- 価格 enrich coverage: {with_price}/{len(recs)} ({with_price*100//max(len(recs),1)}%)")
    lines.append(f"- EV 評価除外フラグ: legacy={legacy}, concurrent_earnings={concurrent}, split_within_po_window={split}")
    lines.append(f"- 原本 PO 件数 (raw): {data.get('count_raw', '?')}  "
                 f"(取り込み除外 {data.get('count_dropped', 0)}: {data.get('dropped_reasons', {})})")
    stale = _freshness(lines, data.get("last_updated") or data.get("raw_last_updated"))
    lines.append("")
    return {"critical": 0, "total": len(recs), "with_price": with_price, "stale": stale}


def check_holdings(lines: list[str]) -> dict[str, int]:
    """data/holdings_records.json (共通スキーマ展開済) を確認。"""
    lines.append("## data/holdings_records.json (大量保有 共通スキーマ)")
    lines.append("")
    if not HOLDINGS_RECORDS_PATH.exists():
        lines.append("- ❌ **missing** — `python -m fetchers.holdings` → `python -m scripts.extract_holdings` を実行")
        lines.append("")
        return {"critical": 1}
    data = json.loads(HOLDINGS_RECORDS_PATH.read_text())
    recs = data.get("records", [])
    suspect = sum(1 for r in recs if r.get("low_ratio_suspect"))
    with_price = sum(
        1 for r in recs if (r.get("attrs") or {}).get("next_day_open_to_close_ret") is not None
    )
    dates = sorted({r["event_date"] for r in recs if r.get("event_date")})
    lines.append(f"- 件数: **{len(recs)}**  ({_size_mb(HOLDINGS_RECORDS_PATH):.2f} MB)")
    if dates:
        lines.append(f"- event_date 範囲: {dates[0]} 〜 {dates[-1]}")
    lines.append(f"- purpose 分布: {data.get('purpose_counts', {})}")
    lines.append(f"- holder 分布: {data.get('holder_counts', {})}")
    lines.append(f"- 価格 coverage (寄り→引け): {with_price}/{len(recs)} ({with_price*100//max(len(recs),1)}%)")
    lines.append(f"- EV 評価除外フラグ: low_ratio_suspect={suspect}")
    lines.append(f"- 原本件数 (raw): {data.get('count_raw', '?')}  "
                 f"(取り込み除外 {data.get('count_dropped', 0)}: {data.get('dropped_reasons', {})})")
    stale = _freshness(lines, data.get("last_updated") or data.get("raw_last_updated"))
    lines.append("")
    return {"critical": 0, "total": len(recs), "with_price": with_price, "stale": stale}


def check_bars(lines: list[str]) -> dict[str, int]:
    """noon_experiment daily_bars キャッシュのサイズ・銘柄数を lines に追記。"""
    lines.append("## cache/noon_experiment/daily_bars_by_code.json (全銘柄 5y 日足)")
    lines.append("")
    if not BARS_PATH.exists():
        lines.append("- (なし) — query/分析高速化用キャッシュ。`python -m scripts.noon_disclosure_experiment` で生成")
        return {"critical": 0}
    size = _size_mb(BARS_PATH)
    data = json.loads(BARS_PATH.read_text())
    codes = list(data.keys())
    nonempty = sum(1 for c in codes if data[c])
    lines.append(f"- ファイルサイズ: {size:.0f} MB")
    lines.append(f"- 銘柄数: {len(codes):,}  (うち bars あり: {nonempty:,})")
    lines.append("")
    return {"critical": 0, "codes": len(codes)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true", help="critical があれば非ゼロ exit")
    ap.add_argument("--out", type=Path, default=HEALTH_MD, help="出力 md ファイル (data_health.md)")
    args = ap.parse_args()

    lines: list[str] = []
    lines.append("# データ健全性チェック")
    lines.append("")

    summary = {}
    summary["records"] = check_records(lines)
    summary["po"] = check_po(lines)
    summary["holdings"] = check_holdings(lines)
    summary["fins"] = check_fins(lines)
    summary["tdnet"] = check_tdnet(lines)
    summary["buyback"] = check_buyback(lines)
    summary["bars"] = check_bars(lines)

    critical = sum(s.get("critical", 0) for s in summary.values())
    stale = sum(s.get("stale", 0) for s in summary.values())
    lines.append("---")
    lines.append("")
    lines.append(f"**critical issues: {critical}**  /  STALE sources (>{STALE_DAYS}日): {stale}")

    md = "\n".join(lines)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md)

    # stdout にも要約
    print(md)
    print(f"\nwrote {args.out}")
    if args.strict and critical:
        sys.exit(critical)


if __name__ == "__main__":
    main()

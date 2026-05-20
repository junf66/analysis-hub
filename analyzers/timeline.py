"""銘柄コード横断タイムライン。

PO (po-tracker) + 大量保有報告書 (holdings-tracker) の全イベントを共通スキーマで集約し、
(code, event_date) でソートして時系列表示する。

CLI 例:
  python -m analyzers.timeline 7203
  python -m analyzers.timeline 7203 8035 9984
  python -m analyzers.timeline --since 2025-01-01 7203
  python -m analyzers.timeline --window 7203 2025-04-15  # 指定日±30 日窓
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from fetchers import holdings as holdings_fetcher
from fetchers import kouaku as kouaku_fetcher
from fetchers import po as po_fetcher
from normalizers import holdings as holdings_normalizer
from normalizers import kouaku as kouaku_normalizer
from normalizers import po as po_normalizer


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def load_all_events(refresh: bool = False) -> list[dict[str, Any]]:
    """各ソースを fetch (or キャッシュ) → 正規化 → 結合した events を返す。

    kouaku_mixed はローカル成果物 (data/kouaku_records.json) のみ。リモート fetch は
    別途 scripts/fetch_disclosures.py + extract_mixed_disclosures.py で更新する。
    """
    if refresh:
        po_fetcher.fetch()
        holdings_fetcher.fetch()

    po_payload = po_fetcher.load_cached()
    holdings_payload = holdings_fetcher.load_cached()

    events: list[dict[str, Any]] = []
    events.extend(po_normalizer.normalize(po_payload["records"]))
    events.extend(holdings_normalizer.normalize(holdings_payload["records"]))
    try:
        kouaku_payload = kouaku_fetcher.load_cached()
        events.extend(kouaku_normalizer.normalize(kouaku_payload.get("records", [])))
    except FileNotFoundError:
        pass  # kouaku 未実行でも他ソースだけで継続
    return events


# ---- フィルタ ---------------------------------------------------------

def filter_events(
    events: Iterable[dict[str, Any]],
    codes: set[str] | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in events:
        if codes is not None and e["code"] not in codes:
            continue
        d = _parse_date(e["event_date"])
        if d is None:
            continue
        if since and d < since:
            continue
        if until and d > until:
            continue
        out.append(e)
    return out


def window_events(
    events: Iterable[dict[str, Any]],
    code: str,
    anchor: date,
    days: int = 30,
) -> list[dict[str, Any]]:
    """指定銘柄について anchor 日 ± days のイベントだけ抽出。"""
    return filter_events(
        events, codes={code}, since=anchor - timedelta(days=days), until=anchor + timedelta(days=days)
    )


# ---- 表示 ------------------------------------------------------------

def _summary_line(event: dict[str, Any]) -> str:
    """イベント 1 件の短い説明。ソース別に重要そうな属性を抜粋。"""
    attrs = event.get("attrs", {})
    et = event["event_type"]

    if et.startswith("po_"):
        name = attrs.get("name", "")
        po_type = attrs.get("type", "")
        scale = attrs.get("po_scale")
        dilu = attrs.get("dilution")
        bits = [f"{name} ({po_type})"]
        if scale:
            bits.append(f"規模 {scale}億")
        if dilu is not None:
            bits.append(f"希薄化 {dilu}%")
        if et == "po_announce":
            return "PO 発表 " + " / ".join(bits)
        if et == "po_decide":
            ip = attrs.get("issue_price")
            return f"PO 価格決定{f' 発行価格 {ip}円' if ip else ''} " + " / ".join(bits)
        if et == "po_deliver":
            return "PO 受渡 " + " / ".join(bits)

    if et == "kouaku_mixed":
        sub = attrs.get("subpattern", "?")
        goods = attrs.get("good_factors", [])
        bads = attrs.get("bad_factors", [])
        good_hints = "/".join(sorted({g.get("subpattern_hint") for g in goods if g.get("subpattern_hint")}))
        bad_hints = "/".join(sorted({b.get("subpattern_hint") for b in bads if b.get("subpattern_hint")}))
        gap = attrs.get("gap_pct")
        oc = attrs.get("next_day_open_to_close_ret")
        price = ""
        if gap is not None:
            price = f"  GAP={gap:+.2f}%"
        if oc is not None:
            price += f" 寄→引={oc:+.2f}%"
        if attrs.get("limit_locked"):
            price += " [LIMIT]"
        return f"好悪材料 [{sub}] 好:{good_hints} / 悪:{bad_hints}{price}"

    if et.startswith("holdings_"):
        filer = attrs.get("filer_name", "")
        ratio = attrs.get("holding_ratio")
        prev = attrs.get("previous_ratio")
        change = attrs.get("ratio_change")
        purpose = attrs.get("purpose_category_jp", "")
        kind = {
            "holdings_filing": "大量保有報告",
            "holdings_change": "変更報告",
            "holdings_correction": "訂正報告",
            "holdings_filing_correction": "訂正報告",
            "holdings_change_correction": "訂正変更報告",
        }.get(et, et)
        ratio_str = f"{ratio}%" if ratio is not None else ""
        if change is not None and prev is not None:
            ratio_str = f"{prev}% → {ratio}% ({change:+}%)"
        return f"{kind} {filer} {ratio_str}{f' [{purpose}]' if purpose else ''}"

    return et


def render_timeline(events: list[dict[str, Any]]) -> str:
    """events を (code, event_date) 昇順で 1 行ずつ整形する。"""
    sorted_events = sorted(events, key=lambda e: (e["code"], e["event_date"], e["event_type"]))
    lines: list[str] = []
    current_code: str | None = None
    for e in sorted_events:
        if e["code"] != current_code:
            attrs = e.get("attrs", {})
            name = attrs.get("name") or attrs.get("issuer_name") or attrs.get("company_name_jp") or ""
            lines.append("")
            lines.append(f"### [{e['code']}] {name}".rstrip())
            current_code = e["code"]
        lines.append(
            f"  {e['event_date']}  {e['source']:<11} {e['event_type']:<28} {_summary_line(e)}"
        )
    return "\n".join(lines).lstrip("\n")


# ---- 概要ヘルパ -------------------------------------------------------

def cross_source_codes(events: Iterable[dict[str, Any]]) -> list[tuple[str, dict[str, int]]]:
    """両ソースに登場する銘柄コード一覧。(code, {source: count}) を per-source 件数降順で返す。"""
    by_code: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in events:
        by_code[e["code"]][e["source"]] += 1
    cross = [
        (code, dict(counts))
        for code, counts in by_code.items()
        if len(counts) >= 2  # 2 ソース以上に出現
    ]
    cross.sort(key=lambda x: -sum(x[1].values()))
    return cross


# ---- CLI -------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("codes", nargs="*", help="4桁銘柄コード (複数可)。省略時は両ソース共通の銘柄を一覧表示")
    parser.add_argument("--since", help="ISO date (YYYY-MM-DD) 以降だけ表示")
    parser.add_argument("--until", help="ISO date (YYYY-MM-DD) 以前だけ表示")
    parser.add_argument(
        "--window",
        nargs=2,
        metavar=("CODE", "ANCHOR"),
        help="特定銘柄について anchor 日 ±N 日 (--days で指定、既定 30) のイベント表示",
    )
    parser.add_argument("--days", type=int, default=30, help="--window の窓幅 (片側)")
    parser.add_argument("--refresh", action="store_true", help="キャッシュを使わず最新を fetch")
    args = parser.parse_args()

    events = load_all_events(refresh=args.refresh)

    # --- 概要表示 ---
    sources = defaultdict(int)
    for e in events:
        sources[e["source"]] += 1
    print(f"loaded {len(events)} events  sources={dict(sources)}")

    if args.window:
        code, anchor_str = args.window
        anchor = _parse_date(anchor_str)
        if anchor is None:
            parser.error(f"invalid --window anchor: {anchor_str}")
        scoped = window_events(events, code, anchor, days=args.days)
        print(f"\n# {code} の {anchor} ±{args.days} 日窓 ({len(scoped)} events)\n")
        print(render_timeline(scoped))
        return

    if args.codes:
        scoped = filter_events(
            events,
            codes=set(args.codes),
            since=_parse_date(args.since),
            until=_parse_date(args.until),
        )
        print(f"\n# 指定銘柄タイムライン ({len(scoped)} events)\n")
        print(render_timeline(scoped))
        return

    # 引数なし: 両ソース共通の銘柄を一覧表示
    cross = cross_source_codes(events)
    print(f"\n# 両ソースに出現する銘柄: {len(cross)} 件 (上位 30)\n")
    print(f"  {'code':<6} {'total':>5}  per-source")
    for code, counts in cross[:30]:
        total = sum(counts.values())
        per = " ".join(f"{s}:{n}" for s, n in sorted(counts.items()))
        print(f"  {code:<6} {total:>5}  {per}")
    print(
        f"\n試しに上位 1 銘柄のタイムラインを表示するには:\n"
        f"  python -m analyzers.timeline {cross[0][0] if cross else 'CODE'}"
    )


if __name__ == "__main__":
    main()

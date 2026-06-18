"""適時開示タイトルのマイニング (新イベント種の発掘・Stage1=件数把握)。

cache/disclosures/tdnet_all.json (yanoshin全件・Title全文・2021-) を開示タイトルの正規表現で
イベント種に分類し、種ごとの件数・ユニーク銘柄数・直近3年頻度・サンプルを出す。
目的: まだEV検証していないイベント種(TOB/MBO/株式併合/立会外分売/業務提携/第三者割当/優待…)の
うち**十分なnがある種**を特定し、Stage2(価格取得→イベントスタディ)の投資先を決める。

価格は取らない(Stage1)。出力: reports/disclosure_title_mining.md。
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_text

REPO = Path(__file__).resolve().parent.parent.parent
TDNET_ALL = REPO / "cache" / "disclosures" / "tdnet_all.json"
REPORT = REPO / "reports" / "disclosure_title_mining.md"

# (ラベル, 正規表現)。優先順=具体的→一般的(先勝ち)。未検証の新イベント種を上に置く。
_RULES: list[tuple[str, str]] = [
    ("TOB公開買付", r"公開買付"),
    ("MBO・非公開化", r"ＭＢＯ|MBO|マネジメント・?バイアウト|非公開化"),
    ("株式併合", r"株式併合"),
    ("立会外分売", r"立会外分売"),
    ("子会社化・買収", r"子会社化|完全子会社化|株式の取得.*子会社"),
    ("大口受注", r"大口受注|大型受注|大規模受注|受注を獲得|受注獲得"),
    ("資本業務提携", r"資本業務提携|業務資本提携|資本提携"),
    ("業務提携", r"業務提携|協業|共同開発"),
    ("第三者割当増資", r"第三者割当"),
    ("株主優待", r"株主優待"),
    ("自己株式取得", r"自己株式の?取得|自社株買"),
    ("自己株式消却", r"自己株式の?消却"),
    ("株式分割", r"株式分割"),
    ("公募・売出", r"公募|売出|募集"),
    ("業績予想修正", r"業績予想.*修正|通期.*予想.*修正"),
    ("配当予想修正", r"配当予想.*修正|配当.*修正"),
    ("特別損益", r"特別損失|特別利益|減損"),
    ("MSCB・新株予約権", r"新株予約権|ＣＢ|転換社債"),
    ("上場廃止・整理", r"上場廃止|監理|整理銘柄"),
    ("訂正・延期", r"訂正|延期|中止"),
]


def classify(title: str) -> str | None:
    """タイトルを先勝ちでイベント種に分類(該当なしは None)。"""
    for label, pat in _RULES:
        if re.search(pat, title):
            return label
    return None


def load_records() -> list[dict[str, Any]]:
    """tdnet_all.json を平坦化 (by_date → records)。"""
    d = json.loads(TDNET_ALL.read_text())
    out: list[dict[str, Any]] = []
    for recs in d["by_date"].values():
        out.extend(recs)
    return out


def build_report(recs: list[dict[str, Any]]) -> str:
    """イベント種ごとの件数・銘柄数・直近3年頻度・サンプルを Markdown に。"""
    dates = sorted(r.get("pubdate", "")[:10] for r in recs if r.get("pubdate"))
    span = f"{dates[0]} 〜 {dates[-1]}" if dates else "?"
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        lab = classify(r.get("title", ""))
        if lab:
            buckets[lab].append(r)
    L = ["# 適時開示タイトル・マイニング (Stage1=件数把握)", "",
         f"母体 {len(recs):,}件 / 期間 {span} / cache/disclosures/tdnet_all.json。", "",
         "新イベント種の発掘。**n(=独立銘柄日)が十分**な種を Stage2(価格→イベントスタディ)へ回す。", "",
         "| イベント種 | 件数 | ユニーク銘柄 | 直近3年/年 | サンプルtitle |",
         "|---|--:|--:|--:|---|"]
    rows = []
    for lab, _ in _RULES:
        b = buckets.get(lab, [])
        if not b:
            continue
        codes = {r.get("code") for r in b}
        yrs = defaultdict(int)
        for r in b:
            yrs[r.get("pubdate", "")[:4]] += 1
        freq3 = sum(yrs.get(y, 0) for y in ("2024", "2025", "2026")) / 3
        sample = next((r["title"] for r in b if r.get("title")), "")[:34]
        rows.append((len(b), lab, len(codes), freq3, sample))
    for n, lab, ncode, freq3, sample in sorted(rows, reverse=True):
        L.append(f"| {lab} | {n:,} | {ncode:,} | {freq3:.0f} | {sample}… |")
    matched = sum(len(b) for b in buckets.values())
    L += ["", f"分類済 {matched:,} / 未分類 {len(recs) - matched:,}。", "",
          "## Stage2候補の見立て",
          "- **n十分かつ未EV検証**: TOB・株式併合・立会外分売・資本業務提携・第三者割当・優待 が有望。",
          "- 既検証: 株式分割(#4)・業績/配当修正(④⑤等)・自己株取得(エッジなし既出)・公募売出(②⑥⑦)。",
          "- 次段: 各種の (code,event_date) に翌営業日 寄→引(1日完結) を付け、方向別コスト+クラスタt+"
          "FDR+OOS+勝率>50%フィルタ で評価。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPORT, help="出力 md (既定 reports/disclosure_title_mining.md)")
    args = ap.parse_args()
    recs = load_records()
    report = build_report(recs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, report)
    print(report)
    print(f"[title_mining] {len(recs):,}件 → {args.out}")


if __name__ == "__main__":
    main()

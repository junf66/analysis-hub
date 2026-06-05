"""適時開示 Title 文言ベースの材料ランドスケープ走査 (死角の新サブパターン発掘)。

既存の kouaku 分類は DiscItems コード＋好悪ルールでタグ化するため、タグ語彙に無い
材料タイプ(=未活用 Title テキスト)が丸ごと死角になる。本スクリプトは /td/bulk
(5年・759k件) の Title を材料カテゴリの正規表現バケットへ割り、各バケットの
件数・既存タグ被覆率・代表 Title・共起 DiscItems を集計して、現行サブパターンが
取りこぼしている大口カテゴリ(=新サブパターン候補)を炙り出す。

被覆判定: tdnet_index.json(既に分類済みの好悪同日材料サブセット)に載っている
(code,event_date) を「既存被覆」とみなし、バケット内の未被覆率を出す。未被覆率が
高く件数の多いバケットほど、新サブパターンとして検証する価値が高い。

出力: reports/title_keyword_scan.md
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from scripts.edge_candidates.extract_mild_good import iter_td_bulk_rows

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TDNET_INDEX = REPO_ROOT / "data" / "edge_candidates" / "tdnet_index.json"
REPORT_PATH = REPO_ROOT / "reports" / "title_keyword_scan.md"

# 材料カテゴリ → Title 正規表現。順序は問わない(複数該当は各々カウント)。
# 既存タグ語彙(good_jisha/bad_tokuson/good_div_rev/good_zouhai/bad_genpai/good_teikei/
# good_juchu/good_split/bad_daisansha/bad_genson/kessan_up/down)と対照し、
# 語彙に無い材料(業績予想修正の方向別・売出し・TOB・自己株消却・不祥事系等)を厚めに置く。
KEYWORD_BUCKETS: dict[str, str] = {
    "業績予想_下方": r"業績予想.*(下方|減額)|(下方|減額).*業績予想",
    "業績予想_上方": r"業績予想.*(上方|増額)|(上方|増額).*業績予想",
    "業績予想_修正_方向不明": r"業績予想.*修正",
    "配当予想_修正": r"配当予想.*修正|復配|無配|記念配当|特別配当",
    "自己株式_取得": r"自己株式.*取得|自社株買",
    "自己株式_消却": r"自己株式.*消却",
    "株式分割": r"株式分割",
    "株式併合": r"株式併合",
    "増資_公募": r"公募.*増資|募集株式|新株式発行",
    "第三者割当": r"第三者割当",
    "売出し_分売": r"株式の売出し|立会外分売|オーバーアロットメント",
    "TOB_公開買付": r"公開買付",
    "MBO_非公開化": r"MBO|非公開化|上場廃止",
    "自社株TOB": r"自己株式.*公開買付|自己株.*買付",
    "特別損失_減損": r"特別損失|減損損失|減損の計上",
    "特別利益": r"特別利益|売却益.*計上",
    "業務資本提携": r"業務提携|資本提携|資本業務提携",
    "受注_大型契約": r"受注|大型受注|契約締結",
    "不祥事_行政処分": r"不適切|不正|行政処分|課徴金|改善報告|特別調査委員会|第三者委員会",
    "訴訟": r"訴訟|提訴|損害賠償請求",
    "経営統合_合併": r"経営統合|合併|株式交換|株式移転",
    "債務_再生": r"民事再生|破産|会社更生|債務超過|債権放棄|私的整理",
    "増配_減配_文言": r"増配|減配",
}


def _norm_code(code: str) -> str:
    """5桁(末尾0)銘柄コードを4桁に正規化 (/td/bulk と tdnet_index の表記差を吸収)。"""
    code = str(code)
    return code[:-1] if len(code) == 5 and code.endswith("0") else code


def _covered_keys(index_path: Path) -> set[tuple[str, str]]:
    """tdnet_index.json から既存分類済みの (code, event_date) 集合を作る。"""
    if not index_path.exists():
        return set()
    recs = json.loads(index_path.read_text()).get("records", [])
    return {(_norm_code(r.get("code")), str(r.get("event_date"))) for r in recs if r.get("tags")}


def scan(rows: Iterable[dict[str, Any]], covered: set[tuple[str, str]],
         sample_n: int = 4) -> list[dict[str, Any]]:
    """各バケットの件数・未被覆率・代表 Title・共起 DiscItems を集計して返す。"""
    pats = {name: re.compile(rx) for name, rx in KEYWORD_BUCKETS.items()}
    n_total = Counter()
    n_uncovered = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    items: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        title = r.get("Title") or r.get("title") or ""
        if not title:
            continue
        code = _norm_code(r.get("Code") or r.get("code") or "")
        date = str(r.get("DiscDate") or r.get("event_date") or "")
        is_covered = (code, date) in covered
        disc = r.get("DiscItems")
        disc_codes = disc.split("|") if isinstance(disc, str) else (disc or [])
        for name, pat in pats.items():
            if pat.search(title):
                n_total[name] += 1
                if not is_covered:
                    n_uncovered[name] += 1
                if len(samples[name]) < sample_n:
                    samples[name].append(title[:50])
                for dc in disc_codes:
                    if dc:
                        items[name][dc] += 1
    out = []
    for name in KEYWORD_BUCKETS:
        tot = n_total[name]
        if not tot:
            continue
        out.append({
            "bucket": name,
            "n": tot,
            "uncovered": n_uncovered[name],
            "uncovered_pct": round(n_uncovered[name] / tot * 100, 1),
            "top_items": items[name].most_common(3),
            "samples": samples[name],
        })
    out.sort(key=lambda d: d["uncovered"], reverse=True)
    return out


def render(rows: list[dict[str, Any]]) -> str:
    """scan() の結果を未被覆件数降順の Markdown 表にする。"""
    lines = ["# 適時開示 Title 文言ベース 材料ランドスケープ走査", "",
             "現行 kouaku 分類(DiscItems+好悪ルール)が取りこぼす材料カテゴリの炙り出し。",
             "未被覆 = tdnet_index(分類済みサブセット)に (code,event_date) が無い件数。",
             "**未被覆件数が多いバケット = 新サブパターン候補**。", "",
             "| 材料バケット | 件数 | 未被覆 | 未被覆% | 主な DiscItems | 代表Title |",
             "|---|---:|---:|---:|---|---|"]
    for d in rows:
        items = " ".join(f"{c}({n})" for c, n in d["top_items"])
        samp = d["samples"][0] if d["samples"] else ""
        lines.append(f"| {d['bucket']} | {d['n']} | {d['uncovered']} | {d['uncovered_pct']} | {items} | {samp} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPORT_PATH))
    args = ap.parse_args()
    covered = _covered_keys(TDNET_INDEX)
    print(f"[scan] 既存被覆 (code,date) = {len(covered)} 件。/td/bulk 走査開始...")
    rows = scan(iter_td_bulk_rows(), covered)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(rows), encoding="utf-8")
    print(f"[scan] {len(rows)} バケット → {out_path}")
    for d in rows[:8]:
        print(f"  {d['bucket']:24s} n={d['n']:6d} 未被覆={d['uncovered']:6d} ({d['uncovered_pct']}%)")


if __name__ == "__main__":
    main()

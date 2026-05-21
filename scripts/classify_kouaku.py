"""適時開示を「好材料 / 悪材料 / 中立」に分類する。

入力は 2 系統:
  1. TDnet 開示 (自社株買い等) : data/kouaku_classification.csv のキーワード正規表現
  2. /fins/summary             : DocType + 数値 (YoY 比較・予想差分) で判定

判定の優先順は:
  - 既知 DocType を先に処理 (EarnForecastRevision / DividendForecastRevision / 決算短信YoY)
  - 残りはキーワード辞書を当てる

返り値: ClassifiedDisclosure
  - polarity: "good" | "bad" | "neutral"
  - subpattern_hint: "jisha" | "fukuhai" | "zouhai" | "tokubai" | "genshu" | "kahou" | "muhai" | "kouhou" | "seikyu" | None
  - reason: 人が読めるラベル (どのルールで分類したか)
  - metric: 数値が伴う場合の辞書 (e.g. {"NP_YoY_pct": -23.4})
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
DICT_PATH = REPO_ROOT / "data" / "kouaku_classification.csv"


@dataclass
class Rule:
    polarity: str
    subpattern_hint: str
    pattern: re.Pattern[str]
    note: str


@dataclass
class ClassifiedDisclosure:
    polarity: str  # "good" / "bad" / "neutral"
    subpattern_hint: str | None
    reason: str
    code: str
    event_date: str
    title: str
    raw: dict[str, Any] = field(default_factory=dict)
    metric: dict[str, float] = field(default_factory=dict)
    disc_no: str | None = None
    disc_time: str | None = None


def load_rules(path: Path = DICT_PATH) -> list[Rule]:
    """data/kouaku_classification.csv からタイトル分類ルール一覧をロード。"""
    rules: list[Rule] = []
    with path.open() as f:
        for row in csv.DictReader(f):
            rules.append(
                Rule(
                    polarity=row["polarity"].strip(),
                    subpattern_hint=row["subpattern_hint"].strip(),
                    pattern=re.compile(row["pattern"].strip()),
                    note=row.get("note", "").strip(),
                )
            )
    return rules


# ---- /fins/summary 専用ロジック ------------------------------------------

def _classify_fins_doctype(row: dict[str, Any]) -> tuple[str | None, str | None, str, dict[str, float]]:
    """DocType を見て (polarity, subpattern_hint, reason, metric) を返す。

    本当の減益/業績修正判定は後段 (extract) で過去値と比較しないと不可能。
    ここでは DocType だけで分かる neutral タグを返すに留める。
    """
    doctype = (row.get("DocType") or "")
    if "EarnForecastRevision" in doctype:
        return ("neutral", "kouhou_or_kahou", f"DocType={doctype}", {})
    if "DividendForecastRevision" in doctype:
        return ("neutral", "zouhai_or_genhai", f"DocType={doctype}", {})
    if "FinancialStatements" in doctype:
        # 決算短信そのものは中立。減益かどうかは extract 側で前年同期 NP と比較する。
        return ("neutral", "kessan", f"DocType={doctype}", {})
    return (None, None, "", {})


# ---- 公開 API -------------------------------------------------------------

def _code4(code: Any) -> str:
    """J-Quants 5桁コード (末尾0付与) を 4 桁に正規化。4桁ならゼロパディング。"""
    s = str(code or "")
    if not s:
        return ""
    if len(s) == 5 and s.isdigit():
        return s[:4]
    return s.zfill(4) if s.isdigit() else s


def classify_buyback_record(row: dict[str, Any]) -> ClassifiedDisclosure:
    """Pro share_buyback_tdnet レコードを 1 件 → 好材料 (jisha)。"""
    code = _code4(row.get("Code"))
    title = row.get("Title") or row.get("DocumentTitle") or "自己株式取得"
    return ClassifiedDisclosure(
        polarity="good",
        subpattern_hint="jisha",
        reason="share_buyback_tdnet",
        code=code,
        event_date=row.get("DiscDate") or row.get("Date") or "",
        title=title,
        disc_no=row.get("DiscNo") or row.get("DocumentID"),
        disc_time=row.get("DiscTime"),
        raw=row,
    )


def classify_fins_record(
    row: dict[str, Any],
    *,
    rules: list[Rule] | None = None,
) -> ClassifiedDisclosure:
    """/fins/summary レコードを 1 件分類。後段で履歴と突合する想定なので neutral 主体。"""
    code = _code4(row.get("Code"))
    polarity, hint, reason, metric = _classify_fins_doctype(row)
    return ClassifiedDisclosure(
        polarity=polarity or "neutral",
        subpattern_hint=hint,
        reason=reason or "fins_summary",
        code=code,
        event_date=row.get("DiscDate") or "",
        title=row.get("DocType") or "",
        disc_no=row.get("DiscNo"),
        disc_time=row.get("DiscTime"),
        metric=metric,
        raw=row,
    )


def classify_by_title(title: str, *, rules: list[Rule]) -> tuple[str, str | None, str]:
    """タイトル文字列だけから (polarity, subpattern_hint, matched_rule_note) を返す。"""
    for rule in rules:
        if rule.pattern.search(title):
            return rule.polarity, rule.subpattern_hint, rule.note
    return "neutral", None, ""


# ---- バッチ ---------------------------------------------------------------

def classify_buyback(rows: Iterable[dict[str, Any]]) -> list[ClassifiedDisclosure]:
    """share_buyback_tdnet 行配列を ClassifiedDisclosure 配列に変換。"""
    return [classify_buyback_record(r) for r in rows]


def classify_fins(rows: Iterable[dict[str, Any]]) -> list[ClassifiedDisclosure]:
    """/fins/summary 行配列を ClassifiedDisclosure 配列に変換 (履歴比較は extract 側)。"""
    return [classify_fins_record(r) for r in rows]


if __name__ == "__main__":
    rules = load_rules()
    print(f"loaded {len(rules)} keyword rules from {DICT_PATH}")
    samples = [
        "自己株式の取得に関するお知らせ",
        "2026年3月期 通期業績予想の下方修正に関するお知らせ",
        "配当予想の修正(増配)に関するお知らせ",
        "復配のお知らせ",
        "公募増資による新株式発行に関するお知らせ",
        "特別配当のお知らせ",
    ]
    for s in samples:
        pol, hint, note = classify_by_title(s, rules=rules)
        print(f"  {pol:7s} {hint or '-':10s} {note:20s} :: {s}")

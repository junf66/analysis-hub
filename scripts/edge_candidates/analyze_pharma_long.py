"""確定エッジ⑧候補『好悪×医薬品×信用 翌寄→引 LONG』の再現・分解スクリプト。

好悪材料(kouaku)は全体が翌寄→引けの下方ドリフト(短ドリフト)を持つが、医薬品の信用銘柄
だけは逆にロングで効く。日次クロスセクション demean(その日の好悪平均を控除=基線超過α)で評価。
信用/貸借・規模・好材料系で分解し、信用が効きの分かれ目であることを示す。

validate_edges「新エッジ」事前登録でも raw net+1.18%/FDR✓/OOS+0.85% を確認済み。
出力: reports/pharma_long.md
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
KOUAKU_PATH = REPO_ROOT / "data" / "kouaku_records.json"
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
REPORT_PATH = REPO_ROOT / "reports" / "pharma_long.md"

LONG_COST = 0.20
MIN_N = 5


def _to5(code: str) -> str:
    return code + "0" if len(code) == 4 else code


def load_kouaku() -> list[dict[str, Any]]:
    """kouaku records を返す。"""
    return json.loads(KOUAKU_PATH.read_text()).get("records", [])


def load_master() -> dict[str, dict[str, Any]]:
    """code5 → equities_master レコード。"""
    return {m["Code"]: m for m in json.loads(MASTER_PATH.read_text()).get("records", [])}


def eligible_ret(r: dict[str, Any]) -> float | None:
    """limit_locked でなく翌寄→翌引けがある場合その ret(%)。"""
    a = r.get("attrs") or {}
    if a.get("limit_locked"):
        return None
    v = a.get("next_day_open_to_close_ret")
    return float(v) if v is not None else None


def day_means(records: list[dict[str, Any]]) -> dict[str, float]:
    """日付ごとの好悪ユニバース平均リターン(クロスセクション demean 用)。"""
    by_date: dict[str, list[float]] = defaultdict(list)
    for r in records:
        v = eligible_ret(r)
        if v is not None:
            by_date[r.get("event_date") or ""].append(v)
    return {d: statistics.fmean(v) for d, v in by_date.items() if v}


def collect(records: list[dict[str, Any]], master: dict[str, dict[str, Any]],
            dmeans: dict[str, float], filt: Callable[[dict[str, Any], dict[str, Any]], bool]
            ) -> list[float]:
    """医薬品ベース×filt の基線超過α(long net 控除前の残差)を集める。"""
    out: list[float] = []
    for r in records:
        v = eligible_ret(r)
        if v is None:
            continue
        m = master.get(_to5(r.get("code", ""))) or {}
        if m.get("S17Nm") != "医薬品":
            continue
        if not filt(r, m):
            continue
        out.append(v - dmeans.get(r.get("event_date") or "", 0.0))
    return out


def stat(rows: list[float]) -> dict[str, float]:
    """n/long net α/t/勝率(long net = α - LONG_COST)。"""
    n = len(rows)
    if n < MIN_N:
        return {"n": n, "ev": 0.0, "t": 0.0, "win": 0.0}
    ev = statistics.fmean(rows)
    sd = statistics.stdev(rows) if n > 1 else 0.0
    t = ev / (sd / (n ** 0.5)) if sd > 0 else 0.0
    win = sum(1 for x in rows if x > 0) / n * 100
    return {"n": n, "ev": ev - LONG_COST, "t": t, "win": win}


_FILTERS: list[tuple[str, Callable[[dict[str, Any], dict[str, Any]], bool]]] = [
    ("医薬品 全体", lambda r, m: True),
    ("医薬品 × 信用", lambda r, m: m.get("MrgnNm") == "信用"),
    ("医薬品 × 貸借", lambda r, m: m.get("MrgnNm") == "貸借"),
    ("医薬品 × 小型", lambda r, m: m.get("scale_band") == "小型"),
    ("医薬品 × 好材料系(kouhou)", lambda r, m: str(r.get("subpattern", "")).startswith("kouhou")),
]


def build_report(records: list[dict[str, Any]], master: dict[str, dict[str, Any]]) -> str:
    """医薬品ロング分解レポートを生成。"""
    dmeans = day_means(records)
    L: list[str] = []
    L.append("# 確定エッジ⑧候補『好悪×医薬品×信用 翌寄→引 LONG』分解 (2026-06-03)")
    L.append("")
    L.append("好悪材料は全体が翌寄→引けの下方ドリフト(短ドリフト)を持つが、医薬品の信用銘柄だけ逆にロングで効く。")
    L.append("日次クロスセクション demean(その日の好悪平均を控除=基線超過α)で評価。long往復0.20% net。")
    L.append("")
    L.append("| 条件 | n | α net(long) | t | 勝率 |")
    L.append("|---|---|---|---|---|")
    for lab, f in _FILTERS:
        s = stat(collect(records, master, dmeans, f))
        L.append(f"| {lab} | {s['n']} | {s['ev']:+.2f}% | {s['t']:+.2f} | {s['win']:.0f}% |")
    L.append("")
    L.append("## 所見")
    L.append("")
    L.append("- **信用が効きの分かれ目**: 医薬品×信用 ≫ 医薬品×貸借(ほぼ無効)。")
    L.append("  信用専のみ(=貸借不可)の医薬品は小型・新興バイオ寄りで、開示に過剰反応→翌日ロングで戻りを取る。")
    L.append("- 全体ショート優位のkouakuの中で**唯一の逆張りLONG**。validate_edges 事前登録でも")
    L.append("  raw net+1.18%/t_clust+2.56/FDR✓/OOS+0.85% を確認済み(基線超過αでは+1.44%)。")
    return "\n".join(L)


if __name__ == "__main__":
    records = load_kouaku()
    master = load_master()
    REPORT_PATH.write_text(build_report(records, master))
    print(f"wrote {REPORT_PATH}")

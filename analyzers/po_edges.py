"""po-tracker セッションで検証済の 3 つの PO エッジ戦略をハブ側に移植したもの。

参照 EV (po-tracker セッション時点):
  1. 発表翌日エッジ  : 普通株、翌日寄りロング → 9:10売り       EV +0.66%
  2. 受渡日エッジ    : 普通株、受渡日 GD で寄りロング → 引け売り EV +0.80%
  3. リートエッジ    : REIT、翌日寄りショート → 決定日引け買戻  EV +1.12%

データ定義 (po-tracker docs/FIELDS.md):
  - GD (ギャップダウン) : delivery_gap_pct <= -0.5 (%)
  - 9:10 売りリターン   : next_day_910_ret (J-Quants 1 分足から算出済、2 年以内のみ)

リピーター罠 (3 年連続同月の同銘柄 PO) は filter_repeaters() で除外可能。
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence


@dataclass
class EdgeStat:
    name: str
    n: int
    mean_pct: float
    median_pct: float
    stdev_pct: float
    win_rate: float
    se_pct: float        # standard error of the mean (%)
    t_stat: float        # mean / SE
    note: str = ""

    def format(self) -> str:
        sign = "+" if self.mean_pct >= 0 else ""
        return (
            f"[{self.name}] n={self.n:4d}  "
            f"EV={sign}{self.mean_pct:.2f}%  "
            f"median={self.median_pct:+.2f}%  "
            f"σ={self.stdev_pct:.2f}%  "
            f"win={self.win_rate*100:.1f}%  "
            f"SE={self.se_pct:.2f}%  t={self.t_stat:+.2f}"
            + (f"  // {self.note}" if self.note else "")
        )


def _stats(name: str, returns_pct: Sequence[float], note: str = "") -> EdgeStat:
    n = len(returns_pct)
    if n == 0:
        return EdgeStat(name, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, note or "no samples")
    mean = statistics.fmean(returns_pct)
    median = statistics.median(returns_pct)
    stdev = statistics.stdev(returns_pct) if n > 1 else 0.0
    wins = sum(1 for r in returns_pct if r > 0)
    win_rate = wins / n
    se = stdev / math.sqrt(n) if n > 1 else 0.0
    t = mean / se if se > 0 else 0.0
    return EdgeStat(name, n, mean, median, stdev, win_rate, se, t, note)


# ---- 個別エッジ ---------------------------------------------------------

_INTRADAY_FIELDS = [
    ("next_day_905_ret", "9:05"),
    ("next_day_910_ret", "9:10"),
    ("next_day_915_ret", "9:15"),
    ("next_day_930_ret", "9:30"),
    ("next_day_1000_ret", "10:00"),
    ("next_day_morning_ret", "前場引"),
]


def announce_next_day_edge(records: Iterable[dict[str, Any]]) -> dict[str, EdgeStat]:
    """発表翌日エッジ (普通株、翌日寄りロング → next_day_XXX_ret で利確)。

    9:05 〜 前場引まで全時刻を返す。主戦略は 9:10。
    """
    buckets: dict[str, list[float]] = {field: [] for field, _ in _INTRADAY_FIELDS}
    for r in records:
        if r.get("type") != "普通":
            continue
        if r.get("status") not in ("complete", "nextday"):
            continue
        for field, _ in _INTRADAY_FIELDS:
            v = r.get(field)
            if v is not None:
                buckets[field].append(float(v))
    return {
        field: _stats(f"発表翌日(普通) next_open→{label}", buckets[field])
        for field, label in _INTRADAY_FIELDS
    }


GD_THRESHOLD_PCT = -0.5  # delivery_gap_pct がこの値以下で GD と判定 (po-tracker FIELDS.md 準拠)


def delivery_day_edge(
    records: Iterable[dict[str, Any]],
    gd_only: bool = True,
) -> EdgeStat:
    """受渡日エッジ (普通株、受渡日 GD で寄りロング → 引け売り)。

    GD 判定: delivery_gap_pct <= -0.5 (% : 受渡日始値が前営業日終値より 0.5% 以上下)。
    gd_only=False にすると GD 条件無しの全件平均を返す (参考値)。
    """
    samples: list[float] = []
    for r in records:
        if r.get("type") != "普通":
            continue
        if r.get("status") != "complete":
            continue
        dr = r.get("delivery_ret")
        if dr is None:
            continue
        if gd_only:
            gap = r.get("delivery_gap_pct")
            if gap is None or gap > GD_THRESHOLD_PCT:
                continue
        samples.append(float(dr))
    label = "受渡日エッジ(普通) 受渡日寄り→引け"
    if gd_only:
        label += f" [GD: gap<={GD_THRESHOLD_PCT}%]"
    else:
        label += " [GD条件なし]"
    return _stats(label, samples)


def reit_short_edge(records: Iterable[dict[str, Any]]) -> EdgeStat:
    """リートエッジ (REIT、翌日寄りショート → 決定日引け買戻)。short_return = -ret_close。"""
    samples: list[float] = []
    for r in records:
        if r.get("type") != "リート":
            continue
        if r.get("status") != "complete":
            continue
        rc = r.get("ret_close")
        if rc is None:
            continue
        samples.append(-float(rc))
    return _stats("リートエッジ next_open ショート→決定日引け買戻", samples)


# ---- リピーター罠フィルタ ----------------------------------------------

def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def filter_repeaters(
    records: Sequence[dict[str, Any]],
    consecutive_years: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """3 年連続同月に同銘柄が PO を出した場合の「リピーター罠」レコードを分離する。

    戻り値: (safe_records, flagged_records)
    """
    # (code, month) -> set of years
    seen: dict[tuple[str, int], set[int]] = {}
    for r in records:
        code = r.get("code")
        d = _parse_date(r.get("announce_date")) or _parse_date(r.get("decision_date"))
        if not code or not d:
            continue
        seen.setdefault((code, d.month), set()).add(d.year)

    safe: list[dict[str, Any]] = []
    flagged: list[dict[str, Any]] = []
    for r in records:
        code = r.get("code")
        d = _parse_date(r.get("announce_date")) or _parse_date(r.get("decision_date"))
        if not code or not d:
            safe.append(r)
            continue
        years = seen.get((code, d.month), set())
        # 当年を含む直近 consecutive_years が連続して seen にあるか
        target = {d.year - i for i in range(consecutive_years)}
        if target.issubset(years):
            flagged.append(r)
        else:
            safe.append(r)
    return safe, flagged


# ---- 概観ヘルパ --------------------------------------------------------

def overview(records: Sequence[dict[str, Any]]) -> str:
    types = Counter(r.get("type") for r in records)
    statuses = Counter(r.get("status") for r in records)
    return (
        f"records: {len(records)}  "
        f"types: {dict(types)}  "
        f"statuses: {dict(statuses)}"
    )


# ---- CLI -------------------------------------------------------------

def main() -> None:
    from fetchers.po import load_cached

    payload = load_cached()
    records: list[dict[str, Any]] = payload["records"]

    safe, flagged = filter_repeaters(records, consecutive_years=3)

    def run(name: str, source: Sequence[dict[str, Any]]) -> None:
        print(f"\n=== {name} ===")
        print(overview(source))
        for stat in announce_next_day_edge(source).values():
            print(stat.format())
        print(delivery_day_edge(source, gd_only=False).format())
        print(delivery_day_edge(source, gd_only=True).format())
        print(reit_short_edge(source).format())

    run("全件", records)
    run(f"リピーター罠除外 (3 年連続同月、除外 {len(flagged)} 件)", safe)


if __name__ == "__main__":
    main()

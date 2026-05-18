"""po-tracker セッションで検証済の 3 つの PO エッジ戦略をハブ側に移植したもの。

参照 EV (po-tracker セッション時点):
  1. 発表翌日エッジ  : 普通株、翌日寄りロング → 9:10売り       EV +0.66%
  2. 受渡日エッジ    : 普通株、受渡日 GD で寄りロング → 引け売り EV +0.80%
  3. リートエッジ    : REIT、翌日寄りショート → 決定日引け買戻  EV +1.12%

注意事項:
  - 戦略 1 の "9:10売り" は粒度の細かい intraday データを必要とし、現状の
    po_records.json には含まれない。本実装では参考値として
      * open_to_max (next_open → 翌日高値) : 上限ベンチマーク
      * ret_open    (next_open → dec_open) : 翌寄り→決定日寄り保有時のリターン
    を併記する。
  - リピーター罠 (3 年連続同月の同銘柄 PO) は filter_repeaters() で除外可能。
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable, Sequence


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

def announce_next_day_edge(records: Iterable[dict[str, Any]]) -> dict[str, EdgeStat]:
    """発表翌日エッジ (普通株)。9:10 売り相当データが無いため参考値 2 種を返す。"""
    samples_open_to_max: list[float] = []   # ロング上限ベンチ
    samples_open_to_dec_open: list[float] = []  # 翌寄り→決定日寄りまで持った場合
    for r in records:
        if r.get("type") != "普通":
            continue
        if r.get("status") not in ("complete", "nextday"):
            continue
        otm = r.get("open_to_max")
        ro = r.get("ret_open")
        if otm is not None:
            samples_open_to_max.append(float(otm))
        if ro is not None:
            samples_open_to_dec_open.append(float(ro))
    return {
        "open_to_max": _stats(
            "発表翌日(普通) next_open→翌日高値",
            samples_open_to_max,
            note="ロング上限ベンチ。実取引で取り切るのは困難",
        ),
        "open_to_dec_open": _stats(
            "発表翌日(普通) next_open→決定日寄り",
            samples_open_to_dec_open,
            note="9:10 相当の intraday データ未整備のため代替指標",
        ),
    }


def delivery_day_edge(
    records: Iterable[dict[str, Any]],
    gd_only: bool = False,
) -> EdgeStat:
    """受渡日エッジ (普通株、受渡日 GD で寄りロング → 引け売り)。delivery_ret を使用。

    gd_only=True で「受渡日寄りが発行価格 (issue_price) を下回る = GD」のみに絞る。
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
            ip = r.get("issue_price")
            do = r.get("delivery_open")
            if ip is None or do is None or do >= ip:
                continue
        samples.append(float(dr))
    name = "受渡日エッジ(普通) 受渡日寄り→引け" + (" [GD条件付]" if gd_only else "")
    return _stats(name, samples)


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
        announce = announce_next_day_edge(source)
        print(announce["open_to_max"].format())
        print(announce["open_to_dec_open"].format())
        print(delivery_day_edge(source).format())
        print(delivery_day_edge(source, gd_only=True).format())
        print(reit_short_edge(source).format())

    run("全件", records)
    run(f"リピーター罠除外 (3 年連続同月、除外 {len(flagged)} 件)", safe)


if __name__ == "__main__":
    main()

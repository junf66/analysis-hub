"""_query_report の集計ヘルパ、特に collapse-daily (非独立サンプル補正) を検証。

同一 (code, event_date) の複数レコードは翌日リターンが同値で独立でないため、
collapse=True で 1 観測に平均集約して n/t の水増しを防ぐ。
"""
from __future__ import annotations

import unittest

from scripts._query_report import metric_values


def _rec(code: str, date: str, val: float | None) -> dict:
    return {"code": code, "event_date": date, "attrs": {"m": val} if val is not None else {}}


class TestMetricValues(unittest.TestCase):
    def test_raw_returns_all(self) -> None:
        recs = [_rec("7203", "2026-01-01", 1.0), _rec("7203", "2026-01-01", 3.0), _rec("6758", "2026-01-02", 2.0)]
        self.assertEqual(sorted(metric_values(recs, "m")), [1.0, 2.0, 3.0])

    def test_skips_none(self) -> None:
        recs = [_rec("7203", "2026-01-01", 1.0), _rec("6758", "2026-01-02", None)]
        self.assertEqual(metric_values(recs, "m"), [1.0])

    def test_collapse_merges_same_code_date(self) -> None:
        # 7203 が同日 2 件 (1.0, 3.0) → 平均 2.0 の 1 観測。6758 は別日で独立。
        recs = [_rec("7203", "2026-01-01", 1.0), _rec("7203", "2026-01-01", 3.0), _rec("6758", "2026-01-02", 5.0)]
        collapsed = metric_values(recs, "m", collapse=True)
        self.assertEqual(len(collapsed), 2)  # 3 件 → 2 独立観測
        self.assertEqual(sorted(collapsed), [2.0, 5.0])

    def test_collapse_sorted_by_date(self) -> None:
        recs = [_rec("A", "2026-03-01", 9.0), _rec("B", "2026-01-01", 1.0), _rec("C", "2026-02-01", 5.0)]
        # 日付順に並ぶ (累積プロット用)
        self.assertEqual(metric_values(recs, "m", collapse=True), [1.0, 5.0, 9.0])

    def test_collapse_different_codes_same_date_kept(self) -> None:
        # 同日でも別銘柄は独立 → 集約しない
        recs = [_rec("A", "2026-01-01", 1.0), _rec("B", "2026-01-01", 3.0)]
        self.assertEqual(len(metric_values(recs, "m", collapse=True)), 2)


if __name__ == "__main__":
    unittest.main()

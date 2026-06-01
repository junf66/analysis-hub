"""#7/#8 シグナル抽出 (enrich_margin_signal / enrich_short_signal) と
共通価格付与 (enrich_common.returns_from_bars) を mock データで検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import enrich_common as ec
from scripts.edge_candidates import enrich_margin_signal as ems
from scripts.edge_candidates import enrich_short_signal as ess


def _bars(seq: list[tuple[str, float, float]]) -> list[dict]:
    return [{"Date": d, "O": o, "C": c} for d, o, c in seq]


class TestReturnsFromBars(unittest.TestCase):
    def test_entry_and_returns(self) -> None:
        # event 2025-01-01。翌営業日(02)寄り=100、+1日=03引け110、+3日=05引け130
        bars = _bars([("2025-01-01", 90, 95), ("2025-01-02", 100, 101),
                      ("2025-01-03", 102, 110), ("2025-01-04", 111, 120),
                      ("2025-01-05", 121, 130)])
        a = ec.returns_from_bars(bars, "2025-01-01", [1, 3])
        self.assertEqual(a["entry_date"], "2025-01-02")
        self.assertEqual(a["entry_open"], 100)
        self.assertAlmostEqual(a["d1_ret"], 10.0)    # 110/100-1
        self.assertAlmostEqual(a["d3_ret"], 30.0)    # 130/100-1

    def test_skip_bars_shifts_entry(self) -> None:
        bars = _bars([("2025-01-01", 90, 95), ("2025-01-02", 100, 101),
                      ("2025-01-03", 200, 210), ("2025-01-04", 220, 240)])
        a = ec.returns_from_bars(bars, "2025-01-01", [1], skip_bars=1)
        self.assertEqual(a["entry_date"], "2025-01-03")   # 1本スキップ
        self.assertEqual(a["entry_open"], 200)
        self.assertAlmostEqual(a["d1_ret"], 20.0)         # 240/200-1

    def test_prefers_adjusted(self) -> None:
        bars = [{"Date": "2025-01-02", "O": 100, "C": 101, "AdjO": 50, "AdjC": 55},
                {"Date": "2025-01-03", "O": 60, "C": 60, "AdjO": 60, "AdjC": 66}]
        a = ec.returns_from_bars(bars, "2025-01-01", [1])
        self.assertEqual(a["entry_open"], 50)             # AdjO 優先
        self.assertAlmostEqual(a["d1_ret"], 32.0)         # 66/50-1

    def test_no_entry_bar(self) -> None:
        bars = _bars([("2025-01-01", 90, 95)])
        self.assertIn("price_error", ec.returns_from_bars(bars, "2025-01-01", [1]))


class TestMarginSignals(unittest.TestCase):
    def _rec(self, code, date, lv):
        return {"Code": code, "Date": date, "LongVol": lv}

    def test_detects_drop(self) -> None:
        recs = [self._rec("100", "2025-01-03", 100000),
                self._rec("100", "2025-01-10", 60000)]   # -40%
        sig = ems.compute_margin_signals(recs, threshold=-30.0)
        self.assertEqual(len(sig), 1)
        self.assertEqual(sig[0]["event_date"], "2025-01-10")
        self.assertAlmostEqual(sig[0]["attrs"]["chg_pct"], -40.0)

    def test_below_threshold_ignored(self) -> None:
        recs = [self._rec("100", "2025-01-03", 100000),
                self._rec("100", "2025-01-10", 80000)]   # -20% > -30
        self.assertEqual(ems.compute_margin_signals(recs, threshold=-30.0), [])

    def test_min_prev_long_floor(self) -> None:
        recs = [self._rec("100", "2025-01-03", 5000),
                self._rec("100", "2025-01-10", 1000)]    # -80% だが prev<10000
        self.assertEqual(ems.compute_margin_signals(recs, threshold=-30.0,
                                                    min_prev_long=10000), [])


class TestShortSignals(unittest.TestCase):
    def _rec(self, code, calc, disc, filer, ratio):
        return {"Code": code, "CalcDate": calc, "DiscDate": disc,
                "SSName": filer, "ShrtPosToSO": ratio}

    def test_detects_surge_with_forward_fill(self) -> None:
        # A=1.0% 継続、B が 0→0.6% 追加 → 合計 1.0→1.6 = +60%
        recs = [self._rec("100", "2025-01-06", "2025-01-07", "A", 0.010),
                self._rec("100", "2025-01-08", "2025-01-09", "B", 0.006)]
        sig = ess.compute_short_signals(recs, threshold=50.0)
        self.assertEqual(len(sig), 1)
        self.assertEqual(sig[0]["event_date"], "2025-01-09")
        self.assertAlmostEqual(sig[0]["attrs"]["total_ratio"], 0.016, places=6)
        self.assertAlmostEqual(sig[0]["attrs"]["chg_pct"], 60.0, places=4)

    def test_small_increase_ignored(self) -> None:
        recs = [self._rec("100", "2025-01-06", "2025-01-07", "A", 0.010),
                self._rec("100", "2025-01-08", "2025-01-09", "A", 0.012)]  # +20%
        self.assertEqual(ess.compute_short_signals(recs, threshold=50.0), [])

    def test_min_total_floor(self) -> None:
        # prev_total 0.001 < 0.005 floor → 急増でも無視
        recs = [self._rec("100", "2025-01-06", "2025-01-07", "A", 0.001),
                self._rec("100", "2025-01-08", "2025-01-09", "A", 0.010)]
        self.assertEqual(ess.compute_short_signals(recs, threshold=50.0,
                                                   min_total=0.005), [])


if __name__ == "__main__":
    unittest.main()

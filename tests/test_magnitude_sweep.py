"""analyze_magnitude_sweep の純関数を検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import analyze_magnitude_sweep as ms


class TestPrimaryMag(unittest.TestCase):
    def test_bad_first(self):
        r = {"bad_factors": [{"metric": {"NP_YoY_pct": -15.0}}],
             "good_factors": [{"metric": {"Div_revision_pct": 5.0}}]}
        self.assertEqual(ms.primary_mag(r), -15.0)

    def test_good_fallback(self):
        r = {"bad_factors": [{"metric": {}}], "good_factors": [{"metric": {"Div_revision_pct": 5.0}}]}
        self.assertEqual(ms.primary_mag(r), 5.0)

    def test_none(self):
        self.assertIsNone(ms.primary_mag({"bad_factors": [{"metric": {}}], "good_factors": []}))


class TestCellStats(unittest.TestCase):
    def _recs(self, vals):
        return [{"event_date": f"2025-01-{i+1:02d}", "attrs": {"next_day_open_to_close_ret": v}}
                for i, v in enumerate(vals)]

    def test_short_direction(self):
        # 全部 -1% のロングリターン → short が有利、net = +1 - 0.15
        s = ms.cell_stats(self._recs([-1.0] * 40))
        self.assertEqual(s["dir"], "short")
        self.assertAlmostEqual(s["net_ev"], 1.0 - ms.SHORT_COST, places=4)

    def test_long_direction(self):
        s = ms.cell_stats(self._recs([1.0] * 40))
        self.assertEqual(s["dir"], "long")
        self.assertAlmostEqual(s["net_ev"], 1.0 - ms.LONG_COST, places=4)

    def test_small_n_none(self):
        self.assertIsNone(ms.cell_stats(self._recs([1.0] * 5)))


if __name__ == "__main__":
    unittest.main()

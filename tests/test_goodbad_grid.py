"""analyze_goodbad_grid の純関数を検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import analyze_goodbad_grid as gg


class TestBand(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(gg.band(1.5), "<3%")
        self.assertEqual(gg.band(4.0), "3-5%")
        self.assertEqual(gg.band(7.0), "5-10%")
        self.assertEqual(gg.band(20.0), "≥10%")
        self.assertIsNone(gg.band(None))


class TestGoodRow(unittest.TestCase):
    def test_with_magnitude(self):
        r = {"good_factors": [{"subpattern_hint": "zouhai", "metric": {"Div_revision_pct": 8.0}}]}
        self.assertEqual(gg.good_row(r), "5-10%")

    def test_no_magnitude_jisha(self):
        r = {"good_factors": [{"subpattern_hint": "jisha", "metric": {}}]}
        self.assertEqual(gg.good_row(r), "程度なし(自社株買/分割)")

    def test_none(self):
        self.assertIsNone(gg.good_row({"good_factors": [{"subpattern_hint": "x", "metric": {}}]}))


if __name__ == "__main__":
    unittest.main()

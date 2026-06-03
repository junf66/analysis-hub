"""query_kouaku の程度フィルタ補助関数 _primary_mag を検証。"""
from __future__ import annotations

import unittest

from scripts import query_kouaku as qk


class TestPrimaryMag(unittest.TestCase):
    def test_bad_first(self):
        r = {"bad_factors": [{"metric": {"NP_YoY_pct": -15.0}}],
             "good_factors": [{"metric": {"Div_revision_pct": 5.0}}]}
        self.assertEqual(qk._primary_mag(r), -15.0)

    def test_good_fallback(self):
        r = {"bad_factors": [], "good_factors": [{"metric": {"Div_revision_pct": 8.0}}]}
        self.assertEqual(qk._primary_mag(r), 8.0)

    def test_none(self):
        self.assertIsNone(qk._primary_mag({"bad_factors": [{"metric": {}}], "good_factors": []}))


if __name__ == "__main__":
    unittest.main()

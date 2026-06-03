"""analyze_buyback_earnings の純関数 (YoY帯分け / 判定) を検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import analyze_buyback_earnings as abe


class TestYoyBand(unittest.TestCase):
    def test_bands(self) -> None:
        self.assertEqual(abe.yoy_band(-15), "重減 ≤-10%")
        self.assertEqual(abe.yoy_band(-10), "重減 ≤-10%")
        self.assertEqual(abe.yoy_band(-7), "中減 -10〜-5%")
        self.assertEqual(abe.yoy_band(-3), "軽減 -5〜0%")     # キッコーマン型
        self.assertEqual(abe.yoy_band(-0.1), "軽減 -5〜0%")
        self.assertEqual(abe.yoy_band(2), "軽増 0〜+5%")
        self.assertEqual(abe.yoy_band(8), "中増 +5〜+10%")
        self.assertEqual(abe.yoy_band(25), "増益 ≥+10%")

    def test_none(self) -> None:
        self.assertIsNone(abe.yoy_band(None))


class TestVerdict(unittest.TestCase):
    def test_pass(self) -> None:
        self.assertEqual(abe._verdict({"n": 100, "net_ev": 1.0, "t_clust": 2.5,
                                       "fdr_significant": True, "oos": 0.8}), "★通過")

    def test_raw_only(self) -> None:
        self.assertEqual(abe._verdict({"n": 100, "net_ev": 1.0, "t_clust": 2.5,
                                       "fdr_significant": False, "oos": 0.8}), "△(FDR前のみ)")

    def test_reject(self) -> None:
        self.assertEqual(abe._verdict({"n": 100, "net_ev": -0.5, "t_clust": -2.0,
                                       "fdr_significant": False, "oos": -1.0}), "✕")

    def test_small_n(self) -> None:
        self.assertEqual(abe._verdict({"n": 10, "net_ev": 5.0, "t_clust": 3.0,
                                       "fdr_significant": True, "oos": 1.0}), "—(n<30)")


if __name__ == "__main__":
    unittest.main()

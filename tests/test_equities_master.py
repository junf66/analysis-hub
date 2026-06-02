"""fetch_equities_master の純関数 (規模区分丸め) を検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import fetch_equities_master as fem


class TestScaleBand(unittest.TestCase):
    def test_large(self) -> None:
        self.assertEqual(fem.scale_band("TOPIX Core30"), "大型")
        self.assertEqual(fem.scale_band("TOPIX Large70"), "大型")

    def test_mid(self) -> None:
        self.assertEqual(fem.scale_band("TOPIX Mid400"), "中型")

    def test_small(self) -> None:
        self.assertEqual(fem.scale_band("TOPIX Small 1"), "小型")
        self.assertEqual(fem.scale_band("TOPIX Small 2"), "小型")
        self.assertEqual(fem.scale_band("-"), "小型")
        self.assertEqual(fem.scale_band(None), "小型")


if __name__ == "__main__":
    unittest.main()

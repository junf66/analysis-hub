"""enrich_split_axes の純関数 (軸ラベル付与) を mock で検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import analyze_split_detailed as asd
from scripts.edge_candidates import enrich_split_axes as esa


class TestComboLabel(unittest.TestCase):
    def test_alone(self) -> None:
        self.assertEqual(esa.combo_label({"good_split"}), "単独")

    def test_buyback_simultaneous(self) -> None:
        self.assertEqual(esa.combo_label({"good_split", "good_jisha"}), "自社株買い同時")

    def test_priority_buyback_over_zouhai(self) -> None:
        self.assertEqual(esa.combo_label({"good_split", "good_jisha", "good_zouhai"}),
                         "複合(その他)")

    def test_bad_only(self) -> None:
        self.assertEqual(esa.combo_label({"good_split", "bad_tokuson"}), "悪材料同時")

    def test_good_with_bad(self) -> None:
        self.assertEqual(esa.combo_label({"good_split", "good_zouhai", "bad_genson"}),
                         "増配同時+悪材料")


class TestIssType(unittest.TestCase):
    def test_as_of_latest_before(self) -> None:
        idx = {"100": [("2025-01-03", "1"), ("2025-01-10", "2")]}
        self.assertEqual(esa.isstype_as_of(idx, "100", "2025-01-12"), "貸借")
        self.assertEqual(esa.isstype_as_of(idx, "100", "2025-01-05"), "信用")

    def test_none_before_first(self) -> None:
        idx = {"100": [("2025-01-10", "2")]}
        self.assertIsNone(esa.isstype_as_of(idx, "100", "2025-01-05"))

    def test_unknown_code(self) -> None:
        self.assertIsNone(esa.isstype_as_of({}, "999", "2025-01-05"))


class TestReitCode(unittest.TestCase):
    def test_reit_range(self) -> None:
        self.assertTrue(esa.is_reit_code("8951"))
        self.assertTrue(esa.is_reit_code("89510"))

    def test_non_reit(self) -> None:
        self.assertFalse(esa.is_reit_code("8035"))
        self.assertFalse(esa.is_reit_code("7203"))


class TestAxisFields(unittest.TestCase):
    def _bars(self, seq):
        out = []
        for d, o, c, vo, af in seq:
            out.append({"Date": d, "O": o, "C": c, "Vo": vo, "AdjFactor": af})
        return out

    def test_gap_turnover_ratio(self) -> None:
        bars = self._bars([
            ("2025-01-06", 100, 100, 1000, 1.0),   # before (prev_close=100)
            ("2025-01-07", 102, 110, 2000, 1.0),   # entry (gap +2%)
            ("2025-01-08", 111, 120, 1500, 1.0),
            ("2025-02-10", 60, 61, 3000, 0.5),     # ex-date: 1:2 split → AdjFactor 0.5
        ])
        a = esa.axis_fields_from_bars(bars, "2025-01-06")
        self.assertAlmostEqual(a["gap_pct"], 2.0)         # 102/100-1
        self.assertEqual(a["entry_price"], 102)
        self.assertAlmostEqual(a["split_ratio"], 2.0)     # 1/0.5
        self.assertEqual(a["ex_date"], "2025-02-10")
        self.assertAlmostEqual(a["turnover_20"], 100 * 1000)  # 1本のみ before

    def test_empty_when_no_after(self) -> None:
        bars = self._bars([("2025-01-06", 100, 100, 1000, 1.0)])
        self.assertEqual(esa.axis_fields_from_bars(bars, "2025-01-06"), {})


class TestAnalyzeBuckets(unittest.TestCase):
    def test_gap_buckets(self) -> None:
        self.assertEqual(asd._gap_bucket(2.0), "GU(>+1%)")
        self.assertEqual(asd._gap_bucket(0.0), "フラット(±0.3%)")
        self.assertEqual(asd._gap_bucket(-2.0), "中GD(-3〜-1%)")
        self.assertEqual(asd._gap_bucket(-5.0), "深GD(<-3%)")
        self.assertIsNone(asd._gap_bucket(None))

    def test_ratio_buckets(self) -> None:
        self.assertEqual(asd._ratio_bucket(2.0), "1:2")
        self.assertEqual(asd._ratio_bucket(5.0), "1:5〜9")
        self.assertEqual(asd._ratio_bucket(10.0), "1:10以上")
        self.assertEqual(asd._ratio_bucket(None), "比率不明")

    def test_turnover_price_buckets(self) -> None:
        self.assertEqual(asd._turnover_bucket(2e9), "高(≥10億/日)")
        self.assertEqual(asd._turnover_bucket(5e7), "低(<1億/日)")
        self.assertEqual(asd._price_bucket(54000), "高単価(≥1万円)")
        self.assertEqual(asd._price_bucket(500), "低単価(<1千円)")

    def test_verdict(self) -> None:
        base = {"n": 100, "win": 50, "oos": 1.0, "fdr_significant": True}
        self.assertEqual(asd.verdict({**base, "net_ev": 2.0, "t_clust": 3.0}), "★優先")
        self.assertEqual(asd.verdict({**base, "net_ev": 0.8, "t_clust": 2.3}), "通過")
        self.assertEqual(asd.verdict({**base, "net_ev": -0.5, "t_clust": -2.0, "win": 40}), "除外")
        self.assertEqual(asd.verdict({**base, "net_ev": 0.8, "t_clust": 1.5}), "—")


if __name__ == "__main__":
    unittest.main()

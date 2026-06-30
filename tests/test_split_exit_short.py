"""analyze_split_exit_short.short_rows の符号/市場控除/貸借フィルタのテスト。"""
import unittest

from scripts.edge_candidates import analyze_split_exit_short as ses


class TestShortRows(unittest.TestCase):
    def setUp(self):
        # 営業日 5日。TOPIX は横ばい(O=C=100)で市場控除=0 にして純粋にショート符号を見る。
        self.cal = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
        self.tpx = {d: {"O": 100.0, "C": 100.0} for d in self.cal}
        self.cidx = {d: i for i, d in enumerate(self.cal)}
        # 1380: ex_date=01-01, 翌日(01-02)寄り100 → +3日(01-05)引け 90 = -10% → ショート+10%
        self.ebars = {"1380": {"2024-01-01": [100.0, 100.0], "2024-01-02": [100.0, 100.0],
                               "2024-01-03": [100.0, 100.0], "2024-01-04": [100.0, 100.0],
                               "2024-01-05": [100.0, 90.0]}}
        self.recs = [{"code": "1380", "event_date": "2023-12-01",
                      "attrs": {"ex_date": "2024-01-01"}}]

    def test_short_sign_and_market_adjust(self):
        rows = ses.short_rows(self.recs, self.ebars, self.tpx, self.cal, self.cidx,
                              entry_off=1, n=3)
        self.assertEqual(len(rows), 1)
        ex, code, pnl = rows[0]
        self.assertEqual(code, "1380")
        # 株−10%・市場0% → ショートpnl = -(-10 - 0) - 0.15 = +9.85
        self.assertAlmostEqual(pnl, 10.0 - ses.SHORT, places=6)

    def test_shortable_filter_excludes_non_taishaku(self):
        mh = {"2023-01-01": {"13800": {"MrgnNm": "信用", "scale_band": "小型"}}}
        rows = ses.short_rows(self.recs, self.ebars, self.tpx, self.cal, self.cidx,
                              entry_off=1, n=3, shortable_only=True, master_hist=mh)
        self.assertEqual(rows, [])   # 信用(非貸借)は空売り不可で除外
        mh2 = {"2023-01-01": {"13800": {"MrgnNm": "貸借", "scale_band": "小型"}}}
        rows2 = ses.short_rows(self.recs, self.ebars, self.tpx, self.cal, self.cidx,
                               entry_off=1, n=3, shortable_only=True, master_hist=mh2)
        self.assertEqual(len(rows2), 1)

    def test_stat_basic(self):
        rows = [("2024-01-01", "A", 2.0), ("2024-02-01", "B", -1.0), ("2024-03-01", "C", 3.0)]
        s = ses._stat(rows)
        self.assertEqual(s["n"], 3)
        self.assertAlmostEqual(s["net"], (2 - 1 + 3) / 3)
        self.assertAlmostEqual(s["win"], 2 / 3 * 100)


if __name__ == "__main__":
    unittest.main()

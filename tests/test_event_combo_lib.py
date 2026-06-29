"""event_combo_lib の純関数 (リターン算出・方向別統計・非重複間引き・PIT) のテスト。"""
import unittest

from scripts.edge_candidates import event_combo_lib as ec


class TestReturns(unittest.TestCase):
    def test_returns_and_gap(self):
        bars = {"2021-06-01": [100.0, 102.0],   # prev (event day)
                "2021-06-02": [105.0, 110.0],   # 反応日 (entry): gap=105/102, d0=110/105
                "2021-06-03": [110.0, 121.0],   # +1
                "2021-06-04": [120.0, 99.0]}    # +2 (未使用)
        r = ec.returns_from_event_bars(bars, "2021-06-01", days=[0, 1])
        self.assertEqual(r["entry_date"], "2021-06-02")
        self.assertAlmostEqual(r["entry_open"], 105.0)
        self.assertAlmostEqual(r["gap"], (105 / 102 - 1) * 100, places=6)
        self.assertAlmostEqual(r["d0_ret"], (110 / 105 - 1) * 100, places=6)
        self.assertAlmostEqual(r["d1_ret"], (121 / 105 - 1) * 100, places=6)

    def test_no_entry_bar(self):
        bars = {"2021-06-01": [100.0, 102.0]}
        self.assertIn("price_error", ec.returns_from_event_bars(bars, "2021-06-01"))

    def test_gap_bucket(self):
        self.assertEqual(ec.gap_bucket(5.0), "GU(>+3%)")
        self.assertEqual(ec.gap_bucket(-5.0), "GD(<-3%)")
        self.assertEqual(ec.gap_bucket(0.5), "フラット")
        self.assertEqual(ec.gap_bucket(None), "不明")


class TestCodes(unittest.TestCase):
    def test_code_norm(self):
        self.assertEqual(ec.code4("13010"), "1301")
        self.assertEqual(ec.code4("1301"), "1301")
        self.assertEqual(ec.code5("1301"), "13010")
        self.assertEqual(ec.code5("13010"), "13010")


class TestPIT(unittest.TestCase):
    def test_pit_picks_latest_le_event(self):
        mh = {"2020-06-01": {"13010": {"scale_band": "大型", "MktNm": "東証一部"}},
              "2022-06-01": {"13010": {"scale_band": "中型", "MktNm": "プライム"}}}
        # 2021イベント → 2020スナップショット (大型)
        a = ec.pit_attrs(mh, "1301", "2021-03-15")
        self.assertEqual(a["scale_band"], "大型")
        # 2023イベント → 2022スナップショット (中型)
        b = ec.pit_attrs(mh, "1301", "2023-03-15")
        self.assertEqual(b["scale_band"], "中型")
        # スナップショット以前 → 最古を使用
        c = ec.pit_attrs(mh, "1301", "2019-01-01")
        self.assertEqual(c["scale_band"], "大型")


class TestDirectionalStats(unittest.TestCase):
    def _recs(self, vals):
        return [{"code": f"100{i}", "event_date": f"2021-01-{i+1:02d}", "attrs": {"m": v}}
                for i, v in enumerate(vals)]

    def test_long_short_sign(self):
        recs = self._recs([2.0, 2.0, 2.0, 2.0])      # +2% 一定
        lo = ec.directional_stats(recs, "m", "long", 0.2)
        sh = ec.directional_stats(recs, "m", "short", 0.15)
        self.assertAlmostEqual(lo["net_ev"], 2.0 - 0.2)     # long: ret-cost
        self.assertAlmostEqual(sh["net_ev"], -2.0 - 0.15)   # short: -ret-cost
        self.assertEqual(lo["win"], 100.0)                  # long pnl>0
        self.assertEqual(sh["win"], 0.0)                    # short pnl<0

    def test_none_skipped(self):
        recs = self._recs([1.0]) + [{"code": "9999", "event_date": "2021-02-01", "attrs": {}}]
        s = ec.directional_stats(recs, "m", "long", 0.0)
        self.assertEqual(s["n"], 1)

    def test_nonoverlap_keep_dedups_same_code(self):
        # 同一銘柄で hold_days=5 以内の重複は1本に間引かれる
        obs = [("2021-01-01", "A", 1.0), ("2021-01-03", "A", 9.0),  # 2日差→後者は捨てる
               ("2021-01-20", "A", 2.0),                            # 17日差→採用
               ("2021-01-01", "B", 5.0)]
        keep = ec._nonoverlap_keep(obs, hold_days=5)
        self.assertEqual(sorted(keep), [1.0, 2.0, 5.0])


if __name__ == "__main__":
    unittest.main()

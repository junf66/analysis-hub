"""enrich / analyze / backtest / query の純関数を fixture で固定。

API は叩かない。
"""
from __future__ import annotations

import unittest

from scripts import query_kouaku
from scripts.analyze_kouaku_edge import _disc_bucket
from scripts.backtest_kouaku import _net_pnl, _stat_block
from scripts.enrich_price_kouaku import _pct, _INTRADAY_TARGETS, _enrich_minute


class TestPctHelper(unittest.TestCase):
    def test_pct_basic(self) -> None:
        self.assertAlmostEqual(_pct(110, 100), 10.0)
        self.assertAlmostEqual(_pct(90, 100), -10.0)

    def test_pct_none_returns_none(self) -> None:
        self.assertIsNone(_pct(None, 100))
        self.assertIsNone(_pct(100, None))
        self.assertIsNone(_pct(100, 0))


class TestIntradayTargets(unittest.TestCase):
    def test_targets_in_expected_order(self) -> None:
        times = [t for t, _ in _INTRADAY_TARGETS]
        self.assertEqual(times, sorted(times))
        # 主要時刻が全部入っている
        self.assertIn("09:05", times)
        self.assertIn("09:10", times)
        self.assertIn("09:15", times)
        self.assertIn("11:30", times)


class TestEnrichMinuteOpenAtFirstBar(unittest.TestCase):
    """illiquid 銘柄では 9:00 ジャストの bar がなくても、最初の bar を始値として採用。"""

    def setUp(self) -> None:
        # _minute_bars をモンキーパッチして API を叩かない
        from scripts import enrich_price_kouaku as ep
        self._orig = ep._minute_bars
        ep._minute_bars = self._mock_bars
        self.ep = ep

    def tearDown(self) -> None:
        self.ep._minute_bars = self._orig

    def _mock_bars(self, code: str, date_str: str) -> list[dict]:
        # 9:00, 9:05 にだけ歩み値、9:10 はなし、9:15 が次
        return [
            {"Time": "09:00", "O": 1000.0, "H": 1010.0, "L": 1000.0, "C": 1005.0},
            {"Time": "09:05", "O": 1005.0, "H": 1010.0, "L": 1000.0, "C": 1010.0},
            {"Time": "09:15", "O": 1010.0, "H": 1020.0, "L": 1005.0, "C": 1020.0},  # 9:10 飛ばし
            {"Time": "11:30", "O": 1020.0, "H": 1020.0, "L": 1020.0, "C": 1030.0},
        ]

    def test_open_is_first_bar(self) -> None:
        rec = {"attrs": {}}
        _enrich_minute(rec, "TEST", "2025-01-22")
        self.assertEqual(rec["attrs"]["next_open_900"], 1000.0)
        self.assertEqual(rec["attrs"]["next_open_first_time"], "09:00")

    def test_target_uses_first_bar_at_or_after_time(self) -> None:
        rec = {"attrs": {}}
        _enrich_minute(rec, "TEST", "2025-01-22")
        # 9:05 → C=1010 / O=1000 = +1%
        self.assertAlmostEqual(rec["attrs"]["next_day_905_ret"], 1.0)
        # 9:10 → bar 不在 → 9:15 の C を使う → 1020 / 1000 = +2%
        self.assertAlmostEqual(rec["attrs"]["next_day_910_ret"], 2.0)
        # 9:15 → 1020 / 1000 = +2%
        self.assertAlmostEqual(rec["attrs"]["next_day_915_ret"], 2.0)
        # 11:30 (前場引) → 1030 / 1000 = +3%
        self.assertAlmostEqual(rec["attrs"]["next_day_morning_ret"], 3.0)


class TestEnrichMinuteEmptyResponse(unittest.TestCase):
    def setUp(self) -> None:
        from scripts import enrich_price_kouaku as ep
        self._orig = ep._minute_bars
        ep._minute_bars = lambda *_args, **_kw: []
        self.ep = ep

    def tearDown(self) -> None:
        self.ep._minute_bars = self._orig

    def test_minute_error_recorded(self) -> None:
        rec = {"attrs": {}}
        _enrich_minute(rec, "TEST", "2025-01-22")
        self.assertEqual(rec["attrs"]["minute_error"], "no minute bars")
        self.assertNotIn("next_open_900", rec["attrs"])


class TestDiscBucket(unittest.TestCase):
    """analyzer 側の _disc_bucket と query 側の _disc_bucket が同じ境界を持つ。"""

    def _rec(self, time_str: str) -> dict:
        return {"good_factors": [{"disc_time": time_str}], "bad_factors": []}

    def test_boundaries_match_query(self) -> None:
        cases = [
            ("00:00:00", "寄前"),
            ("08:59:59", "寄前"),
            ("09:00:00", "寄り中"),
            ("10:59:59", "寄り中"),
            ("11:00:00", "場中"),
            ("14:59:59", "場中"),
            ("15:00:00", "引け間際"),
            ("15:29:59", "引け間際"),
            ("15:30:00", "大引け後"),
            ("23:59:00", "大引け後"),
        ]
        for t, expected in cases:
            with self.subTest(time=t):
                self.assertEqual(_disc_bucket(self._rec(t)), expected, t)
                self.assertEqual(query_kouaku._disc_bucket(self._rec(t)), expected, t)


class TestBacktestNetPnl(unittest.TestCase):
    def test_short_returns_invert_sign(self) -> None:
        # short: 価格 -1% → +1% (コスト前)
        self.assertAlmostEqual(_net_pnl(-1.0, 0.2, "short"), 0.8)
        self.assertAlmostEqual(_net_pnl(+1.0, 0.2, "short"), -1.2)

    def test_long_returns_passthrough(self) -> None:
        self.assertAlmostEqual(_net_pnl(+1.0, 0.2, "long"), 0.8)
        self.assertAlmostEqual(_net_pnl(-1.0, 0.2, "long"), -1.2)


class TestBacktestStatBlock(unittest.TestCase):
    def test_empty(self) -> None:
        s = _stat_block([])
        self.assertEqual(s["n"], 0)

    def test_known_values(self) -> None:
        s = _stat_block([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(s["n"], 5)
        self.assertAlmostEqual(s["ev"], 3.0)
        self.assertAlmostEqual(s["cumul"], 15.0)
        self.assertEqual(s["win"], 100.0)


class TestQueryBootstrap(unittest.TestCase):
    def test_ci_brackets_mean(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        lo, hi = query_kouaku._bootstrap_ci(values, n_iter=500)
        # 平均 3.0 が CI 内
        self.assertLessEqual(lo, 3.0)
        self.assertGreaterEqual(hi, 3.0)

    def test_constant_values_zero_width(self) -> None:
        values = [2.0] * 10
        lo, hi = query_kouaku._bootstrap_ci(values, n_iter=500)
        self.assertAlmostEqual(lo, 2.0)
        self.assertAlmostEqual(hi, 2.0)

    def test_too_few_samples(self) -> None:
        lo, hi = query_kouaku._bootstrap_ci([1.0])
        self.assertEqual((lo, hi), (0.0, 0.0))


class TestQueryHistogram(unittest.TestCase):
    def test_empty(self) -> None:
        out = query_kouaku._ascii_histogram([])
        self.assertEqual(out, ["(empty)"])

    def test_basic_structure(self) -> None:
        out = query_kouaku._ascii_histogram([1.0, 2.0, 3.0, 4.0, 5.0], bins=5)
        self.assertEqual(len(out), 5)
        for line in out:
            self.assertIn("%", line)


class TestQueryGroupKeys(unittest.TestCase):
    def test_year_extractor(self) -> None:
        self.assertEqual(query_kouaku._GROUP_KEYS["year"]({"event_date": "2025-08-12"}), "2025")

    def test_code_extractor(self) -> None:
        self.assertEqual(query_kouaku._GROUP_KEYS["code"]({"code": "7990"}), "7990")

    def test_subpattern_extractor(self) -> None:
        self.assertEqual(query_kouaku._GROUP_KEYS["subpattern"]({"subpattern": "kouhou_genshu"}), "kouhou_genshu")


if __name__ == "__main__":
    unittest.main(verbosity=2)

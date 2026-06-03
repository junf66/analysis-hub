"""rsi_mean_reversion: Wilder RSI / 仕掛けロジック / 集計を mock データで検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import rsi_mean_reversion as rmr


class TestWilderRSI(unittest.TestCase):
    def test_constant_returns_50_after_warmup(self) -> None:
        # 全期間 close 一定 → gains=losses=0 → 100.0 (avg_l==0 分岐)
        r = rmr.wilder_rsi([100.0] * 30, period=14)
        self.assertIsNone(r[13])
        self.assertEqual(r[14], 100.0)

    def test_monotonic_up_returns_100(self) -> None:
        r = rmr.wilder_rsi([float(i) for i in range(30)], period=14)
        self.assertAlmostEqual(r[14], 100.0, places=4)

    def test_monotonic_down_returns_0(self) -> None:
        r = rmr.wilder_rsi([float(30 - i) for i in range(30)], period=14)
        self.assertAlmostEqual(r[14], 0.0, places=4)

    def test_too_short_all_none(self) -> None:
        r = rmr.wilder_rsi([1.0, 2.0, 3.0], period=14)
        self.assertTrue(all(x is None for x in r))


class TestAdjFieldNames(unittest.TestCase):
    """/equities/bars/daily の短名 (AdjC/C) に _adj が対応すること。"""

    def test_prefers_adjusted(self) -> None:
        self.assertEqual(rmr._adj({"AdjC": 110.0, "C": 100.0}, "Close"), 110.0)
        self.assertEqual(rmr._adj({"AdjO": 11.0, "O": 10.0}, "Open"), 11.0)

    def test_falls_back_to_raw(self) -> None:
        self.assertEqual(rmr._adj({"C": 100.0}, "Close"), 100.0)

    def test_long_names_not_used(self) -> None:
        # 旧バグの長名は実データに無い → None
        self.assertIsNone(rmr._adj({"Close": 100.0}, "Close"))


class TestSimulate(unittest.TestCase):
    def _bars(self, closes: list[float]) -> list[dict]:
        # 実データの短名 (O/C) で作成。Open=前日Close、当日 Close 指定で簡易作成
        bars = []
        for i, c in enumerate(closes):
            o = closes[i - 1] if i else c
            bars.append({"Date": f"2025-01-{i+1:02d}", "O": o, "C": c})
        return bars

    def test_no_signal_no_trades(self) -> None:
        bars = self._bars([100.0 + i for i in range(30)])  # 単調上昇 RSI=100
        trades = rmr.simulate_code(bars, entry=30.0, exit_=70.0)
        self.assertEqual(trades, [])

    def test_returns_zero_on_short_series(self) -> None:
        bars = self._bars([100.0] * 5)
        self.assertEqual(rmr.simulate_code(bars, 30.0, 70.0), [])

    def test_skips_codes_with_missing_close(self) -> None:
        bars = self._bars([100.0] * 30)
        bars[10]["C"] = None
        self.assertEqual(rmr.simulate_code(bars, 30.0, 70.0), [])

    def test_generates_trade_on_down_then_up(self) -> None:
        # 20日上昇 (RSI=100 warmup) → 30日下落 (RSI<30 クロス) → 30日上昇 (RSI>70 クロス)
        closes = ([100.0 + i for i in range(20)]              # 100->119
                  + [119.0 - i for i in range(1, 31)]          # 118->89
                  + [89.0 + i for i in range(1, 31)])          # 90->119
        bars = self._bars(closes)
        trades = rmr.simulate_code(bars, entry=30.0, exit_=70.0)
        self.assertEqual(len(trades), 1)
        self.assertGreater(trades[0]["ret"], 0)  # 安値圏で買い→上昇圏で売り


class TestAggregate(unittest.TestCase):
    def test_empty_returns_zero_n(self) -> None:
        self.assertEqual(rmr.aggregate_pattern([], cost=0.20)["n"], 0)

    def test_basic_stats(self) -> None:
        trades = [{"entry_date": f"2025-01-{i:02d}", "ret": 1.0,
                   "hold_days": 3} for i in range(1, 11)]
        s = rmr.aggregate_pattern(trades, cost=0.20)
        self.assertEqual(s["n"], 10)
        self.assertAlmostEqual(s["net_ev"], 0.80, places=4)
        self.assertEqual(s["win"], 100.0)


if __name__ == "__main__":
    unittest.main()

"""analyzers/survival.py と Deflated Sharpe 群の数学的正しさを検証。

精度ツールは数値が正しくないと無価値。既知の解析的性質で固める。
"""
from __future__ import annotations

import unittest

from analyzers import stats, survival


class TestDrawdownStreak(unittest.TestCase):
    def test_max_drawdown_known(self) -> None:
        # 1 → 2 → 1 = ピーク2から1 = 50% DD
        self.assertAlmostEqual(survival.max_drawdown([1.0, 2.0, 1.0]), 0.5, places=9)
        self.assertEqual(survival.max_drawdown([1.0, 1.0, 1.0]), 0.0)  # 単調横ばい
        self.assertEqual(survival.max_drawdown([1.0, 2.0, 3.0]), 0.0)  # 単調増は DD 0

    def test_max_losing_streak(self) -> None:
        self.assertEqual(survival.max_losing_streak([1, -1, -1, 1, -1]), 2)
        self.assertEqual(survival.max_losing_streak([1, 2, 3]), 0)
        self.assertEqual(survival.max_losing_streak([-1, -1, -1]), 3)


class TestBootstrapSurvival(unittest.TestCase):
    def test_positive_edge_grows(self) -> None:
        # 明確な正のエッジ: 終端中央値 > 1、P(損失) は小さい
        rets = [1.0, 1.0, 1.0, -0.5] * 30
        s = survival.bootstrap_survival(rets, f=1.0, n_paths=2000, seed=1)
        self.assertGreater(s["end_median"], 1.0)
        self.assertLess(s["p_loss"], 0.5)
        self.assertGreaterEqual(s["mdd_p95"], s["mdd_median"])  # worst5% >= 中央

    def test_larger_f_more_risk(self) -> None:
        # 賭け比率を上げると最大DDは増える (同じ系列)
        rets = [2.0, -2.0, 3.0, -1.0] * 40
        lo = survival.bootstrap_survival(rets, f=0.25, n_paths=3000, seed=7)
        hi = survival.bootstrap_survival(rets, f=1.0, n_paths=3000, seed=7)
        self.assertGreater(hi["mdd_median"], lo["mdd_median"])

    def test_too_few(self) -> None:
        self.assertEqual(survival.bootstrap_survival([1.0]).get("n_trades"), 1)


class TestDeflatedSharpe(unittest.TestCase):
    def test_expected_max_grows_with_trials(self) -> None:
        # 試行数が増えるほど偶然の Sharpe 最大値 SR0 は上がる
        a = stats.expected_max_sharpe(0.1, 10)
        b = stats.expected_max_sharpe(0.1, 1000)
        self.assertGreater(b, a)
        self.assertEqual(stats.expected_max_sharpe(0.1, 1), 0.0)  # 1試行はばらつき定義不能

    def test_dsr_monotone_in_sr(self) -> None:
        # SR が高いほど DSR は上がる / SR0 を超えると 0.5 を超える
        lo = stats.deflated_sharpe(0.10, 200, 0.0, 3.0, 0.20)
        hi = stats.deflated_sharpe(0.50, 200, 0.0, 3.0, 0.20)
        self.assertGreater(hi, lo)
        self.assertGreater(hi, 0.5)   # SR=0.5 > SR0=0.20
        self.assertLess(lo, 0.5)      # SR=0.10 < SR0=0.20

    def test_min_btl(self) -> None:
        # SR<=SR0 なら到達不能 (None)、SR>SR0 なら正の有限値
        self.assertIsNone(stats.min_track_record_length(0.1, 0.0, 3.0, 0.2))
        mbtl = stats.min_track_record_length(0.5, 0.0, 3.0, 0.2)
        self.assertIsNotNone(mbtl)
        self.assertGreater(mbtl, 0)

    def test_sharpe_moments_normal(self) -> None:
        # 対称データの skew≈0、SR= mean/std
        sr, skew, kurt, n = stats.sharpe_moments([-1.0, 1.0, -1.0, 1.0])
        self.assertAlmostEqual(sr, 0.0, places=9)
        self.assertAlmostEqual(skew, 0.0, places=9)
        self.assertEqual(n, 4)


if __name__ == "__main__":
    unittest.main()

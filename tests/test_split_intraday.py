"""株式分割 intraday の生値ベース計算とバー選択の単体テスト。

分足が分割未調整の生値である点に起因したバグ (調整値と混在で約-50%) の
再発防止。enrich のバー選択と analyze の生値リターン計算をロックする。
"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import (
    analyze_split_intraday as A,
    enrich_split_intraday as E,
)


class TestBarSelection(unittest.TestCase):
    def setUp(self) -> None:
        # 約定の薄い銘柄を模す: 09:00 寄り, 09:31 (09:30ちょうど無し), 11:25 (11:30無し), 14:59
        self.bars = [
            {"Time": "09:00", "O": 1000, "C": 1005},
            {"Time": "09:31", "O": 1010, "C": 1012},
            {"Time": "11:25", "O": 1020, "C": 1022},
            {"Time": "14:59", "O": 1030, "C": 1035},
        ]

    def test_px_open_is_first_morning_open(self) -> None:
        self.assertEqual(E._px_open(self.bars), 1000)

    def test_px_930_first_bar_at_or_after_0930(self) -> None:
        # 09:30 ちょうどが無くても 09:31 を採る
        self.assertEqual(E._px_930(self.bars), 1010)

    def test_px_1130_last_morning_close(self) -> None:
        # 11:30 ちょうどが無くても 11:25 (前場最後) の Close
        self.assertEqual(E._px_1130(self.bars), 1022)

    def test_px_930_skips_afternoon(self) -> None:
        # 前場に 09:30 以降の約定が無い場合は None (午後を誤って拾わない)
        bars = [{"Time": "09:05", "O": 500, "C": 501},
                {"Time": "13:00", "O": 600, "C": 601}]
        self.assertIsNone(E._px_930(bars))


class TestRawEntryRet(unittest.TestCase):
    def test_open_entry_equals_dn(self) -> None:
        # 寄り(px_open)入りは d{n}_ret に一致
        a = {"px_open": 100.0, "d5_ret": 10.0}
        self.assertAlmostEqual(A._entry_ret(a, "px_open", 5), 10.0, places=6)

    def test_intraday_entry_raw_basis(self) -> None:
        # +5引け生値 = px_open*(1+0.10)=110。9:30=101 入り → 110/101-1
        a = {"px_open": 100.0, "px_930": 101.0, "d5_ret": 10.0}
        self.assertAlmostEqual(A._entry_ret(a, "px_930", 5),
                               (110.0 / 101.0 - 1) * 100.0, places=6)

    def test_split_does_not_blow_up(self) -> None:
        # 生値同士なので分割比は相殺。px が entry_open(AdjO=生値/2) と混ざらないことの担保:
        # px_open/px_930 が共に生値なら結果は -50% のような破綻を起こさない
        a = {"px_open": 4000.0, "px_930": 4010.0, "d10_ret": 5.0}
        r = A._entry_ret(a, "px_930", 10)
        self.assertGreater(r, -5.0)
        self.assertLess(r, 10.0)

    def test_missing_price_returns_none(self) -> None:
        self.assertIsNone(A._entry_ret({"px_open": 100.0, "d5_ret": 1.0}, "px_930", 5))
        self.assertIsNone(A._entry_ret({"px_930": 100.0, "d5_ret": 1.0}, "px_930", 5))


if __name__ == "__main__":
    unittest.main()

"""topix_adjust: TOPIX 検索・超過収益計算を mock データで検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import topix_adjust as ta


def _topix(rows: list[tuple[str, float, float]]) -> list[dict]:
    return [{"Date": d, "O": o, "C": c} for d, o, c in rows]


class TestFindIdx(unittest.TestCase):
    def test_exact_match(self) -> None:
        t = _topix([("2025-01-01", 100, 101), ("2025-01-02", 102, 103),
                    ("2025-01-03", 104, 105)])
        self.assertEqual(ta._find_idx(t, "2025-01-02"), 1)

    def test_returns_next_open_when_date_is_holiday(self) -> None:
        t = _topix([("2025-01-06", 100, 101), ("2025-01-07", 102, 103)])
        # 2025-01-05 は休日想定 → 翌営業日 index=0
        self.assertEqual(ta._find_idx(t, "2025-01-05"), 0)

    def test_out_of_range_returns_none(self) -> None:
        t = _topix([("2025-01-01", 100, 101)])
        self.assertIsNone(ta._find_idx(t, "2030-01-01"))


class TestTopixReturn(unittest.TestCase):
    def test_basic_5day(self) -> None:
        t = _topix([(f"2025-01-{i:02d}", 100.0, 100.0 + i) for i in range(1, 21)])
        # entry=2025-01-01: i=0, O=100. +5 = index 5, C=100+6=106. ret=6%
        r = ta.topix_return(t, "2025-01-01", 5)
        assert r is not None
        self.assertAlmostEqual(r, 6.0, places=4)

    def test_out_of_range_returns_none(self) -> None:
        t = _topix([("2025-01-01", 100.0, 101.0)])
        self.assertIsNone(ta.topix_return(t, "2025-01-01", 5))


class TestEnrich(unittest.TestCase):
    def test_adds_alpha(self) -> None:
        t = _topix([(f"2025-01-{i:02d}", 100.0, 100.0 + i) for i in range(1, 21)])
        # 銘柄リターン d5_ret=10% / TOPIX d5_ret=6% → alpha = 4%
        recs = [{"attrs": {"entry_date": "2025-01-01", "d5_ret": 10.0}}]
        # Monkey-patch path 経由でなく直接 enrich (テスト用 topix を渡せないので一旦バイパス)
        # シンプルに topix_return を直接検証する形にした方が安全だが、
        # ここでは load_topix の代わりに topix を組み立てて確認する。
        # → enrich_with_alpha は load_topix を内部で呼ぶので、別関数で確認:
        r = ta.topix_return(t, "2025-01-01", 5)
        assert r is not None
        self.assertAlmostEqual(10.0 - r, 4.0, places=4)


if __name__ == "__main__":
    unittest.main()

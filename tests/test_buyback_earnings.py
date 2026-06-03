"""extract_buyback_earnings の純関数 (同日抽出 / YoY算出) を mock で検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import extract_buyback_earnings as be


class TestSelectSameDay(unittest.TestCase):
    def _row(self, code, date, title, items, time="15:30"):
        return {"Code": code, "DiscDate": date, "DiscTime": time, "Title": title, "DiscItems": items}

    def test_buyback_and_kessan_same_day(self) -> None:
        rows = [
            self._row("100", "2025-04-28", "自己株式の取得に係る事項の決定に関するお知らせ", "11105"),
            self._row("100", "2025-04-28", "2025年3月期 決算短信〔IFRS〕(連結)", "11101"),
        ]
        out = be.select_buyback_with_earnings(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["code"], out[0]["event_date"]), ("100", "2025-04-28"))

    def test_buyback_without_kessan_excluded(self) -> None:
        rows = [self._row("100", "2025-04-28", "自己株式の取得に係る事項の決定に関するお知らせ", "11105")]
        self.assertEqual(be.select_buyback_with_earnings(rows), [])

    def test_requires_11105_disc_item(self) -> None:
        # タイトルは買戻し風だが DiscItems に 11105 が無い → 対象外
        rows = [self._row("100", "2025-04-28", "自己株式の取得に係る事項の決定", "99999"),
                self._row("100", "2025-04-28", "決算短信", "11101")]
        self.assertEqual(be.select_buyback_with_earnings(rows), [])


class TestComputeYoY(unittest.TestCase):
    def _s(self, disc, pt, end, np_, op=None, sales=None):
        r = {"DiscDate": disc, "CurPerType": pt, "CurPerEn": end, "NP": np_}
        if op is not None:
            r["OP"] = op
        if sales is not None:
            r["Sales"] = sales
        return r

    def test_np_yoy_minus_10(self) -> None:
        summary = [self._s("2025-04-30", "FY", "2025-03-31", 100.0),
                   self._s("2026-04-24", "FY", "2026-03-31", 90.0)]
        out = be.compute_yoy(summary, "2026-04-24")
        self.assertEqual(out["per_type"], "FY")
        self.assertAlmostEqual(out["np_yoy"], -10.0)

    def test_matches_period_type(self) -> None:
        # 1Q と FY が混在 → FY 同士で比較
        summary = [self._s("2025-08-01", "1Q", "2025-06-30", 50.0),
                   self._s("2025-04-30", "FY", "2025-03-31", 200.0),
                   self._s("2026-04-24", "FY", "2026-03-31", 220.0)]
        out = be.compute_yoy(summary, "2026-04-24")
        self.assertAlmostEqual(out["np_yoy"], 10.0)

    def test_no_prior_year_returns_no_yoy(self) -> None:
        summary = [self._s("2026-04-24", "FY", "2026-03-31", 90.0)]
        out = be.compute_yoy(summary, "2026-04-24")
        self.assertNotIn("np_yoy", out)
        self.assertEqual(out["per_type"], "FY")

    def test_disc_date_not_found(self) -> None:
        summary = [self._s("2026-04-24", "FY", "2026-03-31", 90.0)]
        self.assertEqual(be.compute_yoy(summary, "2099-01-01"), {})


if __name__ == "__main__":
    unittest.main()

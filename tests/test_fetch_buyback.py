"""fetch_buyback の純粋ロジックと (mock 経由の) API 関数を検証。実 API は叩かない。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import fetch_buyback as fb


class TestBuybackToRecord(unittest.TestCase):
    def test_code_normalization_and_date(self) -> None:
        rec = fb.buyback_to_record({"Code": "28010", "DisclosedDate": "2026-04-24",
                                    "PlannedShares": 24000000})
        self.assertEqual(rec["code"], "2801")          # 5桁→4桁
        self.assertEqual(rec["event_date"], "2026-04-24")
        self.assertEqual(rec["event_type"], "share_buyback")
        self.assertIn("buyback_raw", rec["attrs"])

    def test_missing_code_or_date_returns_none(self) -> None:
        self.assertIsNone(fb.buyback_to_record({"DisclosedDate": "2026-04-24"}))
        self.assertIsNone(fb.buyback_to_record({"Code": "28010"}))

    def test_size_field_extracted(self) -> None:
        rec = fb.buyback_to_record({"code": "7203", "Date": "2026-01-05", "MaxSharesToBuy": 1000})
        self.assertEqual(rec["attrs"]["buyback_MaxSharesToBuy"], 1000)


class TestFetchBuybackEvents(unittest.TestCase):
    def test_uses_pro_host_and_returns_rows(self) -> None:
        with patch.object(fb._jquants, "get_list",
                          return_value=[{"Code": "28010", "DisclosedDate": "2026-04-24"}]) as m:
            rows = fb.fetch_buyback_events("2026-01-01", "2026-12-31")
        self.assertEqual(len(rows), 1)
        self.assertEqual(m.call_args.kwargs.get("base"), fb._jquants.PRO_BASE_URL)


class TestAttachEarnings(unittest.TestCase):
    def test_forecast_decline_computed(self) -> None:
        summary = [{"DiscDate": "2026-04-24", "DocType": "FY", "NP": "61615000000",
                    "NxFNp": "61300000000", "ShOutFY": "969416010"}]
        rec = {"code": "2801", "event_date": "2026-04-24", "attrs": {}}
        with patch.object(fb._jquants, "get_list", return_value=summary):
            fb.attach_earnings(rec)
        self.assertAlmostEqual(rec["attrs"]["forecast_decline_pct"],
                               (61300000000 - 61615000000) / 61615000000 * 100, places=4)
        self.assertEqual(rec["attrs"]["np_actual"], 61615000000.0)

    def test_no_statement_on_or_before_event_sets_error(self) -> None:
        rec = {"code": "9999", "event_date": "2020-01-01", "attrs": {}}
        with patch.object(fb._jquants, "get_list",
                          return_value=[{"DiscDate": "2026-04-24", "NP": "1"}]):
            fb.attach_earnings(rec)
        self.assertIn("earnings_error", rec["attrs"])


if __name__ == "__main__":
    unittest.main()

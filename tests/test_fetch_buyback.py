"""fetch_buyback の純粋ロジックと (mock 経由の) API 関数を検証。実 API は叩かない。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import fetch_buyback as fb


class TestIsBuybackDecision(unittest.TestCase):
    def test_decision_matches(self) -> None:
        self.assertTrue(fb.is_buyback_decision(
            {"DiscItems": ["11105"], "Title": "自己株式の取得に係る事項の決定に関するお知らせ"}))

    def test_status_report_excluded(self) -> None:
        self.assertFalse(fb.is_buyback_decision(
            {"DiscItems": ["11105", "11402"], "Title": "自己株式の取得状況及び取得終了に関するお知らせ"}))

    def test_other_category_excluded(self) -> None:
        self.assertFalse(fb.is_buyback_decision(
            {"DiscItems": ["11381"], "Title": "2026年3月期 決算短信"}))


class TestBuybackToRecord(unittest.TestCase):
    def test_td_list_row_to_record(self) -> None:
        rec = fb.buyback_to_record({"Code": "28010", "DiscDate": "2026-04-24",
                                    "DiscNo": "20260423509282", "DiscItems": ["11105"],
                                    "Title": "自己株式の取得に係る事項の決定に関するお知らせ", "Docs": ["g"]})
        self.assertEqual(rec["code"], "2801")            # 5桁→4桁
        self.assertEqual(rec["event_date"], "2026-04-24")
        self.assertEqual(rec["event_type"], "share_buyback_decision")
        self.assertEqual(rec["attrs"]["disc_no"], "20260423509282")

    def test_missing_code_or_date_returns_none(self) -> None:
        self.assertIsNone(fb.buyback_to_record({"DiscDate": "2026-04-24"}))
        self.assertIsNone(fb.buyback_to_record({"Code": "28010"}))


class TestFetchBuybackEvents(unittest.TestCase):
    def test_filters_to_buyback_decisions(self) -> None:
        rows = [
            {"Code": "28010", "DiscDate": "2026-04-24", "DiscItems": ["11105"],
             "Title": "自己株式の取得に係る事項の決定に関するお知らせ"},
            {"Code": "12340", "DiscDate": "2026-04-24", "DiscItems": ["11381"],
             "Title": "決算短信"},  # 自社株買いでない → 除外
            {"Code": "56780", "DiscDate": "2026-04-24", "DiscItems": ["11105"],
             "Title": "自己株式の取得状況に関するお知らせ"},  # 状況報告 → 除外
        ]
        with patch.object(fb._jquants, "get_list", return_value=rows):
            out = fb.fetch_buyback_events("2026-04-24", "2026-04-24")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["Code"], "28010")


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

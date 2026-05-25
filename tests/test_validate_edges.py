"""validate_edges のソース別 observation 抽出と build_report を検証。"""
from __future__ import annotations

import unittest

from scripts import validate_edges


class TestObservationAdapters(unittest.TestCase):
    def test_kouaku_excludes_limit_lock(self) -> None:
        recs = [
            {"subpattern": "kouhou_genshu", "code": "7203", "event_date": "2026-01-05",
             "good_factors": [{"disc_time": "13:00:00"}], "bad_factors": [],
             "attrs": {"next_day_open_to_close_ret": 1.0}},
            {"subpattern": "kouhou_genshu", "code": "7203", "event_date": "2026-01-06",
             "good_factors": [{"disc_time": "13:00:00"}], "bad_factors": [],
             "attrs": {"next_day_open_to_close_ret": 2.0, "limit_locked": True}},
        ]
        obs = list(validate_edges.kouaku_observations(recs))
        self.assertEqual(len(obs), 1)  # limit_locked 除外
        self.assertEqual(obs[0]["ret"], 1.0)
        self.assertEqual(obs[0]["cell"][0], "kouhou_genshu")

    def test_po_eligible_and_stage_metric(self) -> None:
        recs = [
            {"stage": "decide", "po_type": "リート", "lending_type": "貸借",
             "code": "8951", "event_date": "2026-02-01", "status": "complete",
             "attrs": {"ret_close": -1.5}},
            {"stage": "decide", "po_type": "リート", "lending_type": "貸借",
             "code": "8952", "event_date": "2026-02-02", "status": "complete",
             "legacy_record": True, "attrs": {"ret_close": -9.9}},  # legacy → 除外
        ]
        obs = list(validate_edges.po_observations(recs))
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["ret"], -1.5)

    def test_holdings_excludes_suspect(self) -> None:
        recs = [
            {"purpose_category_jp": "純投資", "holder_category_jp": "外資ファンド",
             "code": "7203", "event_date": "2026-03-01", "attrs": {"next_day_open_to_close_ret": 0.5}},
            {"purpose_category_jp": "純投資", "holder_category_jp": "外資ファンド",
             "code": "7204", "event_date": "2026-03-02", "low_ratio_suspect": True,
             "attrs": {"next_day_open_to_close_ret": 9.9}},
        ]
        obs = list(validate_edges.holdings_observations(recs))
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["cell"], ("純投資", "外資ファンド"))


class TestBuildReport(unittest.TestCase):
    def test_runs_on_committed_data(self) -> None:
        md = validate_edges.build_report(cost_pct=0.2, alpha=0.05, split_frac=0.7, min_n=30)
        self.assertIn("エッジ検証", md)
        self.assertIn("FDR", md)
        # 3 ソースの見出しが出る
        for name in ("kouaku", "po", "holdings"):
            self.assertIn(f"## {name}", md)


if __name__ == "__main__":
    unittest.main()

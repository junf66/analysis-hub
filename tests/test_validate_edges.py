"""validate_edges のソース別 observation 抽出と build_report を検証。"""
from __future__ import annotations

import unittest

from scripts import validate_edges


class TestNewEdges(unittest.TestCase):
    def test_primary_mag_bad_first(self) -> None:
        r = {"bad_factors": [{"metric": {"NP_YoY_pct": -15.0}}],
             "good_factors": [{"metric": {"Div_revision_pct": 5.0}}]}
        self.assertEqual(validate_edges._primary_mag(r), -15.0)

    def test_primary_mag_none(self) -> None:
        self.assertIsNone(validate_edges._primary_mag({"bad_factors": [], "good_factors": []}))

    def test_new_edges_yields_valid_obs(self) -> None:
        # 実データ(kouaku_records 等)から事前登録セルを抽出。obs スキーマと既知セル名を確認。
        obs = list(validate_edges.new_edges_observations([]))
        self.assertTrue(obs)
        for o in obs:
            self.assertIn("cell", o)
            self.assertIsInstance(o["ret"], float)
        cells = {o["cell"] for o in obs}
        self.assertTrue(any("zouhai_kahou_nx" in c for c in cells))


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

    def test_po_named_gd_gap_filter_and_cells(self) -> None:
        recs = [
            # ① announce 普通 → next_day_910_ret
            {"stage": "announce", "po_type": "普通", "code": "1", "event_date": "2026-01-01",
             "status": "complete", "attrs": {"next_day_910_ret": 0.5}},
            # ② deliver 普通 GD: gap<=-0.5 は採用、gap>-0.5 は除外
            {"stage": "deliver", "po_type": "普通", "code": "2", "event_date": "2026-01-02",
             "status": "complete", "attrs": {"gap_pct": -0.8, "next_day_open_to_close_ret": 0.3}},
            {"stage": "deliver", "po_type": "普通", "code": "3", "event_date": "2026-01-03",
             "status": "complete", "attrs": {"gap_pct": 0.2, "next_day_open_to_close_ret": 9.9}},
            # ③ decide リート → ret_close
            {"stage": "decide", "po_type": "リート", "code": "4", "event_date": "2026-01-04",
             "status": "complete", "attrs": {"ret_close": -1.2}},
        ]
        obs = list(validate_edges.po_named_observations(recs))
        cells = {o["cell"].split(" ", 1)[0]: o["ret"] for o in obs}
        self.assertEqual(set(cells), {"①", "②", "③"})  # gap>-0.5 の受渡は除外
        self.assertEqual(cells["②"], 0.3)
        self.assertEqual(cells["③"], -1.2)

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
        md = validate_edges.build_report(long_cost=0.20, short_cost=0.15,
                                         alpha=0.05, split_frac=0.7, min_n=30)
        self.assertIn("エッジ検証", md)
        self.assertIn("FDR", md)
        # 3 ソースの見出し + 既知3エッジ監査セクションが出る
        for name in ("kouaku", "po", "holdings"):
            self.assertIn(f"## {name}", md)
        self.assertIn("既知3エッジ監査", md)


if __name__ == "__main__":
    unittest.main()

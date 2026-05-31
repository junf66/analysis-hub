"""run_candidate の悪材料フィルタ(#2)ロジックを検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import run_candidate as rc


class TestBadMaterialFilter(unittest.TestCase):
    def test_bad_material_keys(self) -> None:
        idx = [{"code": "1", "event_date": "2026-01-05", "tags": ["bad_tokuson"]},
               {"code": "2", "event_date": "2026-01-05", "tags": ["good_zouhai"]},
               {"code": "3", "event_date": "2026-01-06", "tags": ["good_jisha", "bad_daisansha"]}]
        keys = rc.bad_material_keys(idx)
        self.assertIn(("1", "2026-01-05"), keys)
        self.assertIn(("3", "2026-01-06"), keys)
        self.assertNotIn(("2", "2026-01-05"), keys)

    def test_filter_excludes_same_day_bad_and_decline(self) -> None:
        buyback = [
            {"code": "1", "event_date": "2026-01-05", "attrs": {"forecast_decline_pct": 2.0}},   # 増益→残す
            {"code": "1", "event_date": "2026-01-06", "attrs": {"forecast_decline_pct": -3.0}},  # 減益→除外
            {"code": "9", "event_date": "2026-01-07", "attrs": {}},                              # 材料なし→残す
            {"code": "5", "event_date": "2026-01-08", "attrs": {"forecast_decline_pct": 1.0}},   # 同日悪材料→除外
        ]
        bad = {("5", "2026-01-08")}
        out = rc.filter_no_bad_material(buyback, bad)
        codes = {(r["code"], r["event_date"]) for r in out}
        self.assertEqual(codes, {("1", "2026-01-05"), ("9", "2026-01-07")})


class TestLoadEnrichedByTag(unittest.TestCase):
    def test_filters_by_tag(self) -> None:
        # 実データが在ればフィルタが返す件数が good_kessan_up タグ数と一致
        import json
        if not rc.ENRICHED_PATH.exists():
            self.skipTest("enriched_events.json なし")
        recs = rc.load_enriched_by_tag({"good_kessan_up"})
        all_recs = json.loads(rc.ENRICHED_PATH.read_text())["records"]
        expected = sum(1 for r in all_recs if "good_kessan_up" in (r.get("tags") or []))
        self.assertEqual(len(recs), expected)


if __name__ == "__main__":
    unittest.main()

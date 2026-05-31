"""enrich_index_events.select_events のタグ抽出・集約を検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import enrich_index_events as ee


class TestSelectEvents(unittest.TestCase):
    def test_filters_by_tag_and_dedups(self) -> None:
        idx = [
            {"code": "1", "event_date": "2026-01-05", "tags": ["good_kessan_up"]},
            {"code": "1", "event_date": "2026-01-05", "tags": ["good_zouhai"]},  # 同code+date→集約
            {"code": "2", "event_date": "2026-01-06", "tags": ["bad_tokuson"]},  # 対象タグ無し→除外
            {"code": "3", "event_date": "2026-01-07", "tags": ["good_teikei"]},
        ]
        ev = ee.select_events(idx)
        keys = {(e["code"], e["event_date"]) for e in ev}
        self.assertEqual(keys, {("1", "2026-01-05"), ("3", "2026-01-07")})
        self.assertEqual(len(ev), 2)
        for e in ev:
            self.assertEqual(e["attrs"], {})

    def test_missing_code_or_date_skipped(self) -> None:
        idx = [{"code": "", "event_date": "2026-01-05", "tags": ["good_zouhai"]},
               {"code": "9", "event_date": None, "tags": ["good_zouhai"]}]
        self.assertEqual(ee.select_events(idx), [])


if __name__ == "__main__":
    unittest.main()

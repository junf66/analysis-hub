"""enrich_multiday の純粋ロジックを検証 (実 API は叩かない)。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.edge_candidates import enrich_multiday as em


class TestSelectSplit(unittest.TestCase):
    def test_filters_good_split_and_dedups(self) -> None:
        idx = [
            {"code": "1", "event_date": "2026-01-05", "tags": ["good_split"], "title": "株式分割A"},
            {"code": "1", "event_date": "2026-01-05", "tags": ["good_split"], "title": "重複"},
            {"code": "2", "event_date": "2026-01-06", "tags": ["good_zouhai"]},
        ]
        ev = em.select_split_events(idx)
        keys = {(e["code"], e["event_date"]) for e in ev}
        self.assertEqual(keys, {("1", "2026-01-05")})


class TestComputeMultiday(unittest.TestCase):
    def test_dN_ret_computed_from_bars(self) -> None:
        # event=01-05, entry=after[0]=01-06 O=100。+N日=after[N]の close を採用。
        # +1日=after[1]=01-07 C=102 → d1=+2%、+5日=after[5]=01-13 C=120 → d5=+20%
        bars = [{"Date": "2026-01-05", "O": 99, "C": 99},
                {"Date": "2026-01-06", "O": 100, "C": 101},
                {"Date": "2026-01-07", "O": 101, "C": 102},
                {"Date": "2026-01-08", "O": 102, "C": 103},
                {"Date": "2026-01-09", "O": 103, "C": 104},
                {"Date": "2026-01-12", "O": 104, "C": 110},
                {"Date": "2026-01-13", "O": 110, "C": 120}]
        rec = {"code": "1", "event_date": "2026-01-05", "attrs": {}}
        with patch.object(em._jquants, "get_list", return_value=bars):
            em.compute_multiday(rec)
        self.assertEqual(rec["attrs"]["entry_date"], "2026-01-06")
        self.assertEqual(rec["attrs"]["entry_open"], 100)
        self.assertAlmostEqual(rec["attrs"]["d1_ret"], 2.0, places=4)
        self.assertAlmostEqual(rec["attrs"]["d5_ret"], 20.0, places=4)


if __name__ == "__main__":
    unittest.main()

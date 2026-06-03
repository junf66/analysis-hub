"""analyze_po_scale_timing の純関数を検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import analyze_po_scale_timing as pst


class TestVerdict(unittest.TestCase):
    def test_pass(self):
        self.assertEqual(pst._verdict({"n": 50, "net_ev": 0.7, "t_clust": 2.3,
                                       "fdr_significant": True, "oos": 0.5}), "★通過")

    def test_raw_only(self):
        self.assertEqual(pst._verdict({"n": 50, "net_ev": 0.7, "t_clust": 2.3,
                                       "fdr_significant": False, "oos": 0.5}), "△(FDR前のみ)")

    def test_reject(self):
        self.assertEqual(pst._verdict({"n": 50, "net_ev": -0.3, "t_clust": -1.5,
                                       "fdr_significant": False, "oos": -0.2}), "✕")

    def test_small_n(self):
        self.assertEqual(pst._verdict({"n": 10, "net_ev": 1.0, "t_clust": 3.0,
                                       "fdr_significant": True, "oos": 1.0}), "—(n<30)")


class TestMerge(unittest.TestCase):
    def test_merge_attaches_attrs(self):
        po = [{"id": "x1", "stage": "announce", "po_type": "普通", "event_date": "2025-01-01",
               "attrs": {}},
              {"id": "y1", "stage": "decide", "po_type": "普通", "attrs": {}}]
        enr = {"x1": {"next_day_open_to_close_ret": 1.2, "scale_band": "大型"}}
        out = pst.merge(po, enr)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["scale_band"], "大型")
        self.assertEqual(out[0]["attrs"]["next_day_open_to_close_ret"], 1.2)


if __name__ == "__main__":
    unittest.main()

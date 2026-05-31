"""edge_candidates.candidates の設定構造を検証。"""
from __future__ import annotations

import unittest

from scripts.edge_candidates import candidates as cfg


class TestCandidatesConfig(unittest.TestCase):
    def test_eight_candidates_with_required_keys(self) -> None:
        ids = [c["cid"] for c in cfg.CANDIDATES]
        self.assertEqual(ids, ["#1", "#2", "#3", "#4", "#5", "#7", "#8", "#9"])
        for c in cfg.CANDIDATES:
            self.assertIn("source", c)
            self.assertIn("exits", c)
            self.assertIn("caveat_beta", c)
            self.assertTrue(c["exits"])  # 出口が空でない

    def test_multiday_candidates_flagged_beta(self) -> None:
        # 数日保有(#4/#7/#8)は caveat_beta=True
        for cid in ("#4", "#7", "#8"):
            self.assertTrue(cfg.by_id(cid)["caveat_beta"], cid)
        # 日計り(#1/#2/#9)は False
        for cid in ("#1", "#2", "#9"):
            self.assertFalse(cfg.by_id(cid)["caveat_beta"], cid)

    def test_buyback_reuse_excludes_bad(self) -> None:
        self.assertEqual(cfg.by_id("#2")["source"], "buyback_reuse")
        self.assertTrue(cfg.by_id("#2")["exclude_bad"])

    def test_by_id_missing_raises(self) -> None:
        with self.assertRaises(KeyError):
            cfg.by_id("#99")


if __name__ == "__main__":
    unittest.main()

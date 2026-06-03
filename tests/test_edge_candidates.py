"""edge_candidates.lib の検証エンジンとレポート出力を検証。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.edge_candidates import lib


def _rec(date, ret, code="7203", metric="next_day_910_ret"):
    return {"code": code, "event_date": date, "attrs": {metric: ret}}


class TestExitStatsAndValidate(unittest.TestCase):
    def test_limit_lock_and_none_excluded(self) -> None:
        recs = [_rec("2026-01-05", 1.0), _rec("2026-01-06", 2.0),
                {"code": "1", "event_date": "2026-01-07", "attrs": {"next_day_910_ret": 9.0, "limit_locked": True}},
                {"code": "2", "event_date": "2026-01-08", "attrs": {}}]
        res = lib.validate_candidate(recs, exits=[("next_day_910_ret", "9:10")])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["n"], 2)  # limit_lock と None 除外
        self.assertIn("fdr_significant", res[0])

    def test_net_ev_subtracts_cost(self) -> None:
        recs = [_rec(f"2026-01-{i:02d}", 1.0) for i in range(1, 11)]
        res = lib.validate_candidate(recs, exits=[("next_day_910_ret", "9:10")], cost=0.20)
        self.assertAlmostEqual(res[0]["net_ev"], 1.0 - 0.20, places=6)


class TestJudge(unittest.TestCase):
    def test_pass(self) -> None:
        r = [{"exit": "9:10", "n": 50, "net_ev": 0.8, "t_clust": 3.0, "win": 65,
              "p": 0.001, "oos": 0.6, "fdr_significant": True}]
        v, _, _ = lib.judge(r)
        self.assertEqual(v, "通過")

    def test_pass_but_beta_caveat_becomes_hold(self) -> None:
        r = [{"exit": "+5日", "n": 50, "net_ev": 0.8, "t_clust": 3.0, "win": 65,
              "p": 0.001, "oos": 0.6, "fdr_significant": True}]
        v, _, _ = lib.judge(r, caveat_beta=True)
        self.assertEqual(v, "保留")

    def test_reject_coinflip(self) -> None:
        r = [{"exit": "9:10", "n": 200, "net_ev": -0.05, "t_clust": 0.3, "win": 50,
              "p": 0.7, "oos": -0.1, "fdr_significant": False}]
        v, _, _ = lib.judge(r)
        self.assertEqual(v, "却下")

    def test_hold_small_n(self) -> None:
        r = [{"exit": "9:10", "n": 12, "net_ev": 0.9, "t_clust": 2.5, "win": 70,
              "p": 0.01, "oos": 0.5, "fdr_significant": True}]
        v, reason, _ = lib.judge(r)
        self.assertEqual(v, "保留")
        self.assertIn("n=12", reason)

    def test_empty(self) -> None:
        self.assertEqual(lib.judge([])[0], "却下")


class TestReports(unittest.TestCase):
    def test_writes_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            res = [{"exit": "9:10", "n": 50, "net_ev": 0.8, "t_clust": 3.0, "win": 65,
                    "p": 0.001, "oos": 0.6, "fdr_significant": True}]
            p = lib.write_candidate_report("#1", "テスト", res, "通過", "ok", out_dir=d)
            self.assertTrue(p.exists())
            self.assertIn("通過", p.read_text())
            s = lib.write_summary([{"cid": "#1", "name": "テスト", "verdict": "通過", "reason": "ok"}],
                                  out_path=d / "summary.md", data_period="2024-2026")
            self.assertTrue(s.exists())
            self.assertIn("通過した候補", s.read_text())


if __name__ == "__main__":
    unittest.main()

"""edge_candidates.fetch_tdnet_index の分類・変換と (mock経由の) 走査を検証。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.edge_candidates import fetch_tdnet_index as ti


class TestClassify(unittest.TestCase):
    def test_good_and_bad_tags(self) -> None:
        self.assertEqual(ti.classify_title("通期業績予想の上方修正に関するお知らせ"), ["good_kessan_up"])
        self.assertEqual(ti.classify_title("特別損失の計上に関するお知らせ"), ["bad_tokuson"])
        self.assertEqual(ti.classify_title("役員の異動に関するお知らせ"), [])

    def test_to_record_filters_and_normalizes_code(self) -> None:
        rec = ti.to_record({"Code": "28010", "DiscDate": "2026-04-24",
                            "Title": "配当予想の修正（増配）に関するお知らせ", "DiscNo": "x"})
        self.assertEqual(rec["code"], "2801")
        self.assertIn("good_div_rev", rec["tags"])
        self.assertIn("good_zouhai", rec["tags"])
        self.assertIsNone(ti.to_record({"Code": "1", "DiscDate": "2026-01-01", "Title": "決算説明会"}))


class TestFetchIndex(unittest.TestCase):
    def test_scan_keeps_relevant_and_checkpoints(self) -> None:
        rows = [{"Code": "70010", "DiscDate": "2026-01-05", "Title": "業績予想の上方修正", "DiscNo": "a"},
                {"Code": "70020", "DiscDate": "2026-01-05", "Title": "決算説明資料", "DiscNo": "b"}]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "idx.json"
            with patch.object(ti._jquants, "get_list", return_value=rows):
                recs = ti.fetch_index("2026-01-05", "2026-01-05", out_path=out)
            self.assertEqual(len(recs), 1)  # 上方修正のみ採用、決算説明は除外
            self.assertTrue(out.exists())

    def test_resume_from_last_date(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "idx.json"
            from scripts._atomic import atomic_write_json
            atomic_write_json(out, {"records": [{"code": "9999", "event_date": "2026-01-04"}],
                                    "count": 1, "last_date": "2026-01-05"}, indent=0)
            with patch.object(ti._jquants, "get_list", return_value=[]) as m:
                ti.fetch_index("2026-01-01", "2026-01-06", out_path=out)
            # last_date=2026-01-05 の翌日(01-06)のみ走査=1回
            self.assertEqual(m.call_count, 1)


if __name__ == "__main__":
    unittest.main()

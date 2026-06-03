"""fetch_margin_interest と fetch_short_sale_report の resume/走査ロジックを検証。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts._atomic import atomic_write_json
from scripts.edge_candidates import fetch_margin_interest as fm
from scripts.edge_candidates import fetch_short_sale_report as fs


class TestFetchMargin(unittest.TestCase):
    def test_scans_days_and_skips_empty(self) -> None:
        # 2日分: 1日目0件、2日目2件
        def side(*a, date=None, **kw):
            return [{"Date": date, "Code": "1", "LongVol": 10},
                    {"Date": date, "Code": "2", "LongVol": 20}] if date == "2026-01-06" else []
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "m.json"
            with patch.object(fm._jquants, "get_list", side_effect=side):
                recs = fm.fetch_margin("2026-01-05", "2026-01-06", out_path=out)
            self.assertEqual(len(recs), 2)

    def test_resume_from_last_date(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "m.json"
            atomic_write_json(out, {"records": [{"Date": "2026-01-04", "Code": "9"}],
                                    "count": 1, "last_date": "2026-01-05"}, indent=0)
            with patch.object(fm._jquants, "get_list", return_value=[]) as m:
                fm.fetch_margin("2026-01-01", "2026-01-07", out_path=out)
            # last_date=01-05 → 01-06,01-07 の2日のみ走査
            self.assertEqual(m.call_count, 2)


class TestFetchShortSale(unittest.TestCase):
    def test_uses_disc_date_param(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "s.json"
            with patch.object(fs._jquants, "get_list", return_value=[]) as m:
                fs.fetch_short_sale("2026-05-22", "2026-05-22", out_path=out)
            _, kw = m.call_args
            self.assertEqual(kw.get("disc_date"), "2026-05-22")


if __name__ == "__main__":
    unittest.main()

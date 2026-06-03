"""fetch_topix.fetch_topix の挙動を mock で検証 (実 API は叩かない)。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.edge_candidates import fetch_topix as ft


class TestFetchTopix(unittest.TestCase):
    def test_sorts_by_date(self) -> None:
        rows = [{"Date": "2026-05-25", "C": 3800},
                {"Date": "2026-05-20", "C": 3791}]
        with patch.object(ft._jquants, "get_list", return_value=rows) as m:
            out = ft.fetch_topix("2026-05-20", "2026-05-25")
        self.assertEqual([r["Date"] for r in out], ["2026-05-20", "2026-05-25"])
        _, kw = m.call_args
        self.assertEqual(kw.get("from"), "2026-05-20")
        self.assertEqual(kw.get("to"), "2026-05-25")


if __name__ == "__main__":
    unittest.main()

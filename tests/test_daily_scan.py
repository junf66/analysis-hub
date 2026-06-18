"""引け後スキャナ (scripts.daily_scan) の純関数テスト。

ネットワーク(scan_10R の daily bars 取得)は触らず、ファイル読取系(scan_zouhai/
scan_po_announce)・整形(build_body)・日付(jst_today)を一時ファイルで検証する。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import daily_scan


class DailyScanTest(unittest.TestCase):
    def test_jst_today_is_iso_date(self) -> None:
        self.assertRegex(daily_scan.jst_today(), r"^\d{4}-\d{2}-\d{2}$")

    def test_scan_zouhai_filters_subpattern_and_after_close(self) -> None:
        recs = {"records": [
            # 該当: zouhai_kahou_nx かつ 大引け後(15:30+)
            {"code": "1111", "name": "該当社", "event_date": "2026-06-10",
             "subpattern": "zouhai_kahou_nx",
             "bad_factors": [{"disc_time": "15:40"}], "good_factors": []},
            # 除外: 場中開示
            {"code": "2222", "name": "場中社", "event_date": "2026-06-10",
             "subpattern": "zouhai_kahou_nx",
             "bad_factors": [{"disc_time": "11:00"}], "good_factors": []},
            # 除外: 別サブパターン
            {"code": "3333", "name": "別社", "event_date": "2026-06-10",
             "subpattern": "kouhou_seikyu",
             "bad_factors": [{"disc_time": "15:40"}], "good_factors": []},
            # 除外: 別日
            {"code": "4444", "name": "別日社", "event_date": "2026-06-09",
             "subpattern": "zouhai_kahou_nx",
             "bad_factors": [{"disc_time": "15:40"}], "good_factors": []},
        ]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kouaku.json"
            p.write_text(json.dumps(recs))
            orig = daily_scan.KOUAKU_PATH
            daily_scan.KOUAKU_PATH = p
            try:
                out = daily_scan.scan_zouhai("2026-06-10")
            finally:
                daily_scan.KOUAKU_PATH = orig
        self.assertEqual([r["code"] for r in out], ["1111"])

    def test_scan_po_announce_only_chugata_futsu(self) -> None:
        po = {"records": [
            {"code": "1111", "name": "中型社", "stage": "announce",
             "po_type": "普通", "event_date": "2026-06-10"},
            {"code": "5555", "name": "小型社", "stage": "announce",
             "po_type": "普通", "event_date": "2026-06-10"},
            {"code": "1111", "name": "中型社", "stage": "decide",
             "po_type": "普通", "event_date": "2026-06-10"},   # decideは対象外
        ]}
        master = {"11110": {"Code": "11110", "CoName": "中型社", "scale_band": "中型"},
                  "55550": {"Code": "55550", "CoName": "小型社", "scale_band": "小型"}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "po.json"
            p.write_text(json.dumps(po))
            orig = daily_scan.PO_PATH
            daily_scan.PO_PATH = p
            try:
                out = daily_scan.scan_po_announce(master, "2026-06-10")
            finally:
                daily_scan.PO_PATH = orig
        self.assertEqual([r["code"] for r in out], ["1111"])

    def test_build_body_counts_and_breadth_tier(self) -> None:
        r10 = ([{"code": "6227", "name": "テスト", "close": 8000, "mkt": "グロース",
                 "sh_close": True, "banned": False, "tier": "core"},
                {"code": "6997", "name": "プ社", "close": 5240, "mkt": "プライム",
                 "sh_close": True, "banned": False, "tier": "prime"}], 20,
               "過熱=薄く/見送り(+1.70%/勝52%・非有意)")
        z4 = [{"code": "1111", "name": "増配社"}]
        b1: list = []
        body, n = daily_scan.build_body("2026-06-15", r10, z4, b1)
        self.assertEqual(n, 3)
        self.assertIn("候補 **3件**", body)
        self.assertIn("6227", body)
        self.assertIn("6997", body)        # プライム小型も別枠に表示
        self.assertIn("別枠", body)         # プライム小型の見出し
        self.assertIn("過熱日", body)   # breadth>15 の警告が出る

    def test_scan_10R_is_callable(self) -> None:
        # ネットワーク依存ゆえ呼び出しはしないが、シンボルの存在を確認。
        self.assertTrue(callable(daily_scan.scan_10R))


if __name__ == "__main__":
    unittest.main()

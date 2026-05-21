"""extract → enrich → query パイプラインの薄い E2E ロック。

外部 API は叩かない。Fixture と一時ファイルで完結。

実行:
  python -m unittest tests.test_pipeline_integration -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.extract_mixed_disclosures import (
    _merge_existing_attrs,
    aggregate_mixed,
    classify_all,
    decide_subpattern,
)
from scripts.enrich_price_kouaku import _already_enriched
from scripts import query_kouaku


def _buyback(code: str, date_: str, title: str = "自己株式の取得") -> dict:
    return {"Code": f"{code}0", "DiscDate": date_, "Title": title, "DiscNo": f"DUMMY_{code}_{date_}"}


def _fins(code: str, date_: str, doctype: str, **fields) -> dict:
    base = {"Code": f"{code}0", "DiscDate": date_, "DocType": doctype, "DiscNo": f"FIN_{code}_{date_}"}
    base.update(fields)
    return base


class TestNewSubpatterns(unittest.TestCase):
    """Phase 1.5 で追加した kouhou_muhai / kouhou_genhai の決定ロジック。"""

    def test_kouhou_muhai(self) -> None:
        self.assertEqual(decide_subpattern({"kouhou"}, {"muhai"}), "kouhou_muhai")

    def test_kouhou_genhai(self) -> None:
        self.assertEqual(decide_subpattern({"kouhou"}, {"genhai"}), "kouhou_genhai")

    def test_kouhou_kahou_takes_priority_over_muhai_when_both_present(self) -> None:
        # 上方+下方が両方ある場合は kouhou_kahou が先 (ルール定義順による)
        self.assertEqual(decide_subpattern({"kouhou"}, {"kahou", "muhai"}), "kouhou_kahou")


class TestExtractPreservesAttrs(unittest.TestCase):
    """extract 再実行で既存 attrs (価格 enrich) が消えないこと。"""

    def test_merge_existing_attrs_carries_price_data(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "kouaku_records.json"
            # 既存ファイル: 1 件、price enrich 済
            path.write_text(json.dumps({
                "schema_version": 1,
                "records": [{
                    "id": "kouaku:7990:2025-08-12",
                    "code": "7990",
                    "event_date": "2025-08-12",
                    "attrs": {"next_open": 1234.0, "gap_pct": 1.5, "next_day_910_ret": -0.5},
                }],
            }))
            # 新規 records (attrs 空)
            new_recs = [{
                "id": "kouaku:7990:2025-08-12",
                "code": "7990",
                "event_date": "2025-08-12",
                "attrs": {},
            }]
            carried = _merge_existing_attrs(new_recs, path)
            self.assertEqual(carried, 1)
            self.assertEqual(new_recs[0]["attrs"]["next_open"], 1234.0)
            self.assertEqual(new_recs[0]["attrs"]["gap_pct"], 1.5)
            self.assertEqual(new_recs[0]["attrs"]["next_day_910_ret"], -0.5)

    def test_merge_skips_new_records_without_match(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "kouaku_records.json"
            path.write_text(json.dumps({"records": [{"id": "kouaku:1234:2024-01-01", "attrs": {"next_open": 100}}]}))
            new_recs = [{"id": "kouaku:9999:2025-12-31", "attrs": {}}]
            carried = _merge_existing_attrs(new_recs, path)
            self.assertEqual(carried, 0)
            self.assertEqual(new_recs[0]["attrs"], {})

    def test_merge_no_existing_file(self) -> None:
        carried = _merge_existing_attrs([{"id": "foo", "attrs": {}}], Path("/nonexistent/path.json"))
        self.assertEqual(carried, 0)


class TestEnrichIdempotent(unittest.TestCase):
    def test_already_enriched_skips_with_next_open(self) -> None:
        self.assertTrue(_already_enriched({"attrs": {"next_open": 1500.0}}))

    def test_already_enriched_skips_with_price_error(self) -> None:
        self.assertTrue(_already_enriched({"attrs": {"price_error": "no bars"}}))

    def test_already_enriched_false_when_empty(self) -> None:
        self.assertFalse(_already_enriched({"attrs": {}}))
        self.assertFalse(_already_enriched({}))


class TestQueryFilter(unittest.TestCase):
    """query_kouaku のフィルタロジック (外部 API なし)。"""

    def _records(self) -> list[dict]:
        return [
            {
                "id": "kouaku:7203:2025-01-22",
                "code": "7203",
                "event_date": "2025-01-22",
                "subpattern": "kouhou_genshu",
                "good_factors": [{"disc_time": "15:30:00"}],
                "bad_factors": [{"disc_time": "15:30:00"}],
                "attrs": {"next_day_open_to_close_ret": -1.5, "gap_pct": 0.3, "limit_locked": False},
            },
            {
                "id": "kouaku:4502:2025-02-13",
                "code": "4502",
                "event_date": "2025-02-13",
                "subpattern": "kouhou_kahou",
                "good_factors": [{"disc_time": "13:00:00"}],
                "bad_factors": [{"disc_time": "13:00:00"}],
                "attrs": {"next_day_open_to_close_ret": -2.0, "gap_pct": -3.0, "limit_locked": False},
            },
            {
                "id": "kouaku:6670:2026-02-05",
                "code": "6670",
                "event_date": "2026-02-05",
                "subpattern": "kouhou_muhai",
                "good_factors": [{"disc_time": "15:30:00"}],
                "bad_factors": [{"disc_time": "15:30:00"}],
                "attrs": {"next_day_open_to_close_ret": 0.0, "gap_pct": 26.0, "limit_locked": True},
            },
        ]

    def _make_args(self, **kw) -> object:
        defaults = dict(
            subpattern=None, disc_time_bucket=None, year=None, since=None, until=None,
            code=None, gap_min=None, gap_max=None, exclude_locked=True, include_locked=False,
        )
        defaults.update(kw)
        ns = type("NS", (), defaults)()
        return ns

    def test_excludes_locked_by_default(self) -> None:
        filtered = query_kouaku._filter(self._records(), self._make_args())
        self.assertEqual([r["code"] for r in filtered], ["7203", "4502"])

    def test_include_locked(self) -> None:
        filtered = query_kouaku._filter(self._records(), self._make_args(exclude_locked=False))
        self.assertEqual(len(filtered), 3)

    def test_filter_by_subpattern(self) -> None:
        filtered = query_kouaku._filter(self._records(), self._make_args(subpattern="kouhou_kahou"))
        self.assertEqual([r["code"] for r in filtered], ["4502"])

    def test_filter_by_year(self) -> None:
        filtered = query_kouaku._filter(self._records(), self._make_args(year=2026, exclude_locked=False))
        self.assertEqual([r["code"] for r in filtered], ["6670"])

    def test_filter_by_disc_time_bucket(self) -> None:
        filtered = query_kouaku._filter(self._records(), self._make_args(disc_time_bucket="場中"))
        self.assertEqual([r["code"] for r in filtered], ["4502"])

    def test_filter_by_gap_range(self) -> None:
        filtered = query_kouaku._filter(self._records(), self._make_args(gap_min=-1.0, gap_max=1.0))
        self.assertEqual([r["code"] for r in filtered], ["7203"])

    def test_disc_bucket_thresholds(self) -> None:
        # 15:29 → 引け間際
        rec = {"good_factors": [{"disc_time": "15:29:00"}], "bad_factors": []}
        self.assertEqual(query_kouaku._disc_bucket(rec), "引け間際")
        # 15:30 → 大引け後
        rec = {"good_factors": [{"disc_time": "15:30:00"}], "bad_factors": []}
        self.assertEqual(query_kouaku._disc_bucket(rec), "大引け後")
        # 11:00 → 場中 (時刻が 11 台になったら場中扱い)
        rec = {"good_factors": [{"disc_time": "11:00:00"}], "bad_factors": []}
        self.assertEqual(query_kouaku._disc_bucket(rec), "場中")


class TestExtractAggregateNoPriorYearProducesGoodKouhou(unittest.TestCase):
    """履歴に前年比較対象がない決算短信は good/kouhou と分類されないこと
    (誤って 'good' になってペアを作ると spurious record になる)。"""

    def test_kessan_without_prior_is_neutral(self) -> None:
        # 1 件だけ (前年同期なし) → polarity=neutral, hint=kessan
        fins = [
            _fins("1234", "2025-08-12", "1QFinancialStatements_Consolidated_JP",
                  CurPerType="1Q", CurPerSt="2025-04-01", CurPerEn="2025-06-30",
                  CurFYSt="2025-04-01", CurFYEn="2026-03-31", NP="1000000000"),
        ]
        classified = classify_all([], fins)
        # neutral でも kessan hint があるので mixed には絡まない (good_factor 不在)
        records = aggregate_mixed(classified)
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

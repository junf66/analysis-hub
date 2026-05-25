"""未カバーだった公開関数を最小限ずつ exercise する API カバレッジテスト。

実 API は叩かない。各関数を「呼べる/期待通り構造を返す」レベルで触る。
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


# ---- analyze_kouaku_edge ----------------------------------------------------

class TestAnalyzeBuildReport(unittest.TestCase):
    def setUp(self) -> None:
        from scripts import analyze_kouaku_edge as m
        self.m = m
        self.payload = {
            "records": [
                {
                    "id": "kouaku:7203:2025-01-22",
                    "code": "7203", "event_date": "2025-01-22",
                    "subpattern": "kouhou_genshu",
                    "good_factors": [{"disc_time": "15:30:00", "subpattern_hint": "kouhou"}],
                    "bad_factors": [{"disc_time": "15:30:00", "subpattern_hint": "genshu"}],
                    "attrs": {"gap_pct": 0.5, "next_day_open_to_close_ret": -1.5, "next_day_915_ret": -1.0},
                },
                {
                    "id": "kouaku:4502:2025-02-13",
                    "code": "4502", "event_date": "2025-02-13",
                    "subpattern": "kouhou_kahou",
                    "good_factors": [{"disc_time": "13:00:00", "subpattern_hint": "kouhou"}],
                    "bad_factors": [{"disc_time": "13:00:00", "subpattern_hint": "kahou"}],
                    "attrs": {"gap_pct": -3.0, "next_day_open_to_close_ret": -2.0, "limit_locked": False},
                },
            ]
        }

    def test_build_main_report_starts_with_heading(self) -> None:
        out = self.m.build_main_report(self.payload)
        self.assertTrue(out.startswith("# "), out[:30])
        self.assertIn("kouhou_genshu", out)
        self.assertIn("DiscTime", out)

    def test_build_sub_report_contains_subpattern(self) -> None:
        out = self.m.build_sub_report("kouhou_genshu", self.payload["records"][:1])
        self.assertIn("kouhou_genshu", out)
        self.assertIn("n_records", out)


# ---- backtest_kouaku --------------------------------------------------------

class TestBacktestBuildReport(unittest.TestCase):
    def test_build_report_handles_empty(self) -> None:
        from scripts.backtest_kouaku import build_report
        out = build_report([], cost_pct=0.2)
        self.assertIn("# kouaku_mixed バックテスト", out)
        self.assertIn("0.20%", out)

    def test_build_report_with_records(self) -> None:
        from scripts.backtest_kouaku import build_report
        recs = [
            {
                "subpattern": "kouhou_genshu",
                "good_factors": [{"disc_time": "15:30:00"}],
                "bad_factors": [{"disc_time": "15:30:00"}],
                "event_date": "2025-01-22",
                "attrs": {"next_day_open_to_close_ret": -1.0, "limit_locked": False},
            }
        ] * 5  # n=5 でセル収録される
        out = build_report(recs, cost_pct=0.2)
        self.assertIn("kouhou_genshu", out)


# ---- classify_kouaku --------------------------------------------------------

class TestClassifyFinsRecord(unittest.TestCase):
    def test_earn_forecast_returns_neutral_kouhou_or_kahou(self) -> None:
        from scripts.classify_kouaku import classify_fins_record
        cd = classify_fins_record({
            "Code": "72030", "DiscDate": "2025-01-22", "DocType": "EarnForecastRevision",
        })
        self.assertEqual(cd.code, "7203")
        self.assertEqual(cd.polarity, "neutral")
        self.assertEqual(cd.subpattern_hint, "kouhou_or_kahou")

    def test_financial_statements_returns_kessan(self) -> None:
        from scripts.classify_kouaku import classify_fins_record
        cd = classify_fins_record({
            "Code": "72030", "DiscDate": "2025-01-22",
            "DocType": "FYFinancialStatements_Consolidated_JP",
        })
        self.assertEqual(cd.subpattern_hint, "kessan")


class TestClassifyFinsBatch(unittest.TestCase):
    def test_returns_list_of_classified(self) -> None:
        from scripts.classify_kouaku import classify_fins
        rows = [{"Code": "72030", "DiscDate": "2025-01-22", "DocType": "EarnForecastRevision"}]
        out = classify_fins(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].code, "7203")


# ---- data_health ------------------------------------------------------------

class TestDataHealthChecks(unittest.TestCase):
    """data_health は 250MB+1GB JSON を実読みするので fixture 化して高速に。"""

    def setUp(self) -> None:
        from scripts import data_health as m
        self.m = m
        self._td = tempfile.TemporaryDirectory()
        td = Path(self._td.name)
        # 小さい fixture
        (td / "data").mkdir()
        (td / "data" / "records.json").write_text(json.dumps({
            "schema_version": 1, "subpattern_counts": {"kouhou_genshu": 1},
            "records": [{
                "id": "kouaku:7203:2025-01-22", "code": "7203",
                "event_date": "2025-01-22", "subpattern": "kouhou_genshu",
                "good_factors": [], "bad_factors": [],
                "attrs": {"next_open": 100.0, "limit_locked": False},
            }],
        }))
        (td / "cache").mkdir()
        (td / "cache" / "fins.json").write_text(json.dumps({
            "by_date": {"2025-01-22": [{"Code": "72030", "DocType": "EarnForecastRevision",
                                          "CurFYSt": "2025-04-01", "DiscDate": "2025-01-22"}]}
        }))
        (td / "cache" / "buyback.json").write_text(json.dumps({"by_date": {}}))
        (td / "cache" / "bars.json").write_text(json.dumps({"7203": [{"Date": "2025-01-22"}]}))
        (td / "cache" / "tdnet.json").write_text(json.dumps({
            "source": "yanoshin:tdnet",
            "by_date": {"2025-01-22": [{"code": "7203", "title": "自己株式の取得"}]},
            "record_count": 1,
        }))
        (td / "data" / "po.json").write_text(json.dumps({
            "schema_version": "po.v1",
            "source": "po-tracker",
            "count_raw": 1,
            "count": 1,
            "stage_counts": {"announce": 1},
            "type_counts": {"普通": 1},
            "raw_last_updated": "2024-01-01T00:00:00+00:00",
            "records": [{
                "id": "po:test_1:announce",
                "code": "7203",
                "event_date": "2024-08-01",
                "event_type": "po_announce",
                "source": "po-tracker",
                "ref_id": "test_1",
                "stage": "announce",
                "po_type": "普通",
                "lending_type": "貸借",
                "legacy_record": False,
                "concurrent_earnings": False,
                "split_within_po_window": False,
                "stale_incomplete": False,
                "status": "complete",
                "attrs": {"next_open": 100.0, "prev_close": 110.0, "next_day_910_ret": 0.5},
            }],
        }))

        (td / "data" / "holdings.json").write_text(json.dumps({
            "schema_version": "holdings.v1",
            "source": "edinet",
            "count_raw": 1,
            "count": 1,
            "purpose_counts": {"純投資": 1},
            "holder_counts": {"外資ファンド": 1},
            "raw_last_updated": "2026-01-01T00:00:00Z",
            "records": [{
                "id": "holdings:7203_20260101_X",
                "code": "7203",
                "event_date": "2026-01-01",
                "event_type": "holdings_filing",
                "source": "edinet",
                "ref_id": "7203_20260101_X",
                "purpose_category_jp": "純投資",
                "holder_category_jp": "外資ファンド",
                "low_ratio_suspect": False,
                "attrs": {"next_open": 100.0, "next_day_open_to_close_ret": 0.5},
            }],
        }))

        # path swap
        self._orig = (
            m.RECORDS_PATH, m.FINS_PATH, m.BUYBACK_PATH, m.BARS_PATH,
            m.TDNET_PATH, m.PO_RECORDS_PATH, m.HOLDINGS_RECORDS_PATH,
        )
        m.RECORDS_PATH = td / "data" / "records.json"
        m.FINS_PATH = td / "cache" / "fins.json"
        m.BUYBACK_PATH = td / "cache" / "buyback.json"
        m.BARS_PATH = td / "cache" / "bars.json"
        m.TDNET_PATH = td / "cache" / "tdnet.json"
        m.PO_RECORDS_PATH = td / "data" / "po.json"
        m.HOLDINGS_RECORDS_PATH = td / "data" / "holdings.json"

    def tearDown(self) -> None:
        (
            self.m.RECORDS_PATH, self.m.FINS_PATH, self.m.BUYBACK_PATH,
            self.m.BARS_PATH, self.m.TDNET_PATH, self.m.PO_RECORDS_PATH,
            self.m.HOLDINGS_RECORDS_PATH,
        ) = self._orig
        self._td.cleanup()

    def test_check_records(self) -> None:
        lines: list[str] = []
        result = self.m.check_records(lines)
        self.assertEqual(result.get("total"), 1)
        self.assertTrue(any("kouhou_genshu" in ln for ln in lines))

    def test_check_fins(self) -> None:
        lines: list[str] = []
        result = self.m.check_fins(lines)
        self.assertEqual(result.get("rows"), 1)

    def test_check_buyback_missing(self) -> None:
        lines: list[str] = []
        self.m.check_buyback(lines)
        self.assertTrue(any("share_buyback" in ln for ln in lines))

    def test_check_bars(self) -> None:
        lines: list[str] = []
        result = self.m.check_bars(lines)
        self.assertEqual(result.get("codes"), 1)

    def test_check_tdnet(self) -> None:
        lines: list[str] = []
        result = self.m.check_tdnet(lines)
        self.assertEqual(result.get("rows"), 1)
        self.assertTrue(any("tdnet_all" in ln for ln in lines))

    def test_check_tdnet_missing(self) -> None:
        self.m.TDNET_PATH = Path(self._td.name) / "cache" / "does-not-exist.json"
        lines: list[str] = []
        self.m.check_tdnet(lines)
        self.assertTrue(any("なし" in ln or "missing" in ln for ln in lines))

    def test_check_po(self) -> None:
        lines: list[str] = []
        result = self.m.check_po(lines)
        self.assertEqual(result.get("total"), 1)
        self.assertEqual(result.get("with_price"), 1)
        self.assertTrue(any("po_records.json" in ln for ln in lines))

    def test_check_po_missing(self) -> None:
        self.m.PO_RECORDS_PATH = Path(self._td.name) / "data" / "does-not-exist.json"
        lines: list[str] = []
        result = self.m.check_po(lines)
        self.assertEqual(result.get("critical"), 1)
        self.assertTrue(any("missing" in ln for ln in lines))

    def test_check_holdings(self) -> None:
        lines: list[str] = []
        result = self.m.check_holdings(lines)
        self.assertEqual(result.get("total"), 1)
        self.assertEqual(result.get("with_price"), 1)
        self.assertTrue(any("holdings_records.json" in ln for ln in lines))

    def test_check_holdings_missing(self) -> None:
        self.m.HOLDINGS_RECORDS_PATH = Path(self._td.name) / "data" / "does-not-exist.json"
        lines: list[str] = []
        result = self.m.check_holdings(lines)
        self.assertEqual(result.get("critical"), 1)
        self.assertTrue(any("missing" in ln for ln in lines))


# ---- enrich_price_kouaku ----------------------------------------------------

class TestEnrichRecordWithMockBars(unittest.TestCase):
    def setUp(self) -> None:
        from scripts import enrich_price_kouaku as ep
        self.ep = ep
        self._orig_bars = ep._bars
        self._orig_min = ep._minute_bars
        ep._bars = lambda code, since, until: [
            {"Date": "2025-01-21", "O": 100, "H": 105, "L": 99, "C": 102,
             "AdjO": 100, "AdjH": 105, "AdjL": 99, "AdjC": 102},
            {"Date": "2025-01-22", "O": 102, "H": 110, "L": 101, "C": 108,
             "AdjO": 102, "AdjH": 110, "AdjL": 101, "AdjC": 108},
            {"Date": "2025-01-23", "O": 108, "H": 112, "L": 105, "C": 110,
             "AdjO": 108, "AdjH": 112, "AdjL": 105, "AdjC": 110},
        ]
        ep._minute_bars = lambda code, d: []

    def tearDown(self) -> None:
        self.ep._bars = self._orig_bars
        self.ep._minute_bars = self._orig_min

    def test_enrich_record_fills_basic_attrs(self) -> None:
        rec = {"code": "7203", "event_date": "2025-01-22", "attrs": {}}
        self.ep.enrich_record(rec)
        a = rec["attrs"]
        self.assertEqual(a["prev_close"], 108)  # 当日終値
        self.assertEqual(a["next_open"], 108)
        self.assertIsNotNone(a["gap_pct"])
        self.assertFalse(a["limit_locked"])


class TestEnrichAllSkipsAlreadyEnriched(unittest.TestCase):
    def test_skips_records_with_next_open(self) -> None:
        from scripts.enrich_price_kouaku import enrich_all
        rec = {"code": "7203", "event_date": "2025-01-22",
               "attrs": {"next_open": 100.0}}  # already enriched
        out = enrich_all([rec], sleep_sec=0.0)
        self.assertEqual(out, [rec])


# ---- fetch_disclosures: save_* (実 fetch 関数は network 必要なので import のみ) -----

class TestFetchSavers(unittest.TestCase):
    def test_save_buyback_writes_json(self) -> None:
        from scripts.fetch_disclosures import save_buyback
        with tempfile.TemporaryDirectory() as td:
            p = save_buyback({"2025-01-22": [{"Code": "72030"}]}, cache_dir=Path(td))
            self.assertTrue(p.exists())
            data = json.loads(p.read_text())
            self.assertEqual(data["record_count"], 1)

    def test_save_fins_summary_writes_json(self) -> None:
        from scripts.fetch_disclosures import save_fins_summary
        with tempfile.TemporaryDirectory() as td:
            p = save_fins_summary({"2025-01-22": [{"Code": "72030"}, {"Code": "45020"}]}, cache_dir=Path(td))
            data = json.loads(p.read_text())
            self.assertEqual(data["record_count"], 2)

    def test_save_fins_summary_by_code_writes_json(self) -> None:
        from scripts.fetch_disclosures import save_fins_summary_by_code
        with tempfile.TemporaryDirectory() as td:
            p = save_fins_summary_by_code({"7203": [{"Code": "72030"}]}, cache_dir=Path(td))
            data = json.loads(p.read_text())
            self.assertEqual(data["record_count"], 1)


class TestFetchFallbacks(unittest.TestCase):
    """EDINET fetcher は NotImplementedError スタブ (現環境では allowlist 未許可)。"""

    def test_edinet_raises(self) -> None:
        from scripts.fetch_disclosures import fetch_edinet_day
        from datetime import date
        with self.assertRaises(NotImplementedError):
            fetch_edinet_day(date(2025, 1, 22))


class TestTdnetYanoshin(unittest.TestCase):
    """yanoshin TDnet 取り口 (mock 経由でレスポンス正規化を検証)。"""

    def _build_response(self, *items: dict) -> bytes:
        return json.dumps({"total_count": len(items), "items": items}).encode()

    def test_tdnet_public_day_normalizes(self) -> None:
        from datetime import date
        from scripts import fetch_disclosures as fd
        body = self._build_response(
            {"Tdnet": {
                "id": "1234567", "pubdate": "2025-01-22 15:30:00",
                "company_code": "72030", "company_name": "トヨタ自動車",
                "title": "自己株式の取得に関するお知らせ",
                "document_url": "https://example.com/x.pdf",
                "markets_string": "東", "url_xbrl": None,
            }},
            {"Tdnet": {
                "id": "1234568", "pubdate": "2025-01-22 16:00:00",
                "company_code": "4246", "company_name": "ダイキョーニシカワ",
                "title": "業績予想の修正に関するお知らせ",
                "document_url": "https://example.com/y.pdf",
                "markets_string": "東",
            }},
        )

        class _Resp:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False
            def read(self_inner): return body

        with patch("urllib.request.urlopen", return_value=_Resp()):
            rows = fd.fetch_tdnet_public_day(date(2025, 1, 22))
        self.assertEqual(len(rows), 2)
        # 5桁→4桁 正規化
        self.assertEqual(rows[0]["code"], "7203")
        # 既に 4 桁ならそのまま
        self.assertEqual(rows[1]["code"], "4246")
        self.assertEqual(rows[0]["title"], "自己株式の取得に関するお知らせ")
        self.assertEqual(rows[0]["pubdate"], "2025-01-22 15:30:00")
        self.assertEqual(rows[0]["company_name"], "トヨタ自動車")

    def test_tdnet_public_day_http_error_raises(self) -> None:
        from datetime import date
        from scripts import fetch_disclosures as fd
        import urllib.error
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b"missing")),
        ):
            with self.assertRaises(fd.TdnetFetchError):
                fd.fetch_tdnet_public_day(date(2025, 1, 22), retries=1)

    def test_save_tdnet_all(self) -> None:
        from scripts.fetch_disclosures import save_tdnet_all
        with tempfile.TemporaryDirectory() as td:
            p = save_tdnet_all(
                {"2025-01-22": [{"code": "7203", "title": "自己株式の取得"}]},
                cache_dir=Path(td),
            )
            data = json.loads(p.read_text())
            self.assertEqual(data["source"], "yanoshin:tdnet")
            self.assertEqual(data["record_count"], 1)

    def test_fetch_tdnet_range_iterates_trading_days(self) -> None:
        from datetime import date
        from scripts import fetch_disclosures as fd
        # _trading_days と fetch_tdnet_public_day を mock して呼び出し回数だけ確認
        with patch.object(fd, "_trading_days", return_value=[date(2025, 1, 22), date(2025, 1, 23)]):
            with patch.object(
                fd, "fetch_tdnet_public_day",
                side_effect=[[{"code": "7203", "title": "x"}], [{"code": "4246", "title": "y"}]],
            ) as fmock:
                out = fd.fetch_tdnet_range(date(2025, 1, 22), date(2025, 1, 23), sleep_sec=0)
        self.assertEqual(fmock.call_count, 2)
        self.assertEqual(out["2025-01-22"][0]["code"], "7203")
        self.assertEqual(out["2025-01-23"][0]["code"], "4246")

    def test_fetch_tdnet_range_skips_failed_day(self) -> None:
        from datetime import date
        from scripts import fetch_disclosures as fd
        with patch.object(fd, "_trading_days", return_value=[date(2025, 1, 22), date(2025, 1, 23)]):
            with patch.object(
                fd, "fetch_tdnet_public_day",
                side_effect=[fd.TdnetFetchError("boom"), [{"code": "9999", "title": "z"}]],
            ):
                out = fd.fetch_tdnet_range(date(2025, 1, 22), date(2025, 1, 23), sleep_sec=0)
        self.assertNotIn("2025-01-22", out)
        self.assertIn("2025-01-23", out)


class TestFetchNetworkBoundEntrypoints(unittest.TestCase):
    """fetch_share_buyback_day / fetch_fins_summary_* は実 API なので、
    呼び出しシグネチャだけ確認 (mock してエラーパスを通す)。"""

    def test_fetch_share_buyback_day_uses_pro_base(self) -> None:
        from scripts import fetch_disclosures as fd
        with patch.object(fd._jquants, "get_list", return_value=[{"Code": "72030"}]) as mocked:
            from datetime import date
            rows = fd.fetch_share_buyback_day(date(2025, 1, 22))
            self.assertEqual(rows, [{"Code": "72030"}])
            args, kwargs = mocked.call_args
            self.assertEqual(kwargs.get("base"), fd._jquants.PRO_BASE_URL)

    def test_fetch_fins_summary_with_code(self) -> None:
        from scripts import fetch_disclosures as fd
        with patch.object(fd._jquants, "get_list", return_value=[]) as mocked:
            fd.fetch_fins_summary(code="7203")
            args, kwargs = mocked.call_args
            self.assertEqual(kwargs.get("code"), "7203")

    def test_fetch_fins_summary_requires_param(self) -> None:
        from scripts.fetch_disclosures import fetch_fins_summary
        with self.assertRaises(ValueError):
            fetch_fins_summary()

    def test_fetch_share_buyback_range_iterates_days(self) -> None:
        from scripts import fetch_disclosures as fd
        from datetime import date
        with patch.object(fd, "_trading_days", return_value=[date(2025, 1, 22), date(2025, 1, 23)]):
            with patch.object(fd, "fetch_share_buyback_day", return_value=[{"Code": "72030"}]):
                out = fd.fetch_share_buyback_range(date(2025, 1, 22), date(2025, 1, 23))
                self.assertEqual(len(out), 2)

    def test_fetch_fins_summary_range_by_date_iterates_days(self) -> None:
        from scripts import fetch_disclosures as fd
        from datetime import date
        with patch.object(fd, "_trading_days", return_value=[date(2025, 1, 22)]):
            with patch.object(fd._jquants, "get_list", return_value=[]):
                out = fd.fetch_fins_summary_range_by_date(date(2025, 1, 22), date(2025, 1, 22))
                self.assertIn("2025-01-22", out)


# ---- noon_disclosure_experiment --------------------------------------------

class TestNoonExperiment(unittest.TestCase):
    def test_collect_bad_events_imports_and_callable(self) -> None:
        """実 fins_summary.json は 250MB なので fixture で動作確認のみ。"""
        from scripts.noon_disclosure_experiment import collect_bad_events
        self.assertTrue(callable(collect_bad_events))

    def test_attach_prices_handles_empty(self) -> None:
        from scripts.noon_disclosure_experiment import attach_prices
        events: list[dict] = []
        attach_prices(events, {})
        self.assertEqual(events, [])

    def test_attach_prices_with_fixture(self) -> None:
        from scripts.noon_disclosure_experiment import attach_prices
        events = [{"code": "7203", "event_date": "2025-01-22"}]
        bars = {"7203": [
            {"Date": "2025-01-21", "AdjC": 100, "AdjO": 100, "AdjH": 100, "AdjL": 100, "C": 100, "O": 100, "H": 100, "L": 100},
            {"Date": "2025-01-22", "AdjC": 105, "AdjO": 102, "AdjH": 106, "AdjL": 101, "C": 105, "O": 102, "H": 106, "L": 101},
            {"Date": "2025-01-23", "AdjC": 110, "AdjO": 108, "AdjH": 112, "AdjL": 107, "C": 110, "O": 108, "H": 112, "L": 107},
        ]}
        attach_prices(events, bars)
        self.assertIn("gap_pct", events[0])

    def test_build_report_returns_md(self) -> None:
        from scripts.noon_disclosure_experiment import build_report
        out = build_report([])
        self.assertTrue(out.startswith("# "))
        self.assertIn("bad", out)

    def test_fetch_all_daily_bars_signature(self) -> None:
        """1GB キャッシュを読まずに関数の存在のみ確認。"""
        from scripts.noon_disclosure_experiment import fetch_all_daily_bars
        self.assertTrue(callable(fetch_all_daily_bars))


if __name__ == "__main__":
    unittest.main(verbosity=2)

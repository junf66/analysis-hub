"""Smoke tests for newly added analysis scripts."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPLIT_DATA_PATH = REPO_ROOT / "data" / "edge_candidates" / "split_multiday_enriched.json"
BUYBACK_DATA_PATH = REPO_ROOT / "data" / "edge_candidates" / "buyback_standalone_enriched.json"

from scripts.analyze_split_gu_filter import load_data as split_load_data
from scripts.analyze_split_gu_filter import build_report as split_build_report
from scripts.analyze_po_edge1_opportunity import load_po_records, load_equities_master
from scripts.analyze_po_edge1_opportunity import build_report as po_build_report
from scripts.analyze_kouaku_magnitude_robustness import build_report as mag_build_report
from scripts.analyze_split_size_definition import load_data as size_load_data
from scripts.analyze_split_size_definition import build_report as size_build_report
from scripts.analyze_reit_po_size_breakdown import load_po_records as reit_load_po
from scripts.analyze_reit_po_size_breakdown import filter_eligible_reit_po, reit_observations_by_size
from scripts.analyze_reit_po_size_breakdown import build_report as reit_build_report
from scripts.analyze_buyback_standalone import load_data as buyback_load_data
from scripts.analyze_buyback_standalone import build_report as buyback_build_report
from scripts.analyze_short_edges_size import load_master, load_kouaku, load_genshu_d3
from scripts.analyze_short_edges_size import load_fins_by_code, np_yoy_asof
from scripts.analyze_short_edges_size import build_report as short_size_build_report
from scripts.analyze_po_edge1a_minute import load_po_records as e1a_load_po
from scripts.analyze_po_edge1a_minute import load_scale_map, load_minute, entry_and_exits, collect
from scripts.analyze_po_edge1a_minute import build_report as e1a_build_report
from scripts.analyze_po_long_size_brackets import bracket_label, collect_po_long
from scripts.analyze_po_long_size_brackets import scale_band_mc_ranges, stat as brk_stat
from scripts.analyze_po_long_size_brackets import build_report as brk_build_report
from scripts.analyze_po_long_size_brackets import load_records as brk_load_records
from scripts.analyze_po_long_size_brackets import load_enriched, load_master_records
from scripts.analyze_po_long_size_brackets import collect_size_by_exit, best_exit
from scripts.analyze_po_long_size_brackets import collect_yen_floor_by_exit
from scripts.analyze_po_delivery_long import collect_delivery_long, metrics as dl_metrics
from scripts.analyze_po_delivery_long import oos_split_date, load_records as dl_load_records
from scripts.analyze_po_delivery_long import build_report as dl_build_report
from scripts.scan_po_candidates import build_observations, scan, signal_ret
from scripts.scan_po_candidates import load_records as scan_load_records
from scripts.scan_po_candidates import load_enriched as scan_load_enriched
from scripts.scan_po_candidates import build_report as scan_build_report
from scripts.scan_kouaku_candidates import build_observations as k_build_obs
from scripts.scan_kouaku_candidates import scan as k_scan, mag_bucket
from scripts.scan_kouaku_candidates import load_kouaku, load_master as k_load_master
from scripts.scan_kouaku_candidates import build_report as k_build_report
from scripts.scan_kouaku_candidates import day_means as k_day_means
from scripts.edge_candidates.analyze_pharma_long import collect as pharma_collect
from scripts.edge_candidates.analyze_pharma_long import stat as pharma_stat
from scripts.edge_candidates.analyze_pharma_long import day_means as pharma_day_means
from scripts.edge_candidates.analyze_pharma_long import load_kouaku as pharma_load_kouaku
from scripts.edge_candidates.analyze_pharma_long import load_master as pharma_load_master
from scripts.edge_candidates.analyze_pharma_long import build_report as pharma_build_report
from scripts.analyze_delivery_long_filters import base_records, rank_filters
from scripts.analyze_delivery_long_filters import load_records as flt_load_records
from scripts.analyze_delivery_long_filters import build_report as flt_build_report
from scripts.analyze_delivery_long_filters import build_observations as flt_build_obs
from scripts.edge_candidates.enrich_buyback_pdf import parse_buyback_text, merge_decisions
from scripts.edge_candidates.extract_mild_cases import build_events as mild_cases_build
from scripts.edge_candidates.scan_title_keywords import scan as title_scan
from scripts.edge_candidates.analyze_mild_nx_band import band_of, build_events as nx_build_events
from scripts.edge_candidates.analyze_zouhai_kahou_nx_beta import (
    short_cell as zk_short_cell, build_rows as zk_build_rows,
)
from scripts.edge_candidates.enrich_mild_buyback import (
    load_buyback_decision_map, enrich_record as mb_enrich_record,
)
from scripts.edge_candidates.gen_chat_assistant import build_prompt as chat_build_prompt
from scripts.edge_candidates.fetch_buyback_edinet import parse_edinet_csv, sec_to_code4


class TestAnalyzeNewScripts(unittest.TestCase):
    """Smoke tests to verify new analysis scripts run without error."""

    def test_split_gu_filter_loads_and_reports(self) -> None:
        """Test that split GU filter script loads data and builds report."""
        if not SPLIT_DATA_PATH.exists():
            self.skipTest("split_multiday_enriched.json not available (regenerable cache)")
        records = split_load_data()
        self.assertIsInstance(records, list)
        report = split_build_report(records)
        self.assertIn("信用", report)
        self.assertIn("GU", report)

    def test_po_edge1_opportunity_loads_and_reports(self) -> None:
        """Test that PO edge1 opportunity script loads and builds report."""
        records = load_po_records()
        master = load_equities_master()
        self.assertIsInstance(records, list)
        self.assertIsInstance(master, dict)
        report = po_build_report(records, master)
        self.assertIn("①A", report)
        self.assertIn("①B", report)
        self.assertIn("+0.52%", report)

    def test_kouaku_magnitude_robustness_reports(self) -> None:
        """Test that magnitude robustness script builds report."""
        report = mag_build_report()
        self.assertIsInstance(report, str)
        self.assertIn("magnitude", report)
        self.assertIn("FDR", report)
        self.assertIn("+1.34%", report)

    def test_split_size_definition_loads_and_reports(self) -> None:
        """Test that split size definition script loads and builds report."""
        if not SPLIT_DATA_PATH.exists():
            self.skipTest("split_multiday_enriched.json not available (regenerable cache)")
        records = size_load_data()
        self.assertIsInstance(records, list)
        self.assertGreater(len(records), 0)
        report = size_build_report(records)
        self.assertIn("小型", report)
        self.assertIn("中型", report)
        self.assertIn("+2.13%", report)

    def test_reit_po_size_breakdown_loads_and_reports(self) -> None:
        """Test that REIT PO size breakdown script loads and builds report."""
        records = reit_load_po()
        self.assertIsInstance(records, list)
        eligible = filter_eligible_reit_po(records)
        self.assertIsInstance(eligible, list)
        self.assertGreater(len(eligible), 0)
        obs_by_size = reit_observations_by_size(eligible)
        self.assertIsInstance(obs_by_size, dict)
        report = reit_build_report(records)
        self.assertIn("決定", report)
        self.assertIn("中型", report)
        self.assertIn("+1.78%", report)

    def test_buyback_standalone_loads_and_reports(self) -> None:
        """Test that buyback standalone script loads data and builds report."""
        if not BUYBACK_DATA_PATH.exists():
            self.skipTest("buyback_standalone_enriched.json not available (regenerable cache)")
        records = buyback_load_data()
        self.assertIsInstance(records, list)
        report = buyback_build_report(records)
        self.assertIn("自社株買い", report)
        self.assertIn("規模別", report)

    def test_buyback_standalone_report_with_synthetic_data(self) -> None:
        """build_report works on synthetic records (no cache dependency)."""
        synthetic = [
            {"attrs": {"combo": "単独", "scale_band": "小型", "disc_bucket": "大引け後",
                       "alpha_d1_ret": 0.5, "alpha_d3_ret": 0.8, "alpha_d5_ret": 1.0,
                       "alpha_d10_ret": 1.2}}
            for _ in range(20)
        ]
        report = buyback_build_report(synthetic)
        self.assertIn("単独", report)
        self.assertIn("所見", report)

    def test_short_edges_size_loads_master(self) -> None:
        """load_master/load_kouaku return expected container types."""
        master = load_master()
        self.assertIsInstance(master, dict)
        self.assertGreater(len(master), 0)
        kouaku = load_kouaku()
        self.assertIsInstance(kouaku, list)
        self.assertIsInstance(load_genshu_d3(), list)
        self.assertIsInstance(load_fins_by_code(), dict)

    def test_np_yoy_asof_computes_year_over_year(self) -> None:
        """np_yoy_asof returns same-quarter prior-year NP percent change."""
        fins = {"13010": [
            {"DiscDate": "2024-05-10", "CurPerType": "FY", "CurPerEn": "2024-03-31", "NP": "90"},
            {"DiscDate": "2023-05-10", "CurPerType": "FY", "CurPerEn": "2023-03-31", "NP": "100"},
        ]}
        yoy = np_yoy_asof(fins, "13010", "2024-06-01")
        self.assertAlmostEqual(yoy, -10.0, places=3)
        self.assertIsNone(np_yoy_asof(fins, "99999", "2024-06-01"))

    def test_short_edges_size_report_with_synthetic_data(self) -> None:
        """build_report works on synthetic ④⑤ records (no cache dependency)."""
        kouaku = [
            {"code": "13010", "subpattern": "zouhai_kahou_nx",
             "good_factors": [{"disc_time": "16:00"}], "bad_factors": [],
             "attrs": {"next_day_open_to_close_ret": 1.0}}
            for _ in range(15)
        ]
        genshu = [
            {"code": "13010", "attrs": {"scale_band": "小型", "d3_ret": 0.8}}
            for _ in range(15)
        ]
        master = {"13010": {"Code": "13010", "scale_band": "小型"}}
        report = short_size_build_report(kouaku, genshu, master)
        self.assertIn("zouhai_kahou_nx", report)
        self.assertIn("zouhai_genshu", report)
        self.assertIn("大引け後", report)

    def test_e1a_entry_and_exits_computes_long_returns(self) -> None:
        """entry_and_exits returns long % from next-day open to each exit time."""
        bars = [
            {"Date": "2024-06-03", "Time": "09:00", "O": 100.0, "C": 100.0},
            {"Date": "2024-06-03", "Time": "09:05", "O": 101.0, "C": 101.0},
            {"Date": "2024-06-03", "Time": "09:30", "O": 102.0, "C": 102.0},
        ]
        res = entry_and_exits(bars, "2024-06-02", ["09:05", "09:30"])
        self.assertAlmostEqual(res["09:05"], 1.0, places=3)
        self.assertAlmostEqual(res["09:30"], 2.0, places=3)
        self.assertIsNone(entry_and_exits(bars, "2024-06-10", ["09:05"]))

    def test_e1a_loads_and_reports(self) -> None:
        """①A reverify loaders return types and build_report runs."""
        self.assertIsInstance(e1a_load_po(), list)
        self.assertIsInstance(load_scale_map(), dict)
        self.assertIsInstance(load_minute(), dict)
        report = e1a_build_report([], {}, {"99999": []})
        self.assertIn("①A", report)
        records = [{"stage": "announce", "po_type": "普通", "code": "13010",
                    "dilution": 5.0, "attrs": {"gap_pct": -1.0}}]
        scale = {"13010": "大型"}
        minute = {"13010": [
            {"Date": "2024-06-03", "Time": "09:00", "O": 100.0, "C": 100.0},
            {"Date": "2024-06-03", "Time": "09:05", "O": 101.0, "C": 101.0},
        ]}
        by_time = collect(records, scale, minute)
        self.assertEqual(by_time["09:05"][0], (101.0 / 100.0 - 1.0) * 100.0 - 0.20)

    def test_size_bracket_label_boundaries(self) -> None:
        """bracket_label assigns market cap (億円) to the right band."""
        self.assertEqual(bracket_label(100), "<300億")
        self.assertEqual(bracket_label(300), "300-500億")
        self.assertEqual(bracket_label(4178), "3,000億-1兆")
        self.assertEqual(bracket_label(50000), "≥1兆")

    def test_size_bracket_collect_routes_by_mc_and_band(self) -> None:
        """collect_po_long routes a GD announce record into mc bracket and scale band."""
        records = [{"id": "x1", "stage": "announce", "po_type": "普通",
                    "code": "13010", "market_cap": 4178.0, "attrs": {"gap_pct": -1.0}}]
        enriched = {"x1": {"next_day_open_to_close_ret": 1.34, "scale_band": "中型"}}
        by_mc, by_band = collect_po_long(records, enriched)
        self.assertAlmostEqual(by_mc["3,000億-1兆"][0], 1.34 - 0.20, places=6)
        self.assertAlmostEqual(by_band["中型"][0], 1.34 - 0.20, places=6)
        # gap が浅い(>-0.5%)レコードは除外
        records[0]["attrs"]["gap_pct"] = 0.1
        by_mc2, _ = collect_po_long(records, enriched)
        self.assertEqual(sum(len(v) for v in by_mc2.values()), 0)

    def test_collect_size_by_exit_routes_intraday_and_close(self) -> None:
        """collect_size_by_exit reads intraday ret from attrs and 引け from enriched."""
        records = [{"id": "x1", "stage": "announce", "po_type": "普通", "code": "13010",
                    "attrs": {"gap_pct": -1.0, "next_day_930_ret": 0.5}}]
        enriched = {"x1": {"next_day_open_to_close_ret": 1.2, "scale_band": "中型"}}
        by_se = collect_size_by_exit(records, enriched)
        self.assertAlmostEqual(by_se["中型"]["9:30"][0], 0.5 - 0.20, places=6)
        self.assertAlmostEqual(by_se["中型"]["引け"][0], 1.2 - 0.20, places=6)
        self.assertEqual(by_se["小型"]["引け"], [])

    def test_collect_yen_floor_filters_by_mc_and_gd(self) -> None:
        """collect_yen_floor_by_exit keeps only mc≥floor, and respects gd_only."""
        records = [
            {"id": "big", "stage": "announce", "po_type": "普通", "market_cap": 15000.0,
             "attrs": {"gap_pct": -1.0, "next_day_905_ret": 0.6}},
            {"id": "small", "stage": "announce", "po_type": "普通", "market_cap": 500.0,
             "attrs": {"gap_pct": -1.0, "next_day_905_ret": 5.0}},
            {"id": "gu", "stage": "announce", "po_type": "普通", "market_cap": 15000.0,
             "attrs": {"gap_pct": 1.0, "next_day_905_ret": 0.2}},
        ]
        enriched = {}
        # GD限定: big のみ (small は時価総額未満, gu は GD でない)
        gd = collect_yen_floor_by_exit(records, enriched, 10000.0, gd_only=True)
        self.assertEqual(len(gd["9:05"]), 1)
        self.assertAlmostEqual(gd["9:05"][0], 0.6 - 0.20, places=6)
        # 全GUGD: big と gu (small は時価総額未満)
        allg = collect_yen_floor_by_exit(records, enriched, 10000.0, gd_only=False)
        self.assertEqual(len(allg["9:05"]), 2)

    def test_delivery_long_filters_and_metrics(self) -> None:
        """collect_delivery_long applies stage/size/gap filters; metrics computes t_clust."""
        records = [
            # 採用: deliver/普通/規模120/時価800/希薄5/gap-1.0 (GD)
            {"stage": "deliver", "po_type": "普通", "po_scale": 120.0, "market_cap": 800.0,
             "dilution": 5.0, "event_date": "2024-01-10",
             "attrs": {"gap_pct": -1.0, "next_day_open_to_close_ret": 1.0}},
            # 除外: 時価総額500以下
            {"stage": "deliver", "po_type": "普通", "po_scale": 120.0, "market_cap": 400.0,
             "dilution": 5.0, "event_date": "2024-01-11",
             "attrs": {"gap_pct": -1.0, "next_day_open_to_close_ret": 9.0}},
            # 除外: gap が範囲外 (GU, gap_hi=0.5 未満でない)
            {"stage": "deliver", "po_type": "普通", "po_scale": 120.0, "market_cap": 800.0,
             "dilution": 5.0, "event_date": "2024-01-12",
             "attrs": {"gap_pct": 2.0, "next_day_open_to_close_ret": 9.0}},
        ]
        rets, by_date = collect_delivery_long(records, -99.0, 0.5)
        self.assertEqual(len(rets), 1)
        self.assertAlmostEqual(rets[0], 1.0 - 0.20, places=6)
        m = dl_metrics(rets, by_date)
        self.assertEqual(m["n"], 1)
        self.assertEqual(dl_metrics([], {})["n"], 0)

    def test_delivery_long_oos_split_and_report(self) -> None:
        """oos_split_date needs ≥4 dates; build_report runs on real cache."""
        self.assertIsNone(oos_split_date({"d1": [1.0]}))
        self.assertEqual(oos_split_date({f"2024-01-0{i}": [1.0] for i in range(1, 6)}, 0.6),
                         "2024-01-04")
        report = dl_build_report(dl_load_records())
        self.assertIn("受渡日", report)
        self.assertIn("GD+フラット", report)

    def test_scan_signal_ret_reads_attrs_and_enriched(self) -> None:
        """signal_ret pulls from attrs or enriched per signal src."""
        r = {"attrs": {"ret_close": 1.5}}
        self.assertEqual(signal_ret(r, {}, {"src": "attrs", "field": "ret_close"}), 1.5)
        self.assertEqual(signal_ret(r, {"next_day_open_to_close_ret": 2.0},
                                    {"src": "enriched", "field": "next_day_open_to_close_ret"}), 2.0)
        self.assertIsNone(signal_ret({"attrs": {}}, {}, {"src": "attrs", "field": "x"}))

    def test_scan_build_observations_emits_single_and_pairwise(self) -> None:
        """build_observations emits 全体 + 1軸 + 2軸 cells for a matching record."""
        records = [{"stage": "decide", "po_type": "リート", "lending_type": "貸借",
                    "event_date": "2024-01-10", "code": "12345",
                    "market_cap": 700.0, "attrs": {"ret_close": -1.0}}]
        obs = build_observations(records, {}, max_combo=2)
        combos = {o["cell"][1] for o in obs}
        self.assertIn(("全体",), combos)
        self.assertIn(("種別:リート",), combos)  # 単一軸
        # 2軸の掛け合わせが少なくとも1つ存在
        self.assertTrue(any(len(c) == 2 for c in combos))

    def test_scan_since_filter_reduces_sample(self) -> None:
        """scan(since=...) restricts to recent events (fewer or equal candidates' base)."""
        records = scan_load_records()
        enriched = scan_load_enriched()
        from scripts.scan_po_candidates import build_observations
        full = len(build_observations(records, enriched))
        recent = len(build_observations(records, enriched, since="2024-06-03"))
        self.assertLessEqual(recent, full)
        self.assertGreater(full, 0)

    def test_pharma_long_filter_and_report(self) -> None:
        """医薬品×信用のみ collect が拾い、貸借は除外。build_report は cache で走る。"""
        records = [
            {"code": "4500", "event_date": "2024-01-10", "subpattern": "kouhou_x",
             "attrs": {"next_day_open_to_close_ret": 2.0}},  # 信用・医薬品 → 採用
            {"code": "4600", "event_date": "2024-01-10", "subpattern": "kouhou_x",
             "attrs": {"next_day_open_to_close_ret": 5.0}},  # 貸借 → 除外
        ]
        master = {
            "45000": {"Code": "45000", "S17Nm": "医薬品", "MrgnNm": "信用", "scale_band": "小型"},
            "46000": {"Code": "46000", "S17Nm": "医薬品", "MrgnNm": "貸借", "scale_band": "小型"},
        }
        dm = pharma_day_means(records)
        shinyo = pharma_collect(records, master, dm, lambda r, m: m.get("MrgnNm") == "信用")
        taishaku = pharma_collect(records, master, dm, lambda r, m: m.get("MrgnNm") == "貸借")
        self.assertEqual(len(shinyo), 1)
        self.assertEqual(len(taishaku), 1)
        self.assertEqual(pharma_stat([])["n"], 0)
        report = pharma_build_report(pharma_load_kouaku(), pharma_load_master())
        self.assertIn("医薬品", report)
        self.assertIn("信用", report)

    def test_kouaku_mag_bucket_and_scan(self) -> None:
        """mag_bucket bands by primary pct metric; scan/report run on real cache."""
        rec = {"bad_factors": [{"metric": {"NP_YoY_pct": -20.0}}], "good_factors": []}
        self.assertEqual(mag_bucket(rec), "程度:中(-30〜-10%)")
        self.assertIsNone(mag_bucket({"bad_factors": [], "good_factors": []}))
        records = load_kouaku()
        master = k_load_master()
        obs = k_build_obs(records[:200], master, max_combo=2)
        self.assertTrue(any(len(o["cell"]) == 2 for o in obs))
        cands = k_scan(records, master)
        self.assertIsInstance(cands, list)
        for c in cands:
            self.assertGreater(c["ev_net"], 0)
        report = k_build_report(records, master)
        self.assertIn("候補", report)
        # day_means: 同日の平均リターン
        dm = k_day_means([
            {"event_date": "2024-01-10", "attrs": {"next_day_open_to_close_ret": 1.0}},
            {"event_date": "2024-01-10", "attrs": {"next_day_open_to_close_ret": 3.0}},
        ])
        self.assertAlmostEqual(dm["2024-01-10"], 2.0, places=6)

    def test_scan_runs_and_reports_on_cache(self) -> None:
        """scan returns candidate dicts and build_report renders from real cache."""
        records = scan_load_records()
        enriched = scan_load_enriched()
        cands = scan(records, enriched)
        self.assertIsInstance(cands, list)
        for c in cands:
            self.assertGreater(c["ev_net"], 0)
            self.assertGreaterEqual(c["t_clustered"], 1.5)
        report = scan_build_report(records, enriched)
        self.assertIn("候補", report)

    def test_delivery_filters_base_and_observations(self) -> None:
        """base_records keeps only GD+フラット deliver 普通; observations emit combos."""
        records = [
            {"stage": "deliver", "po_type": "普通", "event_date": "2024-01-10", "code": "1",
             "po_scale": 400.0, "dilution": 5.0,
             "attrs": {"gap_pct": -1.0, "next_day_open_to_close_ret": 1.0}},
            {"stage": "deliver", "po_type": "普通", "event_date": "2024-01-11", "code": "2",
             "attrs": {"gap_pct": 2.0, "next_day_open_to_close_ret": 1.0}},  # GU除外
        ]
        base = base_records(records)
        self.assertEqual(len(base), 1)
        obs = flt_build_obs(base, max_combo=2)
        cells = {o["cell"] for o in obs}
        self.assertIn(("土台(無フィルタ)",), cells)
        self.assertIn(("PO規模≥300億",), cells)
        self.assertTrue(any(len(c) == 2 for c in cells))

    def test_delivery_filters_rank_and_report(self) -> None:
        """rank_filters returns dicts sorted by net EV; build_report runs on cache."""
        records = flt_load_records()
        ranked = rank_filters(base_records(records))
        self.assertIsInstance(ranked, list)
        if len(ranked) >= 2:
            self.assertGreaterEqual(ranked[0]["ev_net"], ranked[-1]["ev_net"])
        report = flt_build_report(records)
        self.assertIn("加点フィルタ", report)

    def test_best_exit_picks_max_ev_above_min_n(self) -> None:
        """best_exit returns the highest-EV exit meeting the n floor, else None."""
        by_exit = {"9:30": [0.1, 0.1, 0.1], "引け": [1.0, 1.0, 1.0]}
        label, s = best_exit(by_exit, min_n=3)
        self.assertEqual(label, "引け")
        self.assertEqual(s["n"], 3)
        self.assertIsNone(best_exit(by_exit, min_n=10))

    def test_size_bracket_loaders(self) -> None:
        """load_records/load_enriched/load_master_records return expected containers."""
        self.assertIsInstance(brk_load_records(), list)
        self.assertIsInstance(load_enriched(), dict)
        self.assertIsInstance(load_master_records(), list)

    def test_size_bracket_report_and_ranges(self) -> None:
        """build_report runs on synthetic data and ranges reflect overlap."""
        records = [{"id": "x1", "stage": "announce", "po_type": "普通",
                    "code": "13010", "market_cap": 4178.0, "attrs": {"gap_pct": -1.0}}]
        enriched = {"x1": {"next_day_open_to_close_ret": 1.34, "scale_band": "中型"}}
        master = [{"Code": "13010", "scale_band": "中型"}]
        rng = scale_band_mc_ranges(records, master)
        self.assertEqual(rng["中型"]["median"], 4178.0)
        report = brk_build_report(records, enriched, master)
        self.assertIn("TOPIX", report)
        self.assertIn("中型", report)
        self.assertEqual(brk_stat([])["n"], 0)

    def test_buyback_pdf_parse_text(self) -> None:
        """parse_buyback_text extracts 規模%/株数/金額 from PDF本文 (依存なし・CI安全)。"""
        text = ("当社は…発行済株式総数（自己株式を除く）に対する割合 3.08％ … "
                "取得する株式の総数 1,000,000 株 … 取得価額の総額 2,000,000,000 円")
        got = parse_buyback_text(text)
        self.assertAlmostEqual(got["buyback_ratio_pct"], 3.08, places=2)
        self.assertEqual(got["buyback_max_shares"], 1000000.0)
        self.assertEqual(got["buyback_max_amount"], 2000000000.0)
        # 規模%が無いテキストは None
        self.assertIsNone(parse_buyback_text("規模に関する記載なし")["buyback_ratio_pct"])

    def test_edinet_buyback_parse_and_code(self) -> None:
        """parse_edinet_csv は実 220 報告書のテキストブロックから取得枠規模%等を抽出。

        実データの癖を再現: 決議枠の株数・金額は区切り無し連結(カンマ区切りで分割)、
        日付は全角数字、発行済株式総数は「保有状況」ブロックに在る。
        """
        # 取得枠 6,000,000株 / 7,500,000,000円(連結)、発行済 86,021,392 → 6.975%
        # 各テキストブロック冒頭の「YYYY年MM月DD日現在」が報告月末日(=event_date)。
        res = ("2026年５月31日現在区分株式数(株)価額の総額(円)取締役会(2026年５月８日)での決議状況"
               "(取得期間2026年５月18日～2027年２月26日) 6,000,0007,500,000,000"
               "報告月における取得自己株式(取得日)５月18日26,20042,525,500"
               "計―260,100 423,753,800 報告月末現在の累計取得自己株式 260,100 423,753,800"
               "自己株式取得の進捗状況(％) 4.3 5.7 (注)")
        hold = ("2026年５月31日現在報告月末日における保有状況株式数(株)"
                "発行済株式総数86,021,392保有自己株式数670,186")
        csv = "\n".join([
            "\t".join(["要素ID", "項目名", "ctx", "yr", "ci", "pd", "u", "unit", "値"]),
            # 報告期間、表紙の至日は買付プログラム終了予定(将来)。report_end には使わない(回帰ガード)
            "\t".join(["a", "報告期間、表紙", "", "", "", "", "", "", "自 2026年５月１日 至 2027年２月26日"]),
            "\t".join(["b", "取締役会決議による取得の状況 [テキストブロック]", "", "", "", "", "", "", res]),
            "\t".join(["c", "保有状況 [テキストブロック]", "", "", "", "", "", "", hold]),
        ])
        got = parse_edinet_csv(csv)
        self.assertAlmostEqual(got["buyback_ratio_pct"], 6.975, places=2)
        self.assertEqual(got["buyback_max_shares"], 6000000.0)
        self.assertEqual(got["buyback_max_amount"], 7500000000.0)
        self.assertEqual(got["issued_shares"], 86021392.0)
        self.assertEqual(got["cumulative_shares"], 260100.0)
        self.assertEqual(got["cumulative_amount"], 423753800.0)
        self.assertEqual(got["decision_date"], "2026-05-08")  # 全角→半角
        self.assertEqual(got["report_end"], "2026-05-31")
        # 取得枠が（上限）注記混じり・全角括弧でも分割できる
        res2 = ("区分株式数（株）価額の総額（円）取締役会（2026年５月14日）での決議状況"
                "（取得期間 2026年６月１日～2027年３月23日）750,000(上限)285,000,000(上限)"
                "報告月における取得自己株式")
        csv2 = "\n".join([
            "\t".join(["要素ID", "項目名", "v"]),
            "\t".join(["b", "取締役会決議による取得の状況 [テキストブロック]", res2]),
            "\t".join(["c", "保有状況 [テキストブロック]", "発行済株式総数16,086,250保有自己株式数1"]),
        ])
        got2 = parse_edinet_csv(csv2)
        self.assertEqual(got2["buyback_max_shares"], 750000.0)
        self.assertEqual(got2["buyback_max_amount"], 285000000.0)
        self.assertAlmostEqual(got2["buyback_ratio_pct"], 4.662, places=2)
        # 発行済株式総数が無いテキストは ratio None
        self.assertIsNone(parse_edinet_csv("項目名\t値")["buyback_ratio_pct"])
        self.assertEqual(sec_to_code4("72030"), "7203")
        self.assertEqual(sec_to_code4("7203"), "7203")
        self.assertIsNone(sec_to_code4(None))

    def test_buyback_merge_decisions_dedup(self) -> None:
        """merge_decisions は DiscNo で重複排除し新しい順に並べる(週次cron用)。"""
        existing = [{"DiscNo": "1", "DiscDate": "2026-05-01"}]
        new = [{"DiscNo": "1", "DiscDate": "2026-05-01"},  # 既存と重複
               {"DiscNo": "2", "DiscDate": "2026-05-10"}]  # 新規
        merged = merge_decisions(existing, new)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["DiscNo"], "2")  # 新しい順

    def test_mild_cases_build_events(self) -> None:
        """mild_bad=軽い増益×減配, mild_genhai=微減配×好材料 を正しく拾う。"""
        fins = {"12340": [
            {"DiscDate": "2024-05-10", "CurPerType": "FY", "CurPerEn": "2024-03-31",
             "NP": 105, "DivFY": 90, "DiscTime": "15:00"},  # NP+5%(軽増益), DivFY-10%(減配)
            {"DiscDate": "2023-05-10", "CurPerType": "FY", "CurPerEn": "2023-03-31",
             "NP": 100, "DivFY": 100},
        ]}
        bad = mild_cases_build(fins, {}, "mild_bad")
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["attrs"]["bads"], ["genhai"])
        self.assertAlmostEqual(bad[0]["attrs"]["np_yoy"], 5.0, places=3)
        # mild_genhai: 微減配×自社株買い(td). DivFY -10% は減配で対象外、別データで微減配を作る
        fins2 = {"12340": [
            {"DiscDate": "2024-05-10", "CurPerType": "FY", "CurPerEn": "2024-03-31",
             "NP": 100, "DivFY": 98, "DiscTime": "15:00"},  # DivFY-2%(微減配)
            {"DiscDate": "2023-05-10", "CurPerType": "FY", "CurPerEn": "2023-03-31",
             "NP": 100, "DivFY": 100},
        ]}
        genhai = mild_cases_build(fins2, {("12340", "2024-05-10"): {"11105"}}, "mild_genhai")
        self.assertEqual(len(genhai), 1)
        self.assertEqual(genhai[0]["attrs"]["goods"], ["jisha"])
        # 好材料が無ければ mild_genhai は採用しない
        self.assertEqual(len(mild_cases_build(fins2, {}, "mild_genhai")), 0)

    def test_mild_cases_extra_bads_from_td(self) -> None:
        """同日開示の特損/下方修正(td_bad)が bads に加点される。"""
        fins = {"12340": [
            {"DiscDate": "2024-05-10", "CurPerType": "FY", "CurPerEn": "2024-03-31",
             "NP": 105, "DivFY": 90, "DiscTime": "15:00"},  # 軽増益×減配
            {"DiscDate": "2023-05-10", "CurPerType": "FY", "CurPerEn": "2023-03-31",
             "NP": 100, "DivFY": 100},
        ]}
        td_bad = {("12340", "2024-05-10"): {"tokuson", "kabu_geho"}}
        bad = mild_cases_build(fins, {}, "mild_bad", td_bad)
        self.assertEqual(len(bad), 1)
        # 既存の genhai + 同日の特損/下方修正(ソート済み)が並ぶ
        self.assertEqual(bad[0]["attrs"]["bads"], ["genhai", "kabu_geho", "tokuson"])
        # td_bad が無ければ従来どおり genhai のみ
        self.assertEqual(mild_cases_build(fins, {}, "mild_bad")[0]["attrs"]["bads"], ["genhai"])

    def test_title_scan_buckets_and_coverage(self) -> None:
        """Title 走査が材料バケットに割り、未被覆率を計算する。"""
        rows = [
            {"code": "1111", "event_date": "2024-01-05",
             "title": "通期業績予想の下方修正に関するお知らせ", "DiscItems": "11350"},
            {"code": "2222", "event_date": "2024-01-06",
             "title": "特別損失（減損損失）の計上に関するお知らせ", "DiscItems": "11201"},
            {"code": "3333", "event_date": "2024-01-07",
             "title": "本日は晴天なり"},  # どのバケットにも該当しない
        ]
        covered = {("2222", "2024-01-06")}  # 特損だけ既存被覆
        res = {d["bucket"]: d for d in title_scan(rows, covered, sample_n=2)}
        self.assertIn("業績予想_下方", res)
        self.assertEqual(res["業績予想_下方"]["n"], 1)
        self.assertEqual(res["業績予想_下方"]["uncovered"], 1)        # 未被覆
        self.assertEqual(res["特別損失_減損"]["n"], 1)
        self.assertEqual(res["特別損失_減損"]["uncovered"], 0)        # 被覆済み

    def test_mild_nx_band_classification(self) -> None:
        """NxFNp vs NP の中立帯を mild_kahou_nx/mild_kouhou_nx に割る。"""
        self.assertEqual(band_of(-20), "kahou_nx")       # 大幅来期減益
        self.assertEqual(band_of(-5), "mild_kahou_nx")   # 軽い来期減益(死角)
        self.assertEqual(band_of(0), "mild_kouhou_nx")   # 横ばい→軽い増益側
        self.assertEqual(band_of(5), "mild_kouhou_nx")   # 軽い来期増益(死角)
        self.assertEqual(band_of(20), "kouhou_nx")       # 大幅来期増益
        # build_events: FY行のみ・NP/NxFNp/DiscDate 揃いを採用 (行リストを渡す)
        rows = [
            {"Code": "12340", "CurPerType": "FY", "CurPerEn": "2025-03-31", "DiscDate": "2025-05-10",
             "DiscTime": "15:00", "NP": 100, "NxFNp": 95},   # nx_delta -5% → mild_kahou_nx
            {"Code": "12340", "CurPerType": "1Q", "DiscDate": "2025-08-01", "NP": 50, "NxFNp": 80},  # FY以外=除外
            {"Code": "12340", "CurPerType": "FY", "DiscDate": "2024-05-10", "NP": 0, "NxFNp": 10},   # NP=0=除外
        ]
        ev = nx_build_events(rows)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["band"], "mild_kahou_nx")
        self.assertEqual(ev[0]["code"], "1234")  # 5桁→4桁正規化
        self.assertAlmostEqual(ev[0]["nx_delta"], -5.0, places=2)

    def test_zouhai_kahou_nx_beta_short_cell_and_demean(self) -> None:
        """ショート net 計算と β=1 demean(個別−TOPIX)の結合を検証。"""
        # ショート net = -ret - 0.15。全日マイナス株価(=ショート利)で勝率100%。
        obs = [(f"2024-01-{i:02d}", -2.0) for i in range(1, 13)]  # n=12 ≥ MIN_CELL_N
        s = zk_short_cell(obs)
        self.assertEqual(s["n"], 12)
        self.assertAlmostEqual(s["net_ev"], 2.0 - 0.15, places=6)  # -(-2)-0.15
        self.assertEqual(s["win"], 100.0)
        self.assertIsNone(zk_short_cell(obs[:5]))  # n<MIN_CELL_N
        # build_rows: subpattern一致+大引け後+TOPIX同日でαが ret-TOPIX になる
        records = [{
            "subpattern": "zouhai_kahou_nx", "code": "70110", "event_date": "2024-01-04",
            "good_factors": [{"disc_time": "16:00:00"}],
            "attrs": {"next_day_open_to_close_ret": -3.0, "next_bar_date": "2024-01-05"},
        }]
        topix = {"2024-01-05": (100.0, 101.0)}  # TOPIX +1%
        scale = {"7011": "中型"}
        groups = zk_build_rows(records, topix, scale, "大引け後")
        self.assertEqual(groups["中型"]["raw"], [("2024-01-04", -3.0)])
        self.assertAlmostEqual(groups["中型"]["alpha"][0][1], -3.0 - 1.0, places=6)  # ret - TOPIX

    def test_mild_buyback_enrich(self) -> None:
        """同日 jisha 決定の ratio を mild record に添付 (edinet=decision_date / tdnet=event_date)。"""
        bb = {"records": [
            # edinet: 月次報告で複数行・decision_date が決議日
            {"code": "21630", "event_date": "2026-03-31", "decision_date": "2026-03-13",
             "buyback_ratio_pct": 0.282, "source": "edinet"},
            {"code": "21630", "event_date": "2026-04-30", "decision_date": "2026-03-13",
             "buyback_ratio_pct": 0.282, "source": "edinet"},
            # tdnet: decision_date 無し、event_date=開示日
            {"code": "4318", "event_date": "2026-04-30", "buyback_ratio_pct": 2.65, "source": "tdnet"},
        ]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(bb, f)
            bb_path = Path(f.name)
        try:
            m = load_buyback_decision_map(bb_path)
        finally:
            bb_path.unlink()
        self.assertEqual(m[("2163", "2026-03-13")], (0.282, "edinet"))  # 5桁→4桁 + decision_date
        self.assertEqual(m[("4318", "2026-04-30")], (2.65, "tdnet"))    # tdnet=event_date

        # jisha record にヒット → ratio 付与
        rec = {"code": "2163", "event_date": "2026-03-13", "attrs": {"goods": ["jisha"], "np_yoy": -0.1}}
        self.assertTrue(mb_enrich_record(rec, m))
        self.assertEqual(rec["attrs"]["buyback_ratio_pct"], 0.282)
        self.assertEqual(rec["attrs"]["buyback_source"], "edinet")
        # 別日決定 → null (キッコーマン型: 決算日 ≠ 既存決定日)
        rec2 = {"code": "2801", "event_date": "2026-04-24", "attrs": {"goods": ["jisha"]}}
        self.assertTrue(mb_enrich_record(rec2, m))
        self.assertIsNone(rec2["attrs"]["buyback_ratio_pct"])
        self.assertIsNone(rec2["attrs"]["buyback_source"])
        # jisha 以外 → 触らない (no-op)
        rec3 = {"code": "2163", "event_date": "2026-03-13", "attrs": {"goods": ["split"]}}
        self.assertFalse(mb_enrich_record(rec3, m))
        self.assertNotIn("buyback_ratio_pct", rec3["attrs"])

    def test_chat_assistant_prompt(self) -> None:
        """判定プロンプトに①A2版・口座/デバイス・リストが入る。"""
        master = {"as_of": "2026-06-02", "records": [
            {"Code": "43850", "CoName": "メルカリ", "scale_band": "中型", "S17Nm": "情報通信・サービスその他", "MrgnNm": "信用"},
            {"Code": "89510", "CoName": "日本ビルファンド投資法人", "scale_band": "大型", "MrgnNm": "貸借"},
            {"Code": "45230", "CoName": "エーザイ", "scale_band": "中型", "S17Nm": "医薬品", "MrgnNm": "信用"},
        ]}
        p = chat_build_prompt(master)
        self.assertIn("4385 メルカリ", p)                       # 中型Mid400リスト
        self.assertIn("4523 エーザイ", p)                       # 医薬品×信用リスト
        self.assertIn("9:05〜9:15", p)                          # ①Aスキャル版の出口
        self.assertIn("持ち切り版", p)                          # ①A持ち切り版
        self.assertIn("楽天マーケットスピード", p)               # 口座/デバイス指針
        self.assertIn("日興スマホでも可", p)


if __name__ == "__main__":
    unittest.main()

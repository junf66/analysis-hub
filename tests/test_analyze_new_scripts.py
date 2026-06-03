"""Smoke tests for newly added analysis scripts."""
from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()

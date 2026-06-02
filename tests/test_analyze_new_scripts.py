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


if __name__ == "__main__":
    unittest.main()

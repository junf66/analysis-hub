"""Smoke tests for newly added analysis scripts."""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.analyze_split_gu_filter import load_data as split_load_data
from scripts.analyze_split_gu_filter import build_report as split_build_report
from scripts.analyze_po_edge1_opportunity import load_po_records, load_equities_master
from scripts.analyze_po_edge1_opportunity import build_report as po_build_report
from scripts.analyze_kouaku_magnitude_robustness import build_report as mag_build_report
from scripts.analyze_split_size_definition import load_data as size_load_data
from scripts.analyze_split_size_definition import build_report as size_build_report


class TestAnalyzeNewScripts(unittest.TestCase):
    """Smoke tests to verify new analysis scripts run without error."""

    def test_split_gu_filter_loads_and_reports(self) -> None:
        """Test that split GU filter script loads data and builds report."""
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
        records = size_load_data()
        self.assertIsInstance(records, list)
        self.assertGreater(len(records), 0)
        report = size_build_report(records)
        self.assertIn("小型", report)
        self.assertIn("中型", report)
        self.assertIn("+2.13%", report)


if __name__ == "__main__":
    unittest.main()

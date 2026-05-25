"""全 scripts module が import できる + --help が exit 0 で返ることを最小確認。

CLI 引数のタイポ・format string バグ等を早期発見する。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestImportable(unittest.TestCase):
    """import が syntax error / missing dep で死なないこと。"""

    def _check(self, mod: str) -> None:
        # 副作用ありの main() は触らず、module import + 属性存在を assert
        import importlib
        m = importlib.import_module(mod)
        self.assertTrue(hasattr(m, "main") or mod.endswith("_buckets"),
                        f"{mod}: main() がない (CLI module の規約違反)")

    def test_fetch_disclosures(self) -> None:
        self._check("scripts.fetch_disclosures")

    def test_extract_mixed_disclosures(self) -> None:
        self._check("scripts.extract_mixed_disclosures")

    def test_enrich_price_kouaku(self) -> None:
        self._check("scripts.enrich_price_kouaku")

    def test_analyze_kouaku_edge(self) -> None:
        self._check("scripts.analyze_kouaku_edge")

    def test_backtest_kouaku(self) -> None:
        self._check("scripts.backtest_kouaku")

    def test_query_kouaku(self) -> None:
        self._check("scripts.query_kouaku")

    def test_data_health(self) -> None:
        self._check("scripts.data_health")

    def test_update_all(self) -> None:
        self._check("scripts.update_all")

    def test_noon_disclosure_experiment(self) -> None:
        self._check("scripts.noon_disclosure_experiment")

    def test_audit_all(self) -> None:
        self._check("scripts.audit_all")

    def test_buckets_module(self) -> None:
        self._check("scripts._buckets")

    def test_extract_po(self) -> None:
        self._check("scripts.extract_po")

    def test_analyze_po_edge(self) -> None:
        self._check("scripts.analyze_po_edge")

    def test_backtest_po(self) -> None:
        self._check("scripts.backtest_po")

    def test_extract_holdings(self) -> None:
        self._check("scripts.extract_holdings")

    def test_analyze_holdings_edge(self) -> None:
        self._check("scripts.analyze_holdings_edge")

    def test_backtest_holdings(self) -> None:
        self._check("scripts.backtest_holdings")

    def test_query_po(self) -> None:
        self._check("scripts.query_po")

    def test_query_holdings(self) -> None:
        self._check("scripts.query_holdings")


class TestHelpExitsCleanly(unittest.TestCase):
    """全 CLI が --help で exit 0 (argparse の format-string バグ等を検出)。"""

    def _run_help(self, mod: str) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", mod, "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, f"{mod} --help failed: {proc.stderr.decode()[:300]}")
        self.assertIn(b"usage:", proc.stdout.lower() if proc.stdout else b"")

    def test_fetch_disclosures(self) -> None:
        self._run_help("scripts.fetch_disclosures")

    def test_extract_mixed_disclosures(self) -> None:
        self._run_help("scripts.extract_mixed_disclosures")

    def test_enrich_price_kouaku(self) -> None:
        self._run_help("scripts.enrich_price_kouaku")

    def test_analyze_kouaku_edge(self) -> None:
        self._run_help("scripts.analyze_kouaku_edge")

    def test_backtest_kouaku(self) -> None:
        self._run_help("scripts.backtest_kouaku")

    def test_query_kouaku(self) -> None:
        self._run_help("scripts.query_kouaku")

    def test_data_health(self) -> None:
        self._run_help("scripts.data_health")

    def test_update_all(self) -> None:
        self._run_help("scripts.update_all")

    def test_noon_disclosure_experiment(self) -> None:
        self._run_help("scripts.noon_disclosure_experiment")

    def test_extract_po(self) -> None:
        self._run_help("scripts.extract_po")

    def test_analyze_po_edge(self) -> None:
        self._run_help("scripts.analyze_po_edge")

    def test_backtest_po(self) -> None:
        self._run_help("scripts.backtest_po")

    def test_extract_holdings(self) -> None:
        self._run_help("scripts.extract_holdings")

    def test_analyze_holdings_edge(self) -> None:
        self._run_help("scripts.analyze_holdings_edge")

    def test_backtest_holdings(self) -> None:
        self._run_help("scripts.backtest_holdings")

    def test_query_po(self) -> None:
        self._run_help("scripts.query_po")

    def test_query_holdings(self) -> None:
        self._run_help("scripts.query_holdings")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""audit_pbo の PBO/CSCV が過学習を弁別することのテスト。"""
import random
import unittest

from scripts.edge_candidates.audit_pbo import combinatorial_pbo, pbo_from_cells


class TestPBO(unittest.TestCase):
    def test_noise_high_robust_low(self):
        random.seed(1)
        t, n = 240, 30
        noise = [[random.gauss(0, 1) for _ in range(t)] for _ in range(n)]
        signal = [c[:] for c in noise]
        signal[0] = [random.gauss(0.5, 1) for _ in range(t)]
        pbo_noise = combinatorial_pbo(noise, n_splits=12)["pbo"]
        pbo_robust = combinatorial_pbo(signal, n_splits=12)["pbo"]
        self.assertGreater(pbo_noise, 0.4)          # 純ノイズは過学習(高PBO)
        self.assertLess(pbo_robust, pbo_noise)      # 真の信号入りは弁別され低下

    def test_degenerate(self):
        import math
        self.assertTrue(math.isnan(combinatorial_pbo([[1.0, 2.0]], n_splits=4)["pbo"]))  # n<2はnan安全

    def test_from_cells(self):
        cells = {"a": [("2020-01", 0.1), ("2020-02", -0.2)], "b": [("2020-01", 0.0), ("2020-02", 0.3)]}
        r = pbo_from_cells(cells, n_splits=4)
        self.assertIn("pbo", r)


if __name__ == "__main__":
    unittest.main()

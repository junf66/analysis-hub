"""analyzers/stats.py の統計関数の正しさを検証 (精度ツールは数値が正しくないと無価値)。"""
from __future__ import annotations

import statistics
import unittest

from analyzers import stats


class TestTtoP(unittest.TestCase):
    def test_known_values(self) -> None:
        self.assertAlmostEqual(stats.t_to_p(0.0), 1.0, places=6)
        self.assertAlmostEqual(stats.t_to_p(1.959964), 0.05, places=3)   # 5%
        self.assertAlmostEqual(stats.t_to_p(2.575829), 0.01, places=3)   # 1%
        self.assertEqual(stats.t_to_p(5.0), stats.t_to_p(-5.0))          # 両側対称


class TestBenjaminiHochberg(unittest.TestCase):
    def test_basic(self) -> None:
        # 最大 k で p(k)<=(k/m)*0.05 を満たすのは rank2 (0.008<=0.025) まで
        out = stats.benjamini_hochberg([0.001, 0.008, 0.04, 0.6], alpha=0.05)
        self.assertEqual(out, [True, True, False, False])

    def test_preserves_input_order(self) -> None:
        out = stats.benjamini_hochberg([0.6, 0.001, 0.04, 0.008], alpha=0.05)
        self.assertEqual(out, [False, True, False, True])

    def test_empty(self) -> None:
        self.assertEqual(stats.benjamini_hochberg([]), [])

    def test_none_significant(self) -> None:
        self.assertEqual(stats.benjamini_hochberg([0.9, 0.8, 0.7]), [False, False, False])


class TestClusteredSE(unittest.TestCase):
    def test_distinct_clusters_equals_naive(self) -> None:
        # 全観測が別クラスタなら clustered SE = 素朴 SE
        vals = [1.0, 2.0, 4.0, 3.0, 5.0]
        clusters = ["a", "b", "c", "d", "e"]
        naive = statistics.stdev(vals) / (len(vals) ** 0.5)
        self.assertAlmostEqual(stats.clustered_se(vals, clusters), naive, places=9)

    def test_clustering_inflates_se(self) -> None:
        # 同一クラスタ内で相関 → SE は素朴より大きい
        vals = [1.0, 1.0, 3.0, 3.0]
        clusters = ["A", "A", "B", "B"]
        cse = stats.clustered_se(vals, clusters)
        naive = statistics.stdev(vals) / (len(vals) ** 0.5)
        self.assertAlmostEqual(cse, 1.0, places=6)   # 手計算: sqrt(2*8/16)=1
        self.assertGreater(cse, naive)


class TestEvaluateCells(unittest.TestCase):
    def _obs(self, cell, rets, code="7203"):
        return [{"cell": cell, "ret": r, "date": f"2026-01-{i+1:02d}", "code": code}
                for i, r in enumerate(rets)]

    def test_direction_and_net(self) -> None:
        obs = self._obs("X", [-2.0, -1.0, -3.0, -2.0, -1.5, -0.5])  # 平均負 → short
        res = stats.evaluate_cells(obs, cost_pct=0.2, min_n=5)
        self.assertEqual(len(res), 1)
        r = res[0]
        self.assertEqual(r["direction"], "short")
        # net 平均 = mean(-ret) - cost = 1.6667 - 0.2 = 1.4667
        self.assertAlmostEqual(r["ev_net"], statistics.fmean([2.0,1.0,3.0,2.0,1.5,0.5]) - 0.2, places=4)
        self.assertIn("fdr_significant", r)
        self.assertIn("robust_oos", r)

    def test_min_n_filter(self) -> None:
        res = stats.evaluate_cells(self._obs("X", [1.0, 2.0, 3.0]), min_n=5)
        self.assertEqual(res, [])

    def test_walk_forward_split(self) -> None:
        obs = self._obs("X", [1.0]*10)  # 全部同じ → train 方向で test も同符号
        res = stats.evaluate_cells(obs, cost_pct=0.0, split_frac=0.7, min_n=5)
        r = res[0]
        self.assertEqual(r["train_n"], 7)
        self.assertEqual(r["test_n"], 3)
        self.assertTrue(r["robust_oos"])

    def test_fdr_applied_across_cells(self) -> None:
        # 強いセル (一貫して負、適度なばらつき) と弱いセル (ノイズ)。FDR で強い方が生存。
        strong_rets = [-3.0 + (0.5 if i % 2 else -0.5) for i in range(30)]  # mean -3, stdev 0.5
        strong = self._obs("strong", strong_rets, code="1111")
        weak = [{"cell": "weak", "ret": v, "date": f"2026-02-{i+1:02d}", "code": "2222"}
                for i, v in enumerate([3.0, -3.0, 2.5, -2.5, 1.0, -1.0])]  # 大ばらつき・平均~0
        res = stats.evaluate_cells(strong + weak, cost_pct=0.2, min_n=5)
        bycell = {r["cell"]: r for r in res}
        self.assertTrue(bycell["strong"]["fdr_significant"])   # 明確なエッジは生存
        self.assertFalse(bycell["weak"]["fdr_significant"])    # ノイズは棄却


if __name__ == "__main__":
    unittest.main()

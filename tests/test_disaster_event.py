"""disaster_event パッケージの純ロジック単体テスト (ネットワーク不要)。

ベストトラック固定長パース・台風抽出基準・戦略評価/判定を、
小さなインメモリ fixture でロックする。
"""
from __future__ import annotations

import unittest

from scripts.disaster_event import (
    analyze_typhoon_edge as A,
    fetch_typhoon_data as F,
    identify_typhoons as I,
)

# 固定長ベストトラック (66666 ヘッダ + 002 データ行)。35.0N/139.0E=東京近傍, 915hPa, 35kt。
SAMPLE_BST = "\n".join([
    "66666 1919  002 0025 1919 1 0              HAGIBIS              20191129        ",
    "19100512 002 5 340 1410 0930     030                                            ",
    "19100518 002 5 350 1390 0915     035                                            ",
])


class TestParse(unittest.TestCase):
    def test_parse_columns(self) -> None:
        storms = F.parse_best_track(SAMPLE_BST)
        self.assertEqual(len(storms), 1)
        s = storms[0]
        self.assertEqual(s["intl"], "1919")
        self.assertEqual(s["name"], "HAGIBIS")
        self.assertEqual(s["year"], "2019")
        self.assertEqual(len(s["points"]), 2)
        p = s["points"][1]
        self.assertEqual(p["grade"], "5")
        self.assertAlmostEqual(p["lat"], 35.0)
        self.assertAlmostEqual(p["lon"], 139.0)
        self.assertEqual(p["pressure"], 915)
        self.assertEqual(p["wind"], 35)

    def test_yy_to_year(self) -> None:
        self.assertEqual(F._yy_to_year(19), 2019)
        self.assertEqual(F._yy_to_year(51), 1951)
        self.assertEqual(F._yy_to_year(99), 1999)

    def test_select_period(self) -> None:
        storms = F.parse_best_track(SAMPLE_BST)
        self.assertEqual(len(F.select_period(storms, 2016, 2025)), 1)
        self.assertEqual(len(F.select_period(storms, 2020, 2025)), 0)


class TestIdentify(unittest.TestCase):
    def test_big_typhoon_selected(self) -> None:
        storms = F.parse_best_track(SAMPLE_BST)
        events = I.identify(storms)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["near_min_pressure"], 915)
        self.assertTrue(e["landfall_like"])
        # 最接近点 19100518 = 2019-10-05 18:00 UTC → JST +9h = 2019-10-06 03:00
        self.assertEqual(e["event_date"], "2019-10-06")
        self.assertEqual(e["case_study"], I.CASE_STUDIES["1919"])

    def test_far_storm_excluded(self) -> None:
        # 経度 119.0 (台湾以西), 気圧 915 でも本土ボックス外 → 対象外
        far = SAMPLE_BST.replace("1390", "1190").replace("1410", "1190")
        events = I.identify(F.parse_best_track(far))
        self.assertEqual(events, [])

    def test_weak_typhoon_excluded(self) -> None:
        # 近傍最低気圧 985hPa & 風 30kt → 大型基準 (≤960 / ≥68kt) 未達
        weak = SAMPLE_BST.replace("0915", "0985").replace("0930", "0985")
        weak = weak.replace(" 035 ", " 030 ")
        events = I.identify(F.parse_best_track(weak))
        self.assertEqual(events, [])


class TestEvaluate(unittest.TestCase):
    def _obs(self, rets_list):
        return [{"intl": f"t{i}", "rets": r} for i, r in enumerate(rets_list)]

    def test_long_pass(self) -> None:
        # 平均+2%・全件プラス・小分散 (各台風1件) の post3 long → net+1.8%/勝率100%/t大
        vals = [1.5, 2.0, 2.5] * 9  # 27件, 全て正
        obs = self._obs([{"post3": v} for v in vals])
        play = {"label": "x", "win": "post3", "dir": "long", "cost": A.LONG_COST}
        r = A.eval_play(obs, play)
        self.assertAlmostEqual(r["ev_net"], 2.0 - A.LONG_COST, places=6)
        self.assertEqual(r["win"], 100.0)
        self.assertGreater(r["t_clust"], A.PASS_T)
        self.assertEqual(r["verdict"], "通過候補")

    def test_short_direction_and_reject(self) -> None:
        obs = self._obs([{"post3": 2.0} for _ in range(25)])
        play = {"label": "x", "win": "post3", "dir": "short", "cost": A.SHORT_COST}
        r = A.eval_play(obs, play)
        # +2% を short → 方向損益 -2% - cost → 大幅マイナス, 却下
        self.assertLess(r["ev_net"], 0)
        self.assertEqual(r["verdict"], "却下")

    def test_inago_condition_filters(self) -> None:
        # hit が +3% 以上の観測だけ拾う
        obs = [{"intl": "a", "rets": {"hit": 5.0, "post1": -1.0}},
               {"intl": "b", "rets": {"hit": 1.0, "post1": -1.0}}]
        self.assertTrue(A._passes_cond(obs[0], "inago"))
        self.assertFalse(A._passes_cond(obs[1], "inago"))

    def test_net_helper(self) -> None:
        self.assertAlmostEqual(A._net(2.0, "long", 0.2), 1.8)
        self.assertAlmostEqual(A._net(2.0, "short", 0.15), -2.15)


if __name__ == "__main__":
    unittest.main()

"""export_kouaku_site の slim JSON 生成ロック (in-memory、外部 I/O なし)。

検証:
  - build_edges: 方向判定 / net 統計 / backtest 一致 / n<5・limit-lock 除外 / confidence
  - build_events: 直近降順・件数制限・good/bad ラベル・最早 disc_time
  - build_site_payload: meta 構造とデータ期間

実行:
  python -m unittest tests.test_export_kouaku_site -v
"""
from __future__ import annotations

import statistics
import unittest

from scripts import backtest_kouaku
from scripts import export_kouaku_site


def _factor(hint: str, *, disc_time: str, reason: str = "", title: str = "") -> dict:
    return {
        "subpattern_hint": hint,
        "disc_time": disc_time,
        "reason": reason,
        "title": title,
        "disc_no": "x",
        "metric": {},
    }


def _rec(
    *,
    code: str,
    event_date: str,
    subpattern: str,
    open_to_close: float | None,
    disc_time: str = "13:00:00",  # 既定は 場中
    limit_locked: bool = False,
    gap_pct: float | None = None,
    good_reason: str = "好材料",
    bad_reason: str = "悪材料",
) -> dict:
    attrs: dict = {"limit_locked": limit_locked}
    if open_to_close is not None:
        attrs["next_day_open_to_close_ret"] = open_to_close
    if gap_pct is not None:
        attrs["gap_pct"] = gap_pct
    return {
        "id": f"kouaku:{code}:{event_date}",
        "code": code,
        "event_date": event_date,
        "event_type": "kouaku_mixed",
        "subpattern": subpattern,
        "good_factors": [_factor("good", disc_time=disc_time, reason=good_reason)],
        "bad_factors": [_factor("bad", disc_time=disc_time, reason=bad_reason)],
        "attrs": attrs,
    }


class TestBuildEdges(unittest.TestCase):
    def test_short_direction_and_net_match_backtest(self) -> None:
        # 場中・負 EV のセル → short 方向。net 統計が backtest と一致するか。
        rets = [-2.0, -1.0, -3.0, 1.0, -2.5, -0.5]  # mean<0 → short
        recs = [
            _rec(code="1000", event_date=f"2025-01-0{i+1}",
                 subpattern="kouhou_genshu", open_to_close=v)
            for i, v in enumerate(rets)
        ]
        cost = 0.20
        edges = export_kouaku_site.build_edges(recs, cost_pct=cost)
        self.assertEqual(len(edges), 1)
        e = edges[0]
        self.assertEqual(e["direction"], "short")
        self.assertEqual(e["n"], len(rets))

        # backtest と同一の net 計算で突き合わせ
        nets = [backtest_kouaku._net_pnl(v, cost, "short") for v in rets]
        self.assertAlmostEqual(e["ev_net_pct"], round(statistics.fmean(nets), 4), places=4)
        self.assertAlmostEqual(e["cumul_pct"], round(sum(nets), 2), places=2)
        # ev_pct は cost 前 (= ev_net + cost)
        self.assertAlmostEqual(e["ev_pct"], round(statistics.fmean(nets) + cost, 4), places=4)
        # raw_ev_pct は long 視点の符号付き生 EV
        self.assertAlmostEqual(e["raw_ev_pct"], round(statistics.fmean(rets), 4), places=4)

    def test_long_direction_for_positive_cell(self) -> None:
        rets = [2.0, 1.0, 3.0, 0.5, 1.5]  # mean>0 → long
        recs = [
            _rec(code="2000", event_date=f"2025-02-0{i+1}",
                 subpattern="jisha_genshu", open_to_close=v)
            for i, v in enumerate(rets)
        ]
        edges = export_kouaku_site.build_edges(recs, cost_pct=0.0)
        self.assertEqual(edges[0]["direction"], "long")
        self.assertAlmostEqual(edges[0]["ev_net_pct"], round(statistics.fmean(rets), 4), places=4)

    def test_excludes_small_cells_and_locked_and_null(self) -> None:
        recs = []
        # n=4 のセル (MIN_CELL_N=5 未満) → 除外
        for i in range(4):
            recs.append(_rec(code="3000", event_date=f"2025-03-0{i+1}",
                             subpattern="tob_genshu", open_to_close=-1.0))
        # limit_locked と metric=None は edges から除外され、有効 4 件のみ → やはり <5
        recs.append(_rec(code="3000", event_date="2025-03-10",
                         subpattern="tob_genshu", open_to_close=-1.0, limit_locked=True))
        recs.append(_rec(code="3000", event_date="2025-03-11",
                         subpattern="tob_genshu", open_to_close=None))
        edges = export_kouaku_site.build_edges(recs, cost_pct=0.20)
        self.assertEqual(edges, [])

    def test_confidence_buckets(self) -> None:
        self.assertEqual(export_kouaku_site._confidence(5), "low")
        self.assertEqual(export_kouaku_site._confidence(29), "low")
        self.assertEqual(export_kouaku_site._confidence(30), "mid")
        self.assertEqual(export_kouaku_site._confidence(99), "mid")
        self.assertEqual(export_kouaku_site._confidence(100), "high")

    def test_sorted_by_t_desc(self) -> None:
        recs = []
        # 強いセル (大きく一方向)
        for i in range(8):
            recs.append(_rec(code="4000", event_date=f"2025-04-0{i+1}",
                             subpattern="zouhai_genshu", open_to_close=-3.0))
        # 弱いセル (ばらつき大)
        for i, v in enumerate([0.1, -0.2, 0.3, -0.1, 0.2, -0.3, 0.05]):
            recs.append(_rec(code="5000", event_date=f"2025-05-0{i+1}",
                             subpattern="jisha_kahou", open_to_close=v))
        edges = export_kouaku_site.build_edges(recs, cost_pct=0.0)
        ts = [e["t"] for e in edges]
        self.assertEqual(ts, sorted(ts, reverse=True))


class TestBuildEvents(unittest.TestCase):
    def test_recent_desc_and_limit_and_labels(self) -> None:
        recs = [
            _rec(code="7203", event_date="2025-01-01", subpattern="kouhou_genshu",
                 open_to_close=-1.0, disc_time="14:00:00", good_reason="増配", bad_reason="公募増資"),
            _rec(code="6758", event_date="2025-03-15", subpattern="jisha_genshu",
                 open_to_close=2.0, disc_time="10:00:00"),
            _rec(code="9984", event_date="2024-12-31", subpattern="tob_genshu",
                 open_to_close=0.5),
        ]
        events = export_kouaku_site.build_events(recs, limit=2)
        self.assertEqual(len(events), 2)
        # 降順 (最新が先頭)
        self.assertEqual(events[0]["date"], "2025-03-15")
        self.assertEqual(events[1]["date"], "2025-01-01")
        # good/bad ラベルと最早 disc_time
        self.assertEqual(events[1]["good"], "増配")
        self.assertEqual(events[1]["bad"], "公募増資")
        self.assertEqual(events[1]["disc_time"], "14:00:00")
        self.assertEqual(events[1]["disc_time_bucket"], "場中")

    def test_earliest_disc_time_across_factors(self) -> None:
        rec = _rec(code="1234", event_date="2025-06-01", subpattern="kouhou_genshu",
                   open_to_close=1.0, disc_time="11:30:00")
        rec["bad_factors"][0]["disc_time"] = "09:05:00"  # こちらが最早
        events = export_kouaku_site.build_events([rec], limit=10)
        self.assertEqual(events[0]["disc_time"], "09:05:00")
        self.assertEqual(events[0]["disc_time_bucket"], "寄り中")


class TestBuildSitePayload(unittest.TestCase):
    def test_meta_and_structure(self) -> None:
        recs = [
            _rec(code="1000", event_date="2021-05-24", subpattern="kouhou_genshu",
                 open_to_close=-1.0),
            _rec(code="1000", event_date="2026-05-21", subpattern="kouhou_genshu",
                 open_to_close=-2.0),
        ]
        payload = {"records": recs}
        site = export_kouaku_site.build_site_payload(
            payload, cost_pct=0.20, recent_limit=50, last_updated="2026-05-25")
        self.assertEqual(set(site.keys()), {"meta", "edges", "events"})
        meta = site["meta"]
        self.assertEqual(meta["schema_version"], export_kouaku_site.SCHEMA_VERSION)
        self.assertEqual(meta["last_updated"], "2026-05-25")
        self.assertEqual(meta["total_events"], 2)
        self.assertEqual(meta["data_window"], ["2021-05-24", "2026-05-21"])
        self.assertEqual(meta["cost_assumption_pct"], 0.20)
        self.assertEqual(meta["primary_metric"], export_kouaku_site.PRIMARY_METRIC)

    def test_empty_records(self) -> None:
        site = export_kouaku_site.build_site_payload({"records": []})
        self.assertEqual(site["edges"], [])
        self.assertEqual(site["events"], [])
        self.assertEqual(site["meta"]["data_window"], [None, None])


if __name__ == "__main__":
    unittest.main()

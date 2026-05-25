"""holdings パイプライン (extract → analyze → backtest) の E2E ロック (in-memory)。

外部 fetch は叩かない。holdings 生 record → 共通スキーマ展開 → 分析 → バックテストまで。

実行:
  python -m unittest tests.test_holdings_pipeline -v
"""
from __future__ import annotations

import unittest

from scripts import analyze_holdings_edge
from scripts import backtest_holdings
from scripts import extract_holdings


def _raw(
    *,
    rid: str,
    code: str,
    event_date: str,
    purpose_jp: str = "純投資",
    holder_jp: str = "外資ファンド",
    open_to_close: float | None = 1.0,
    gap_pct: float | None = -0.5,
    event_type: str = "holdings_filing",
    low_ratio_suspect: bool = False,
    holding_ratio: float = 6.0,
) -> dict:
    r = {
        "id": rid,
        "code": code,
        "event_date": event_date,
        "event_type": event_type,
        "source": "edinet",
        "purpose_category": "investment",
        "purpose_category_jp": purpose_jp,
        "holder_category": "foreign_fund",
        "holder_category_jp": holder_jp,
        "holding_ratio": holding_ratio,
        "has_joint_holders": False,
        "filer_freq_180d": 0,
        "low_ratio_suspect": low_ratio_suspect,
        "gap_label": "GD" if (gap_pct or 0) < 0 else "GU",
        "market": "プライム",
    }
    if open_to_close is not None:
        r["open_to_close_pct"] = open_to_close
    if gap_pct is not None:
        r["gap_pct"] = gap_pct
    r["open_price"] = 100.0
    r["close_price"] = 100.0 + (open_to_close or 0)
    return r


class TestExtract(unittest.TestCase):
    def test_expand_record_maps_price_and_dims(self) -> None:
        ev = list(extract_holdings.expand_record(_raw(rid="A", code="7203", event_date="2026-01-05")))
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertEqual(e["id"], "holdings:A")
        self.assertEqual(e["code"], "7203")
        self.assertEqual(e["purpose_category_jp"], "純投資")
        self.assertEqual(e["holder_category_jp"], "外資ファンド")
        # 価格は kouaku 互換命名に正規化
        self.assertEqual(e["attrs"]["next_day_open_to_close_ret"], 1.0)
        self.assertEqual(e["attrs"]["gap_pct"], -0.5)

    def test_invalid_event_type_and_missing_code_dropped(self) -> None:
        self.assertEqual(list(extract_holdings.expand_record(_raw(rid="B", code="1", event_date="2026-01-05", event_type="not_holdings"))), [])
        bad = _raw(rid="C", code="", event_date="2026-01-05")
        self.assertEqual(list(extract_holdings.expand_record(bad)), [])

    def test_expand_all_and_build_payload(self) -> None:
        raws = [
            _raw(rid=f"R{i}", code="7203", event_date=f"2026-01-0{i+1}")
            for i in range(3)
        ]
        events = extract_holdings.expand_all(raws)
        self.assertEqual(len(events), 3)
        payload = extract_holdings.build_payload({"records": raws, "last_updated": "x"})
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["count_raw"], 3)
        self.assertIn("純投資", payload["purpose_counts"])


class TestAnalyze(unittest.TestCase):
    def _events(self) -> list[dict]:
        raws = [_raw(rid=f"R{i}", code="7203", event_date=f"2026-02-0{i+1}", open_to_close=1.0) for i in range(6)]
        raws.append(_raw(rid="S", code="9999", event_date="2026-02-09", low_ratio_suspect=True, open_to_close=99.0))
        return extract_holdings.expand_all(raws)

    def test_eligibility_excludes_suspect(self) -> None:
        events = self._events()
        elig = [e for e in events if analyze_holdings_edge.is_eligible_for_ev(e)]
        self.assertEqual(len(elig), 6)  # suspect 1 件除外

    def test_cross_cells_and_report(self) -> None:
        events = self._events()
        cells = analyze_holdings_edge.cross_cells(events)
        self.assertIn(("純投資", "外資ファンド"), cells)
        # suspect は cross_cells から除外されている
        self.assertEqual(len(cells[("純投資", "外資ファンド")]), 6)
        md = analyze_holdings_edge.build_report({
            "records": events, "purpose_counts": {}, "holder_counts": {},
        })
        self.assertIn("大量保有エッジ検証", md)
        self.assertIn("純投資", md)


class TestBacktest(unittest.TestCase):
    def test_direction_and_net_cost(self) -> None:
        # 負 EV セル → short 方向 / net は cost 控除
        raws = [_raw(rid=f"N{i}", code="7203", event_date=f"2026-03-0{i+1}", open_to_close=-2.0) for i in range(8)]
        events = extract_holdings.expand_all(raws)
        md = backtest_holdings.build_report(events, cost_pct=0.20)
        self.assertIn("大量保有 バックテスト", md)
        self.assertIn("short", md)
        # 強度ランキングが存在
        self.assertIn("強度ランキング", md)

    def test_min_cell_n_excludes_small(self) -> None:
        raws = [_raw(rid=f"M{i}", code="7203", event_date=f"2026-04-0{i+1}", open_to_close=1.0) for i in range(3)]
        events = extract_holdings.expand_all(raws)
        md = backtest_holdings.build_report(events, cost_pct=0.20)
        # n=3 < MIN_CELL_N=5 なので本体テーブルに cell 行は出ない (ヘッダのみ)
        self.assertNotIn("| 純投資 | 外資ファンド | ", md)


if __name__ == "__main__":
    unittest.main()

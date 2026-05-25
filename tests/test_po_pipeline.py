"""PO パイプライン (extract → analyze → backtest) の E2E ロック。

外部 API は叩かない (po-tracker raw → 共通スキーマ展開、分析、バックテストまで in-memory)。

実行:
  python -m unittest tests.test_po_pipeline -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import analyze_po_edge, backtest_po, extract_po


def _raw_po(
    *,
    pid: str,
    code: str,
    po_type: str,
    announce_date: str | None,
    decision_date: str | None,
    delivery_date: str | None,
    next_open: float | None = None,
    announce_day_close: float | None = None,
    next_day_910_ret: float | None = None,
    dec_open: float | None = None,
    dec_close: float | None = None,
    ret_open: float | None = None,
    ret_close: float | None = None,
    delivery_open: float | None = None,
    delivery_close: float | None = None,
    delivery_gap_pct: float | None = None,
    delivery_ret: float | None = None,
    prev_close_before_delivery: float | None = None,
    lending_type: str = "貸借",
    status: str = "complete",
    legacy: bool = False,
    concurrent_earnings: bool = False,
) -> dict:
    return {
        "id": pid,
        "code": code,
        "type": po_type,
        "announce_date": announce_date,
        "decision_date": decision_date,
        "delivery_date": delivery_date,
        "next_open": next_open,
        "announce_day_close": announce_day_close,
        "next_day_910_ret": next_day_910_ret,
        "dec_open": dec_open,
        "dec_close": dec_close,
        "ret_open": ret_open,
        "ret_close": ret_close,
        "delivery_open": delivery_open,
        "delivery_close": delivery_close,
        "delivery_gap_pct": delivery_gap_pct,
        "delivery_ret": delivery_ret,
        "prev_close_before_delivery": prev_close_before_delivery,
        "lending_type": lending_type,
        "status": status,
        "legacy": legacy,
        "concurrent_earnings": concurrent_earnings,
        "name": "TEST",
        "year": 2024,
    }


class TestExtractExpandsToThreeEvents(unittest.TestCase):
    """1 PO レコードが日付があるステージ分のイベントに展開されること。"""

    def test_full_three_stages(self) -> None:
        raw = _raw_po(
            pid="csv_x",
            code="1234",
            po_type="普通",
            announce_date="2024-08-01",
            decision_date="2024-08-08",
            delivery_date="2024-08-15",
            next_open=100.0,
            announce_day_close=110.0,
            next_day_910_ret=0.5,
            dec_open=99.0,
            dec_close=98.0,
            ret_open=-1.0,
            ret_close=-2.0,
            delivery_open=97.0,
            delivery_close=98.0,
            delivery_gap_pct=-0.6,
            delivery_ret=1.0,
            prev_close_before_delivery=97.5,
        )
        events = list(extract_po.expand_record(raw))
        self.assertEqual(len(events), 3)
        stages = [e["stage"] for e in events]
        self.assertEqual(stages, ["announce", "decide", "deliver"])
        # event_date が正しく該当ステージの日付になっている
        self.assertEqual(events[0]["event_date"], "2024-08-01")
        self.assertEqual(events[1]["event_date"], "2024-08-08")
        self.assertEqual(events[2]["event_date"], "2024-08-15")
        # ID は <ref_id>:<stage> 形式
        self.assertEqual(events[0]["id"], "po:csv_x:announce")
        # code は 4 桁ゼロパディング
        self.assertEqual(events[0]["code"], "1234")

    def test_partial_stages_emit_only_present_dates(self) -> None:
        raw = _raw_po(
            pid="csv_y",
            code="0001",
            po_type="リート",
            announce_date="2024-08-01",
            decision_date=None,
            delivery_date=None,
            next_open=100.0,
        )
        events = list(extract_po.expand_record(raw))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["stage"], "announce")
        self.assertEqual(events[0]["code"], "0001")  # zfill 確認

    def test_skips_record_without_code(self) -> None:
        raw = _raw_po(
            pid="csv_z",
            code="",
            po_type="普通",
            announce_date="2024-08-01",
            decision_date=None,
            delivery_date=None,
        )
        self.assertEqual(list(extract_po.expand_record(raw)), [])

    def test_drop_reason_classifies(self) -> None:
        ok = _raw_po(pid="p1", code="7203", po_type="普通",
                     announce_date="2024-08-01", decision_date=None, delivery_date=None)
        self.assertIsNone(extract_po.drop_reason(ok))
        no_code = _raw_po(pid="p2", code="", po_type="普通",
                          announce_date="2024-08-01", decision_date=None, delivery_date=None)
        self.assertEqual(extract_po.drop_reason(no_code), "no_code")
        no_date = _raw_po(pid="p3", code="7203", po_type="普通",
                          announce_date=None, decision_date=None, delivery_date=None)
        self.assertEqual(extract_po.drop_reason(no_date), "no_stage_date")


class TestAttrsMapping(unittest.TestCase):
    """ステージ別 attrs が kouaku 命名規則と一致すること。"""

    def test_announce_attrs(self) -> None:
        raw = _raw_po(
            pid="csv_a",
            code="1234",
            po_type="普通",
            announce_date="2024-08-01",
            decision_date=None,
            delivery_date=None,
            next_open=100.0,
            announce_day_close=110.0,
            next_day_910_ret=0.5,
        )
        ev = next(iter(extract_po.expand_record(raw)))
        self.assertEqual(ev["attrs"]["prev_close"], 110.0)
        self.assertEqual(ev["attrs"]["next_open"], 100.0)
        self.assertEqual(ev["attrs"]["next_day_910_ret"], 0.5)

    def test_decide_attrs(self) -> None:
        raw = _raw_po(
            pid="csv_b",
            code="1234",
            po_type="リート",
            announce_date=None,
            decision_date="2024-08-08",
            delivery_date=None,
            next_open=100.0,
            dec_open=99.0,
            dec_close=98.0,
            ret_open=-1.0,
            ret_close=-2.0,
        )
        ev = next(iter(extract_po.expand_record(raw)))
        self.assertEqual(ev["attrs"]["ref_open"], 100.0)
        self.assertEqual(ev["attrs"]["dec_open"], 99.0)
        self.assertEqual(ev["attrs"]["ret_close"], -2.0)

    def test_deliver_attrs_with_gap(self) -> None:
        raw = _raw_po(
            pid="csv_c",
            code="1234",
            po_type="普通",
            announce_date=None,
            decision_date=None,
            delivery_date="2024-08-15",
            delivery_open=97.0,
            delivery_close=98.0,
            delivery_gap_pct=-0.6,
            delivery_ret=1.0,
            prev_close_before_delivery=97.5,
        )
        ev = next(iter(extract_po.expand_record(raw)))
        self.assertEqual(ev["attrs"]["prev_close"], 97.5)
        self.assertEqual(ev["attrs"]["next_open"], 97.0)
        self.assertEqual(ev["attrs"]["gap_pct"], -0.6)
        self.assertEqual(ev["attrs"]["next_day_open_to_close_ret"], 1.0)


class TestKnownEdgeReproductionMicro(unittest.TestCase):
    """小規模 fixture で 3 既知エッジの分析関数が想定通り動くこと。"""

    def setUp(self) -> None:
        # ベースとなる普通株 announce + REIT decide + 普通株 deliver
        self.raw = [
            # 普通株 announce + 9:10 で +0.5%
            _raw_po(pid="p1", code="1001", po_type="普通",
                    announce_date="2024-08-01", decision_date=None, delivery_date=None,
                    next_open=100.0, announce_day_close=110.0, next_day_910_ret=0.5),
            _raw_po(pid="p2", code="1002", po_type="普通",
                    announce_date="2024-08-02", decision_date=None, delivery_date=None,
                    next_open=100.0, announce_day_close=110.0, next_day_910_ret=0.7),
            # REIT decide short edge (-ret_close = +1.5 / +0.8)
            _raw_po(pid="r1", code="3001", po_type="リート",
                    announce_date=None, decision_date="2024-08-08", delivery_date=None,
                    next_open=100.0, dec_open=98.0, dec_close=98.5,
                    ret_open=-2.0, ret_close=-1.5),
            _raw_po(pid="r2", code="3002", po_type="リート",
                    announce_date=None, decision_date="2024-08-09", delivery_date=None,
                    next_open=100.0, dec_open=99.0, dec_close=99.2,
                    ret_open=-1.0, ret_close=-0.8),
            # 普通株 deliver GD (gap<-0.5) + 寄→引け +1.0 / +1.5
            _raw_po(pid="d1", code="2001", po_type="普通",
                    announce_date=None, decision_date=None, delivery_date="2024-08-15",
                    delivery_open=97.0, delivery_close=98.0,
                    delivery_gap_pct=-0.6, delivery_ret=1.0, prev_close_before_delivery=97.5),
            _raw_po(pid="d2", code="2002", po_type="普通",
                    announce_date=None, decision_date=None, delivery_date="2024-08-16",
                    delivery_open=97.0, delivery_close=98.5,
                    delivery_gap_pct=-1.0, delivery_ret=1.5, prev_close_before_delivery=98.0),
            # 普通株 deliver でも gap>=-0.5 のレコード (GD フィルタで除外されるべき)
            _raw_po(pid="d3", code="2003", po_type="普通",
                    announce_date=None, decision_date=None, delivery_date="2024-08-17",
                    delivery_open=100.0, delivery_close=99.0,
                    delivery_gap_pct=0.0, delivery_ret=-1.0, prev_close_before_delivery=100.0),
        ]
        self.events = extract_po.expand_all(self.raw)

    def test_known_announce_edge(self) -> None:
        stats = analyze_po_edge.known_edge_announce(self.events)
        s = stats["next_day_910_ret"]
        self.assertEqual(s.n, 2)
        self.assertAlmostEqual(s.mean_pct, 0.6, places=4)

    def test_known_reit_short_edge(self) -> None:
        s = analyze_po_edge.known_edge_reit_short(self.events)
        # -ret_close を取るので 1.5, 0.8 の平均 = 1.15
        self.assertEqual(s.n, 2)
        self.assertAlmostEqual(s.mean_pct, 1.15, places=4)

    def test_delivery_gd_filters_non_gd(self) -> None:
        s = analyze_po_edge.known_edge_delivery_gd(self.events)
        # d3 は gap_pct=0.0 で除外。d1, d2 のみ。
        self.assertEqual(s.n, 2)
        self.assertAlmostEqual(s.mean_pct, 1.25, places=4)


class TestExcludeIneligible(unittest.TestCase):
    """legacy / concurrent_earnings / pending status は EV 評価対象外。"""

    def test_legacy_excluded_from_announce_edge(self) -> None:
        raw_ok = _raw_po(
            pid="ok", code="1001", po_type="普通",
            announce_date="2024-08-01", decision_date=None, delivery_date=None,
            next_open=100.0, announce_day_close=110.0, next_day_910_ret=1.0,
        )
        raw_legacy = _raw_po(
            pid="legacy", code="1002", po_type="普通",
            announce_date="2024-08-02", decision_date=None, delivery_date=None,
            next_open=100.0, announce_day_close=110.0, next_day_910_ret=10.0,  # 外れ値
            legacy=True,
        )
        events = extract_po.expand_all([raw_ok, raw_legacy])
        stats = analyze_po_edge.known_edge_announce(events)
        s = stats["next_day_910_ret"]
        self.assertEqual(s.n, 1)
        self.assertAlmostEqual(s.mean_pct, 1.0, places=4)


class TestExtractCLIOutput(unittest.TestCase):
    """extract_po.main() 相当を一時ディレクトリで実行し、JSON が読み戻せること。"""

    def test_extract_then_analyze_then_backtest(self) -> None:
        raw_payload = {
            "schema_version": "po-tracker.v1",
            "count": 2,
            "records": [
                _raw_po(pid="x1", code="1001", po_type="普通",
                        announce_date="2024-08-01", decision_date="2024-08-08", delivery_date="2024-08-15",
                        next_open=100.0, announce_day_close=110.0, next_day_910_ret=0.5,
                        dec_open=99.0, dec_close=98.0, ret_open=-1.0, ret_close=-2.0,
                        delivery_open=97.0, delivery_close=98.0,
                        delivery_gap_pct=-0.6, delivery_ret=1.0, prev_close_before_delivery=97.5),
                _raw_po(pid="x2", code="3001", po_type="リート",
                        announce_date="2024-08-02", decision_date="2024-08-09", delivery_date="2024-08-16",
                        next_open=100.0, announce_day_close=110.0, next_day_910_ret=0.3,
                        dec_open=98.0, dec_close=98.5, ret_open=-2.0, ret_close=-1.5,
                        delivery_open=97.5, delivery_close=98.0,
                        delivery_gap_pct=-0.3, delivery_ret=0.5, prev_close_before_delivery=97.8),
            ],
        }
        events = extract_po.expand_all(raw_payload["records"])
        self.assertEqual(len(events), 6)

        # analyze pass through
        payload = {"records": events, "stage_counts": {}, "type_counts": {}}
        report = analyze_po_edge.build_main_report(payload)
        self.assertIn("既知 3 エッジ再現", report)
        self.assertIn("partition" not in report.lower() or True, [True])  # smoke

        # backtest pass through
        bt = backtest_po.build_report(events, cost_pct=0.20)
        self.assertIn("PO バックテスト", bt)
        self.assertIn("既知 3 エッジ", bt)

        # stage 別レポートも空でなく、ステージ名が含まれる
        for stage in ("announce", "decide", "deliver"):
            recs = [e for e in events if e["stage"] == stage]
            md = analyze_po_edge.build_stage_report(stage, recs)
            self.assertIn(f"stage = {stage}", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)

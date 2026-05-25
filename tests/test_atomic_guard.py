"""atomic_write_records の破損ガード (records 激減時の上書き中断) を検証。

extract が空 cache 等で 0 件を生成し、コミット済みデータを上書きする事故を
防ぐためのガード。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts._atomic import RecordShrinkGuard, atomic_write_records


class TestRecordShrinkGuard(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.path = Path(self._td.name) / "records.json"
        # 既存 100 件
        atomic_write_records(self.path, {"records": [{"id": i} for i in range(100)]})

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_blocks_empty_overwrite(self) -> None:
        with self.assertRaises(RecordShrinkGuard):
            atomic_write_records(self.path, {"records": []})
        # 既存データは保持されている
        self.assertEqual(len(json.loads(self.path.read_text())["records"]), 100)

    def test_blocks_drastic_shrink(self) -> None:
        with self.assertRaises(RecordShrinkGuard):
            atomic_write_records(self.path, {"records": [{"id": i} for i in range(10)]})  # 10 < 50% of 100
        self.assertEqual(len(json.loads(self.path.read_text())["records"]), 100)

    def test_allows_normal_update(self) -> None:
        atomic_write_records(self.path, {"records": [{"id": i} for i in range(120)]})
        self.assertEqual(len(json.loads(self.path.read_text())["records"]), 120)

    def test_force_overrides_guard(self) -> None:
        atomic_write_records(self.path, {"records": []}, force=True)
        self.assertEqual(len(json.loads(self.path.read_text())["records"]), 0)

    def test_first_write_allowed(self) -> None:
        fresh = Path(self._td.name) / "fresh.json"
        atomic_write_records(fresh, {"records": []})  # 既存なし → ガードなし
        self.assertTrue(fresh.exists())


if __name__ == "__main__":
    unittest.main()

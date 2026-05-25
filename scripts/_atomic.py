"""アトミックなファイル書き込みヘルパ。

長大な JSON を直接 write_text すると、Ctrl-C / kill / disk full 等で
途中中断された場合に半端な内容で残り、次回 load 時に JSONDecodeError で
パイプラインが死ぬ。tempfile に書いてから rename することで、ファイルが
常に「書き終わった内容」か「以前の内容」のどちらかになることを保証する。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, content: str) -> None:
    """path に content を書く。最終結果が完全か rollback (前内容維持) のどちらか。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    renamed = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))  # POSIX rename = atomic
        renamed = True
    finally:
        if not renamed and os.path.exists(tmp_name):
            os.unlink(tmp_name)


def atomic_write_json(path: Path, obj: Any, *, indent: int | None = 2, ensure_ascii: bool = False) -> None:
    """JSON シリアライズして atomic_write_text で保存 (中断耐性あり)。"""
    atomic_write_text(path, json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii))


class RecordShrinkGuard(SystemExit):
    """既存より極端に少ない records で上書きしようとしたとき送出 (データ消失防止)。"""


def atomic_write_records(
    path: Path,
    payload: dict[str, Any],
    *,
    force: bool = False,
    shrink_ratio: float = 0.5,
) -> None:
    """records payload を atomic 書き込み。既存より極端に減る/空なら force 無しで中断。

    extract が空 cache 等で 0 件 (または激減した) 結果を生成し、コミット済みの
    正しいデータを上書きしてしまう事故 (silent data loss) を防ぐ安全ガード。
    意図的な縮小・初回生成時は force=True で上書きする。

    判定: 既存ファイルの records 件数 old_n が 0 より大きく、新 new_n が 0 または
    old_n*shrink_ratio 未満なら中断。
    """
    new_n = len(payload.get("records", []))
    if not force and path.exists():
        try:
            old_n = len(json.loads(path.read_text()).get("records", []))
        except (json.JSONDecodeError, OSError):
            old_n = 0
        if old_n > 0 and (new_n == 0 or new_n < old_n * shrink_ratio):
            raise RecordShrinkGuard(
                f"[guard] {path.name}: 新 {new_n} 件 が既存 {old_n} 件の "
                f"{shrink_ratio:.0%} 未満。上書きを中断しました (データ消失防止)。"
                f" cache を再取得するか、意図的な縮小なら --force を付けてください。"
            )
    atomic_write_json(path, payload)

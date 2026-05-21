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
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))  # POSIX rename = atomic
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def atomic_write_json(path: Path, obj: Any, *, indent: int | None = 2, ensure_ascii: bool = False) -> None:
    atomic_write_text(path, json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii))

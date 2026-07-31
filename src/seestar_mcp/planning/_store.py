"""Durable JSON persistence for the local planning stores.

The projects history, site profile and learned horizon mask are irreplaceable
local state — ``projects.json`` alone accumulates every session's integration
across months, and none of it exists anywhere else.

They were previously written by opening the target with ``"w"`` and serializing
into it. That truncates the file *before* the new content is produced, so any
failure in between — a crash, a kill, a full disk, a second process writing at
the same moment — left a truncated file with the old contents already gone.

:func:`write_json_atomic` closes that window.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path | str, data: Any, *, indent: int = 2) -> Path:
    """Serialize ``data`` to ``path`` as JSON, atomically.

    Writes to a temporary file in the **same directory** (so the final
    :func:`os.replace` is a same-filesystem rename, which is atomic on both POSIX
    and Windows), flushes and fsyncs it, then swaps it into place in one step.

    A crash therefore leaves either the complete old file or the complete new one
    — never a half-written mix. On failure the temp file is removed rather than
    left as litter, and the original is untouched.

    This does **not** provide mutual exclusion: two processes doing
    read-modify-write can still lose one another's update (last writer wins). It
    guarantees each store is never *corrupted*, which is the difference between
    losing one session and losing every session ever recorded.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=indent)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)  # atomic swap
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path

"""Atomic file writes (temp file + rename) and tolerant JSON reads."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _read_umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


# read once at import: os.umask() can only be read by setting it, which
# would race with files other threads create meanwhile
_UMASK = _read_umask()


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    newline: str | None = None,
    encoding: str | None = "utf-8",
    mode: int | None = None,
) -> None:
    """Write ``text`` to ``path`` via a temp file in the same dir + rename.

    The file keeps its current mode; a new file gets ``0o666 & ~umask``
    (``mkstemp`` alone would make it 0600). ``mode`` forces a mode instead.
    On error the temp file is removed and the exception propagates.
    """
    target = Path(path)
    if mode is None:
        try:
            mode = target.stat().st_mode & 0o7777
        except FileNotFoundError:
            mode = 0o666 & ~_UMASK
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as fh:
            fh.write(text)
        os.chmod(tmp_name, mode)
        Path(tmp_name).replace(target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(
    path: str | Path,
    data: Any,
    *,
    ensure_ascii: bool = True,
    mode: int | None = None,
) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=ensure_ascii), mode=mode)


def read_json_dict(path: str | Path) -> dict[str, Any] | None:
    """The JSON object in ``path``; None when missing, broken or not a dict."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None

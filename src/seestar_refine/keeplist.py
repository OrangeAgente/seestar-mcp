"""Parse a QA keep-list into resolvable, on-disk sub paths.

The QA subsystem (``qa_session_report``) writes a JSON report whose ``keep_list``
holds the *names* of the good subs to integrate. This module turns that report
(or an equivalent dict) into a :class:`KeepList` of absolute FITS paths under the
configured data dir.

Pure and best-effort: it resolves each keep-list entry under ``data_dir``,
**drops** any whose file does not exist, and **never raises** — unreadable or
malformed input yields an empty ``KeepList("", [])``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Suffixes tried when a keep_list entry is a bare sub *name* (no extension).
_FITS_SUFFIXES = (".fit", ".fits")


@dataclass
class KeepList:
    """The good subs of a session, as absolute on-disk FITS paths.

    ``target`` is the object name; ``sub_paths`` are absolute paths (as strings)
    to the kept subs that actually exist under the data dir.
    """

    target: str
    sub_paths: list[str]


def _resolve_sub(name: str, data_dir: Path) -> str | None:
    """Resolve one keep_list entry to an existing absolute path, else None.

    ``name`` may be an absolute/relative path, a filename with a FITS suffix, or
    a bare sub name. Resolution order: the name as given (relative to ``data_dir``
    when not absolute), then ``<name>.fit`` / ``<name>.fits``. The first candidate
    that exists as a file wins; missing entries are dropped by returning None.
    """
    if not isinstance(name, str) or not name:
        return None
    raw = Path(name)
    candidates = [raw if raw.is_absolute() else data_dir / raw]
    # Bare-name fallback: qa_session_report keep_list entries can be sub names
    # (e.g. "good") rather than filenames — try the FITS suffixes too.
    if raw.suffix.lower() not in _FITS_SUFFIXES:
        base = raw if raw.is_absolute() else data_dir / raw
        for suffix in _FITS_SUFFIXES:
            candidates.append(base.with_name(base.name + suffix))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def load_keep_list(source: Any, *, data_dir: Path) -> KeepList:
    """Load a keep-list from a ``qa_session_report`` JSON path or an equivalent dict.

    ``source`` may be a path to a JSON report with keys ``target`` and
    ``keep_list`` (a list of sub names/filenames), or a dict of the same shape.
    Each entry is resolved under ``data_dir``; entries whose file does not exist
    are dropped (best-effort). Never raises: any unreadable/garbage input returns
    an empty ``KeepList("", [])``.
    """
    try:
        data_dir = Path(data_dir)
        report: Any
        if isinstance(source, dict):
            report = source
        elif isinstance(source, (str, Path)):
            path = Path(source)
            if not path.is_file():
                return KeepList("", [])
            report = json.loads(path.read_text(encoding="utf-8"))
        else:
            return KeepList("", [])

        if not isinstance(report, dict):
            return KeepList("", [])

        target = report.get("target") or ""
        if not isinstance(target, str):
            target = ""
        entries = report.get("keep_list") or []
        if not isinstance(entries, list):
            return KeepList(target, [])

        sub_paths: list[str] = []
        for entry in entries:
            resolved = _resolve_sub(entry, data_dir)
            if resolved is not None:
                sub_paths.append(resolved)
        return KeepList(target, sub_paths)
    except Exception:  # noqa: BLE001 - pure core never-raise contract
        return KeepList("", [])

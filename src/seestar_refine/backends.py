"""Detect which refinement backends are available on this host.

Pure filesystem/env checks so the ``image-refinement`` skill can pick a path:

- ``dss``: the configured DeepSkyStackerCL path is set and the file exists.
- ``pixinsight``: the configured PixInsight executable is set and exists.
- ``pixinsight_mcp``: the external ``pixinsight-mcp`` bridge directory exists
  (default ``~/.pixinsight-mcp/bridge``).

Never raises; every unavailable backend adds an explanatory note.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import RefineSettings


@dataclass
class Backends:
    """Capability report for the refinement backends on this machine."""

    dss: bool
    pixinsight: bool
    pixinsight_mcp: bool
    pystack: bool
    notes: list[str]


def _is_file(path: str) -> bool:
    """True if ``path`` is a non-empty string pointing at an existing file."""
    if not path:
        return False
    try:
        return Path(path).is_file()
    except OSError:
        return False


def detect_backends(
    settings: RefineSettings, *, bridge_dir: Path | None = None
) -> Backends:
    """Return a :class:`Backends` capability report for ``settings``.

    ``dss`` / ``pixinsight`` require the configured CLI path to be set AND exist.
    ``pixinsight_mcp`` requires ``bridge_dir`` (default
    ``~/.pixinsight-mcp/bridge``) to exist as a directory. Pure filesystem checks;
    never raises. A note is added for each unavailable backend.
    """
    notes: list[str] = []

    dss = _is_file(settings.dss_cli)
    if not dss:
        notes.append(
            "DeepSkyStackerCL not found — set SEESTAR_REFINE_DSS_CLI to its path."
        )

    pixinsight = _is_file(settings.pixinsight_exe)
    if not pixinsight:
        notes.append(
            "PixInsight not found — set SEESTAR_REFINE_PIXINSIGHT_EXE to its path."
        )

    if bridge_dir is None:
        bridge_dir = Path.home() / ".pixinsight-mcp" / "bridge"
    try:
        pixinsight_mcp = Path(bridge_dir).is_dir()
    except OSError:
        pixinsight_mcp = False
    if not pixinsight_mcp:
        notes.append(
            "pixinsight-mcp bridge not found — install/run the external "
            "pixinsight-mcp server for the full PixInsight finish."
        )

    try:
        from .pystack import astroalign_available

        pystack = astroalign_available()
    except Exception:  # noqa: BLE001 - any import failure => unavailable
        pystack = False
    if not pystack:
        notes.append(
            "pystack unavailable — astroalign not importable "
            "(add the astroalign dependency)."
        )

    return Backends(
        dss=dss,
        pixinsight=pixinsight,
        pixinsight_mcp=pixinsight_mcp,
        pystack=pystack,
        notes=notes,
    )

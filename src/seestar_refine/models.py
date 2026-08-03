"""Shared data models and helpers for the seestar-refine service.

``StackResult`` is the common return shape for every stacking backend (DSS in
:mod:`seestar_refine.dss`; PixInsight WBPP in :mod:`seestar_refine.wbpp`,
Task 4). Kept in its own module so both backends can import it without a circular
dependency and the server can serialize a uniform envelope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


def slug(text: str) -> str:
    """Filesystem-safe slug for a target name (letters/digits/dashes).

    Lives here because ``dss``, ``handoff`` and ``server`` each carried their own
    byte-identical copy, and they had already begun to drift (one had a stray
    function-local ``import re``). ``None``/empty input yields ``"session"``.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return cleaned or "session"


@dataclass
class StackResult:
    """Outcome of stacking a keep-list into a master (+ optional preview).

    ``engine`` is ``"dss"`` or ``"wbpp"``. ``master_path`` / ``preview_path`` are
    ``None`` until produced. ``stats`` holds basic master statistics
    (``min``/``median``/``max``/``shape``) or ``{}`` when they cannot be computed.
    ``log`` is a tail of the external tool's own output; ``error`` is set (and
    ``ok`` is False) on any failure.
    """

    ok: bool
    engine: str
    target: str
    n_subs: int
    master_path: str | None
    preview_path: str | None
    stats: dict = field(default_factory=dict)
    log: str = ""
    error: str | None = None

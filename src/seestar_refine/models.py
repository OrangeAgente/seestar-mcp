"""Shared data models for the seestar-refine service.

``StackResult`` is the common return shape for every stacking backend (DSS in
:mod:`seestar_refine.dss`; PixInsight WBPP in :mod:`seestar_refine.wbpp`,
Task 4). Kept in its own module so both backends can import it without a circular
dependency and the server can serialize a uniform envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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

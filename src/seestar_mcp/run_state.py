"""Persisted run state: "is a run in progress right now, and what is it doing?".

Before this existed, ``session_id`` lived only in memory. During the 2026-07-31
session the MCP server died five times and no replacement could tell that a run
was underway — the plan survived only because one conversation happened to
remember it. That is not a system property.

The file is small, written atomically (see :func:`write_json_atomic`), updated on
target change and cleared at wind-down. Any process that starts can read it and
answer the question without inference.

**The state is tri-valued, not a boolean plus a caveat.** Shipping
``run_active: true`` beside a timestamp that says not to believe it is a shape a
careful consumer still gets wrong: it parses cleanly, and a reader who checks the
boolean and not the stamp is confidently wrong. ``unknown`` makes staleness a
value rather than a footnote. The staleness threshold is owned here, not by the
client — a client picking one would be hardcoding server policy.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .planning._store import write_json_atomic

_DEFAULT_PATH = Path("data") / "run_state.json"

#: How long a stamp stays believable. The writer refreshes on every target change
#: and every monitoring tick, so anything older than this means the writer died
#: mid-run: the file says "active" but nothing is driving it. Generous enough to
#: survive a slow acquisition (a goto runs a ~2-4 min alignment before stacking).
STALE_AFTER = timedelta(minutes=15)


@dataclass
class RunState:
    """A live run's plan and deadlines. Times are OFFSET-BEARING ISO-8601 UTC."""

    session_start_utc: str
    target: str  # the string passed to goto_target; the firmware echoes it back
    slot_ends_utc: str | None = None
    park_deadline_utc: str | None = None
    targets_remaining: list[str] | None = None  # None = not tracked (key omitted)
    #: Catalogue designation, present ONLY when the target string resolved against
    #: the catalogue. Omitted entirely when it did not — a consumer can then tell
    #: "resolved to M76" from "never resolved" without inferring from a null.
    resolved_id: str | None = None
    stamped_utc: str = ""
    notes: str = ""
    _extra: dict = field(default_factory=dict, repr=False)


def _payload(state: RunState) -> dict:
    """Wire form: drop keys that are *unknown* rather than emitting null.

    ``targets_remaining`` and ``resolved_id`` are omitted when we do not have
    them. ``[]`` and "not tracked" render identically and mean opposite things,
    and a null designation invites a consumer to join on nothing.
    """
    data = asdict(state)
    data.pop("_extra", None)
    if data.get("targets_remaining") is None:
        data.pop("targets_remaining", None)
    if not data.get("resolved_id"):
        data.pop("resolved_id", None)
    if not data.get("notes"):
        data.pop("notes", None)
    return data


def write_run_state(state: RunState, path: Path | None = None) -> Path:
    """Persist ``state``, stamping it now. Atomic; never leaves a partial file."""
    path = Path(path) if path is not None else _DEFAULT_PATH
    state.stamped_utc = datetime.now(timezone.utc).isoformat()
    return write_json_atomic(path, _payload(state))


def clear_run_state(path: Path | None = None) -> None:
    """Remove the file at wind-down. Missing file is not an error."""
    path = Path(path) if path is not None else _DEFAULT_PATH
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def read_run_state(path: Path | None = None, *, now_utc: str | None = None) -> dict:
    """Return ``{state, stamped_utc?, run}`` — the tool-facing answer.

    ``state`` is ``"idle"`` when no file exists, ``"active"`` when the stamp is
    fresh, and ``"unknown"`` when it is older than :data:`STALE_AFTER` or cannot
    be parsed. ``run`` is the record when active, else ``None``.

    Never raises: an unreadable or malformed file is ``"unknown"``, which is
    honest — something wrote it and we cannot vouch for it — rather than
    ``"idle"``, which would assert the scope is free when it may be slewing.
    """
    path = Path(path) if path is not None else _DEFAULT_PATH
    if not path.exists():
        return {"state": "idle", "run": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stamped = data.get("stamped_utc")
        now = (
            datetime.fromisoformat(now_utc)
            if now_utc
            else datetime.now(timezone.utc)
        )
        age = now - datetime.fromisoformat(stamped)
    except Exception:  # noqa: BLE001 - unreadable state is unknown, never idle
        return {"state": "unknown", "run": None}

    if age > STALE_AFTER:
        return {"state": "unknown", "stamped_utc": stamped, "run": data}
    return {"state": "active", "stamped_utc": stamped, "run": data}

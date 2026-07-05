"""Projects/history store: cross-night integration accounting toward goals.

A *project* tracks a target's accumulated integration across many nights against
a user goal ("M31: 2.5 h of 6 h — image more"), records each session, and lets
the planner answer "what needs more data?" and "did I image this recently?".

This module is pure/local and **deterministic**: it never reads the clock. Every
function that records a timestamp takes an explicit ``now_utc`` (an ISO string);
the tool layer supplies ``datetime.now(timezone.utc).isoformat()``.

The store is a JSON object at ``data/projects.json`` (local, gitignored),
mapping ``target_id`` -> project dict; each project's ``sessions`` is a list of
session dicts. A missing or corrupt file is treated as empty and never raises.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DEFAULT_PATH = Path("data") / "projects.json"
# Open-ended goals (goal_minutes == 0) always need more data; sort them high by
# treating their "remaining" as a very large number.
_OPEN_ENDED_REMAINING = float("inf")


@dataclass
class SessionRecord:
    """One completed imaging session's contribution to a project."""

    date_utc: str  # ISO, session wind-down time
    integration_minutes: float  # kept integration this session
    subs_total: int
    subs_kept: int
    median_fwhm: float | None = None  # from qa_session_report if available
    notes: str = ""


@dataclass
class Project:
    """A target's accumulated integration toward a goal, with session history."""

    target_id: str  # catalog id, e.g. "M31"
    target_name: str
    goal_minutes: float  # user integration goal (0 = open-ended)
    collected_minutes: float  # sum of kept integration across sessions
    status: str  # "active" | "complete" | "paused"
    created_utc: str
    updated_utc: str
    sessions: list[SessionRecord] = field(default_factory=list)
    notes: str = ""


def _project_from_dict(data: dict) -> Project:
    """Rebuild a :class:`Project` (and its ``SessionRecord`` list) from a dict."""
    data = dict(data)
    sessions = [SessionRecord(**s) for s in data.pop("sessions", [])]
    return Project(sessions=sessions, **data)


def load_projects(path: Path | None = None) -> dict[str, Project]:
    """Load the store as ``{target_id: Project}``; ``{}`` if missing or corrupt.

    Never raises: a missing file or unparseable/invalid JSON yields ``{}`` so the
    planner degrades gracefully to "no history".
    """
    path = Path(path) if path is not None else _DEFAULT_PATH
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return {tid: _project_from_dict(pd) for tid, pd in raw.items()}
    except (json.JSONDecodeError, TypeError, ValueError, OSError):
        return {}


def save_projects(projects: dict[str, Project], path: Path | None = None) -> Path:
    """Persist ``projects`` as JSON to ``path`` (default ``data/projects.json``).

    Parent directories are created as needed. Returns the path written.
    """
    path = Path(path) if path is not None else _DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {tid: asdict(proj) for tid, proj in projects.items()}
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def get_project(target_id: str, path: Path | None = None) -> Project | None:
    """Return the project for ``target_id``, or ``None`` if it does not exist."""
    return load_projects(path).get(target_id)


def upsert_project(
    target_id: str,
    target_name: str,
    *,
    goal_minutes: float = 0.0,
    status: str | None = None,
    notes: str | None = None,
    now_utc: str,
    path: Path | None = None,
) -> Project:
    """Create or update a project's metadata only.

    Updates ``goal_minutes`` and, when provided, ``status``/``notes``. Never
    touches ``sessions`` or ``collected_minutes``. New projects start
    ``status="active"`` with ``created_utc=now_utc``. ``updated_utc`` is set to
    ``now_utc``.
    """
    projects = load_projects(path)
    proj = projects.get(target_id)
    if proj is None:
        proj = Project(
            target_id=target_id,
            target_name=target_name,
            goal_minutes=goal_minutes,
            collected_minutes=0.0,
            status=status or "active",
            created_utc=now_utc,
            updated_utc=now_utc,
            notes=notes or "",
        )
    else:
        proj.target_name = target_name
        proj.goal_minutes = goal_minutes
        if status is not None:
            proj.status = status
        if notes is not None:
            proj.notes = notes
        proj.updated_utc = now_utc
    projects[target_id] = proj
    save_projects(projects, path)
    return proj


def log_session_result(
    target_id: str,
    target_name: str,
    *,
    integration_minutes: float,
    subs_total: int,
    subs_kept: int,
    median_fwhm: float | None = None,
    notes: str = "",
    now_utc: str,
    path: Path | None = None,
) -> Project:
    """Record a completed session and accumulate its integration.

    Appends a :class:`SessionRecord` (``date_utc=now_utc``), adds
    ``integration_minutes`` to ``collected_minutes``, and sets
    ``updated_utc=now_utc``. Auto-sets ``status="complete"`` when
    ``goal_minutes > 0 and collected_minutes >= goal_minutes``. Creates the
    project (``status="active"``, ``created_utc=now_utc``) if it does not exist.
    """
    projects = load_projects(path)
    proj = projects.get(target_id)
    if proj is None:
        proj = Project(
            target_id=target_id,
            target_name=target_name,
            goal_minutes=0.0,
            collected_minutes=0.0,
            status="active",
            created_utc=now_utc,
            updated_utc=now_utc,
        )
    proj.sessions.append(
        SessionRecord(
            date_utc=now_utc,
            integration_minutes=integration_minutes,
            subs_total=subs_total,
            subs_kept=subs_kept,
            median_fwhm=median_fwhm,
            notes=notes,
        )
    )
    proj.collected_minutes += integration_minutes
    proj.updated_utc = now_utc
    if proj.goal_minutes > 0 and proj.collected_minutes >= proj.goal_minutes:
        proj.status = "complete"
    projects[target_id] = proj
    save_projects(projects, path)
    return proj


def _remaining(proj: Project) -> float:
    """Minutes still needed toward the goal (open-ended → very large)."""
    if proj.goal_minutes == 0:
        return _OPEN_ENDED_REMAINING
    return proj.goal_minutes - proj.collected_minutes


def recommend_projects(
    path: Path | None = None, *, limit: int | None = None
) -> list[Project]:
    """Active projects still needing data, most-needed first.

    Includes active projects with ``goal_minutes == 0`` (open-ended) or
    ``collected_minutes < goal_minutes``, sorted by remaining minutes descending
    (open-ended treated as a large remaining, so they sort high). ``limit`` caps
    the count when given.
    """
    projects = load_projects(path)
    needing = [
        proj
        for proj in projects.values()
        if proj.status == "active"
        and (proj.goal_minutes == 0 or proj.collected_minutes < proj.goal_minutes)
    ]
    needing.sort(key=_remaining, reverse=True)
    if limit is not None:
        needing = needing[:limit]
    return needing


def _parse_iso(value: str) -> datetime:
    """Parse an ISO timestamp, tolerating a trailing ``Z`` (→ ``+00:00``)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def was_recently_imaged(
    target_id: str,
    within_days: int,
    now_utc: str,
    path: Path | None = None,
) -> bool:
    """True if any session's ``date_utc`` is within ``within_days`` before ``now_utc``.

    A session at exactly the ``now_utc - within_days`` boundary counts as recent.
    Unknown targets and unparseable session dates yield ``False``.
    """
    proj = get_project(target_id, path)
    if proj is None:
        return False
    now = _parse_iso(now_utc)
    cutoff = now - timedelta(days=within_days)
    for session in proj.sessions:
        try:
            when = _parse_iso(session.date_utc)
        except (ValueError, TypeError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if cutoff <= when <= now:
            return True
    return False

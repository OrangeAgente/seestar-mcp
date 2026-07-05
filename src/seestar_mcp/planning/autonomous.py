"""Pure, deterministic cores for the autonomous-night operation.

Two side-effect-free functions the autonomous-night skill drives through the
MCP tool layer:

* :func:`plan_night` — a greedy, non-overlapping sequencer over an
  already-ranked list of compact target dicts (the shape produced by
  ``plan_targets``), packing the dark window start→dawn.
* :func:`evaluate_guardrails` — evaluates the hard-stop safety conditions
  (dawn margin, max session duration, battery floor, connection/verification,
  weather no-go) for a single loop iteration.

Both take injected timestamps and inputs — **no clock, no I/O** — so they are
fully deterministic and unit-testable. Neither ever raises: malformed inputs
resolve to a conservative verdict (guardrails) or a skipped target (planner).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ScheduledTarget:
    """One target allocated to a concrete slot within the dark window."""

    target_id: str
    target_name: str
    start_utc: str
    end_utc: str
    minutes: float
    recommended_subs: int
    reason: str  # why this target in this slot


@dataclass
class GuardrailVerdict:
    """The outcome of one guardrail evaluation for the autonomous loop."""

    proceed: bool
    action: str  # "continue" | "switch" | "park_and_stop"
    reasons: list[str]  # every stop/continue reason, human-readable
    hard_stops: list[str]  # subset that are HARD stops (dawn/battery/precip/etc.)


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO timestamp, tolerating a trailing ``Z`` (→ ``+00:00``).

    Returns ``None`` on any failure (never raises) so callers stay pure and
    fail conservatively rather than crashing on malformed input.
    """
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when


def plan_night(
    scored_targets: list[dict],
    dark_window_utc: tuple[str, str] | list[str],
    *,
    min_slot_min: float = 20.0,
    max_targets: int | None = None,
) -> list[ScheduledTarget]:
    """Greedily sequence ranked targets into the dark window, non-overlapping.

    ``scored_targets`` is an already-ranked list of compact dicts (as produced
    by ``plan_targets``): each has ``id``, ``name``, ``best_window_utc``
    (``[start_iso, end_iso]`` or ``None``) and ``recommended_subs``. Targets are
    processed in order of their window start; each is clamped to the remaining
    night ``[max(cursor, dark_start), dark_end]`` and kept only if it yields at
    least ``min_slot_min`` minutes. Pure and deterministic.
    """
    if not scored_targets:
        return []

    dark_start = _parse_iso(dark_window_utc[0])
    dark_end = _parse_iso(dark_window_utc[1])
    if dark_start is None or dark_end is None or dark_end <= dark_start:
        return []

    # Decorate with a parsed window start so we can sort without re-parsing, and
    # drop entries that have no/malformed window.
    parsed: list[tuple[datetime, datetime, dict]] = []
    for target in scored_targets:
        window = target.get("best_window_utc")
        if not window or len(window) < 2:
            continue
        w_start = _parse_iso(window[0])
        w_end = _parse_iso(window[1])
        if w_start is None or w_end is None or w_end <= w_start:
            continue
        parsed.append((w_start, w_end, target))

    parsed.sort(key=lambda item: item[0])

    schedule: list[ScheduledTarget] = []
    cursor = dark_start
    for w_start, w_end, target in parsed:
        if max_targets is not None and len(schedule) >= max_targets:
            break
        # Clamp to the remaining night.
        start = max(w_start, cursor, dark_start)
        end = min(w_end, dark_end)
        if end <= start:
            continue  # window has already passed or falls outside the night
        minutes = (end - start).total_seconds() / 60.0
        if minutes < min_slot_min:
            continue  # slot too short to be worth a target switch
        schedule.append(
            ScheduledTarget(
                target_id=str(target.get("id", "")),
                target_name=str(target.get("name", target.get("id", ""))),
                start_utc=start.isoformat().replace("+00:00", "Z"),
                end_utc=end.isoformat().replace("+00:00", "Z"),
                minutes=round(minutes, 1),
                recommended_subs=int(target.get("recommended_subs", 0) or 0),
                reason=(
                    f"Sweet-band window fits a {round(minutes)}-min slot "
                    f"before dawn"
                ),
            )
        )
        cursor = end

    return schedule


def evaluate_guardrails(
    *,
    now_utc: str,
    dark_window_utc: tuple[str, str] | list[str],
    session_start_utc: str,
    battery_pct: float | None,
    weather_go: bool | None,
    connected: bool,
    verified: bool,
    max_session_hours: float = 10.0,
    battery_floor_pct: float = 20.0,
    dawn_margin_min: float = 15.0,
    stop_on_weather_nogo: bool = True,
) -> GuardrailVerdict:
    """Evaluate the hard-stop safety conditions for one autonomous iteration.

    Pushes a HARD stop (with a specific reason string) for each triggered
    condition: within ``dawn_margin_min`` of dawn; elapsed since
    ``session_start_utc`` >= ``max_session_hours``; battery below
    ``battery_floor_pct``; ``not connected``; ``not verified``; weather no-go
    when ``stop_on_weather_nogo``. ``action`` is ``"park_and_stop"`` if any hard
    stop fires, else ``"continue"``. Unknown battery is a noted non-fatal
    reason, not a hard stop. Never raises.
    """
    reasons: list[str] = []
    hard_stops: list[str] = []

    now = _parse_iso(now_utc)
    dark_end = _parse_iso(dark_window_utc[1]) if dark_window_utc else None
    session_start = _parse_iso(session_start_utc)

    # Dawn margin — within dawn_margin_min of the dark-window end.
    if now is not None and dark_end is not None:
        minutes_to_dawn = (dark_end - now).total_seconds() / 60.0
        if minutes_to_dawn <= dawn_margin_min:
            hard_stops.append(
                f"Within {dawn_margin_min:g} min of dawn "
                f"({minutes_to_dawn:.0f} min remaining)"
            )
    elif now is None or dark_end is None:
        # Can't confirm we're clear of dawn → fail safe.
        hard_stops.append("Dawn margin could not be evaluated (unparseable time)")

    # Max session duration.
    if now is not None and session_start is not None:
        elapsed_hours = (now - session_start).total_seconds() / 3600.0
        if elapsed_hours >= max_session_hours:
            hard_stops.append(
                f"Session duration {elapsed_hours:.1f}h reached the "
                f"{max_session_hours:g}h limit"
            )
    elif session_start is None:
        hard_stops.append(
            "Max session duration could not be evaluated (unparseable start)"
        )

    # Battery floor.
    if battery_pct is None:
        reasons.append("Battery level unknown — proceeding cautiously")
    elif battery_pct < battery_floor_pct:
        hard_stops.append(
            f"Battery {battery_pct:g}% below the {battery_floor_pct:g}% floor"
        )

    # Connection / verification.
    if not connected:
        hard_stops.append("Scope not connected")
    if not verified:
        hard_stops.append("Scope identity unverified")

    # Weather.
    if weather_go is False and stop_on_weather_nogo:
        hard_stops.append("Weather no-go (hard conditions / precip)")
    elif weather_go is None:
        reasons.append("Weather unverified — observability-only, not a hard stop")

    if hard_stops:
        reasons = list(hard_stops) + reasons
        action = "park_and_stop"
    else:
        action = "continue"
        if not reasons:
            reasons.append("All guardrails clear — continue")

    return GuardrailVerdict(
        proceed=not hard_stops,
        action=action,
        reasons=reasons,
        hard_stops=hard_stops,
    )

"""Run-state persistence: the answer to "is a run in progress right now?".

Written because inferring that from a ``get_view_state`` timeout produces a
confident wrong answer, and "idle while stacking" is the worst available way to
be wrong — the Console team shipped exactly that. The tri-state and the
omit-rather-than-null rules are both consumer requirements, so they are pinned
here rather than left as implementation detail.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from seestar_mcp.run_state import (
    STALE_AFTER,
    RunState,
    clear_run_state,
    read_run_state,
    write_run_state,
)

START = "2026-08-02T02:00:00+00:00"


def _state(**kw) -> RunState:
    base = dict(
        session_start_utc=START,
        target="M76",
        slot_ends_utc="2026-08-02T05:00:00+00:00",
        park_deadline_utc="2026-08-02T07:15:00+00:00",
    )
    base.update(kw)
    return RunState(**base)


def test_no_file_is_idle_not_unknown(tmp_path):
    """Nothing written = nothing running. That is a real answer, not an absence."""
    out = read_run_state(tmp_path / "run_state.json")
    assert out["state"] == "idle"
    assert out["run"] is None


def test_a_fresh_stamp_is_active_and_carries_the_plan(tmp_path):
    p = tmp_path / "run_state.json"
    write_run_state(_state(targets_remaining=["NGC7635"]), p)

    out = read_run_state(p)
    assert out["state"] == "active"
    assert out["run"]["target"] == "M76"
    assert out["run"]["targets_remaining"] == ["NGC7635"]
    assert out["run"]["park_deadline_utc"] == "2026-08-02T07:15:00+00:00"


def test_a_stale_stamp_is_unknown_not_active(tmp_path):
    """The writer died mid-run: the file says active, nothing is driving it.

    This is why the state is tri-valued rather than a boolean plus a stamp — a
    consumer reading a boolean and ignoring the stamp would be confidently wrong,
    and it would parse cleanly.
    """
    p = tmp_path / "run_state.json"
    write_run_state(_state(), p)

    later = (
        datetime.now(timezone.utc) + STALE_AFTER + timedelta(minutes=1)
    ).isoformat()
    out = read_run_state(p, now_utc=later)

    assert out["state"] == "unknown"
    assert out["run"] is not None  # still shown, just not vouched for


def test_a_corrupt_file_is_unknown_never_idle(tmp_path):
    """Something wrote it and we cannot vouch for it.

    "idle" would assert the scope is free when it may be slewing — the failure
    direction that matters.
    """
    p = tmp_path / "run_state.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_run_state(p)["state"] == "unknown"


def test_unknown_values_are_omitted_not_nulled(tmp_path):
    """``[]`` and "not tracked" render identically and mean opposite things."""
    p = tmp_path / "run_state.json"
    write_run_state(_state(targets_remaining=None, resolved_id=None), p)

    raw = json.loads(p.read_text(encoding="utf-8"))
    assert "targets_remaining" not in raw, "omit when not tracked, never []"
    assert "resolved_id" not in raw, "omit when unresolved, never null"


def test_resolved_id_appears_only_when_it_resolved(tmp_path):
    p = tmp_path / "run_state.json"
    write_run_state(_state(resolved_id="M76"), p)
    assert json.loads(p.read_text(encoding="utf-8"))["resolved_id"] == "M76"


def test_timestamps_are_offset_bearing(tmp_path):
    """Stated to the Console team explicitly, so pin it."""
    p = tmp_path / "run_state.json"
    write_run_state(_state(), p)
    stamped = json.loads(p.read_text(encoding="utf-8"))["stamped_utc"]
    assert datetime.fromisoformat(stamped).tzinfo is not None
    assert stamped.endswith("+00:00")


def test_clear_makes_it_idle_again(tmp_path):
    p = tmp_path / "run_state.json"
    write_run_state(_state(), p)
    assert read_run_state(p)["state"] == "active"

    clear_run_state(p)
    assert read_run_state(p)["state"] == "idle"
    clear_run_state(p)  # idempotent — wind-down may run twice


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    """Same durability rule as the other stores: no partial file, no litter."""
    p = tmp_path / "run_state.json"
    write_run_state(_state(), p)
    assert [f.name for f in tmp_path.iterdir()] == ["run_state.json"]


def test_run_is_not_a_liveness_signal(tmp_path):
    """``run`` is populated in TWO states, so its presence proves nothing.

    Reported by the Console team, who have a real recording of stale-``unknown``.
    The stale record is retained on purpose — what was running when we lost track
    is worth knowing — but that means a consumer branching on ``run is not None``
    treats a dead session as live, which is the exact failure ``get_run_state``
    exists to prevent.
    """
    p = tmp_path / "run_state.json"
    write_run_state(_state(), p)

    live = read_run_state(p)
    stale = read_run_state(
        p,
        now_utc=(
            datetime.now(timezone.utc) + STALE_AFTER + timedelta(minutes=1)
        ).isoformat(),
    )

    # Populated in BOTH — indistinguishable on `run` alone.
    assert live["run"] is not None and stale["run"] is not None
    assert live["state"] == "active" and stale["state"] == "unknown"

    # None only for idle and for an unreadable file.
    clear_run_state(p)
    assert read_run_state(p)["run"] is None
    p.write_text("{not json", encoding="utf-8")
    assert read_run_state(p)["run"] is None

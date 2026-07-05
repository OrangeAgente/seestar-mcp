"""Tests for seestar_mcp.qa_tier1 (Tier-1 firmware telemetry health monitor).

Tier-1 emits telemetry + neutral health *flags*, never quality verdicts. These
tests use an ``AsyncMock`` AlpacaClient whose ``method_sync`` returns canned
``get_view_state`` / ``get_device_state`` dicts (via ``side_effect`` for a
sequence of polls). ``asyncio_mode=auto`` means async tests need no decorator.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from seestar_mcp.alpaca_client import AlpacaError
from seestar_mcp.provenance import ProvenanceLog
from seestar_mcp.qa_tier1 import (
    Tier1Monitor,
    Tier1Snapshot,
    _first,
    _parse_device_state,
    _parse_view_state,
)


# --- _first ---------------------------------------------------------------


def test_first_returns_first_present_non_none():
    d = {"a": None, "b": 5, "c": 9}
    assert _first(d, "a", "b", "c") == 5
    assert _first(d, "missing", "c") == 9
    assert _first(d, "missing", "a") is None


# --- parsers --------------------------------------------------------------


def test_parse_view_state_canonical_keys():
    d = {
        "stacked_frame": 128,
        "dropped_frame": 12,
        "plate_solve": True,
        "rms": 0.42,
        "hfd": 2.31,
    }
    out = _parse_view_state(d)
    assert out["stacked"] == 128
    assert out["rejected"] == 12
    assert out["solve_ok"] is True
    assert out["solve_rms"] == 0.42
    assert out["hfd"] == 2.31


def test_parse_view_state_alternate_keys():
    d = {
        "stack_count": 64,
        "bad_count": 3,
        "solve_state": "complete",
        "solve_rms": 0.9,
    }
    out = _parse_view_state(d)
    assert out["stacked"] == 64
    assert out["rejected"] == 3
    assert out["solve_ok"] is True
    assert out["solve_rms"] == 0.9


def test_parse_view_state_hfd_averages_x_and_y():
    out = _parse_view_state({"hfd_x": 2.0, "hfd_y": 3.0})
    assert out["hfd"] == 2.5


def test_parse_view_state_hfd_x_only():
    out = _parse_view_state({"hfd_x": 2.2})
    assert out["hfd"] == 2.2


def test_parse_view_state_nested_wrapper_unwrapped():
    d = {"result": {"stacked": 200, "rejected": 5}}
    out = _parse_view_state(d)
    assert out["stacked"] == 200
    assert out["rejected"] == 5


def test_parse_view_state_missing_keys_yield_empty():
    assert _parse_view_state({}) == {}
    assert _parse_view_state({"unrelated": 1}) == {}


def test_parse_device_state_canonical_and_alternate():
    assert _parse_device_state({"focus_pos": 1500, "tracking": True}) == {
        "focus_pos": 1500,
        "tracking": True,
    }
    assert _parse_device_state({"focuser_position": 1490, "track_state": "on"}) == {
        "focus_pos": 1490,
        "tracking": True,
    }
    assert _parse_device_state({"step": 1480}) == {"focus_pos": 1480}


def test_parse_device_state_missing_keys():
    assert _parse_device_state({}) == {}


# --- poll -----------------------------------------------------------------


async def test_poll_merges_view_and_device(tmp_path):
    prov = ProvenanceLog(tmp_path / "prov.jsonl")
    alpaca = AsyncMock()
    alpaca.method_sync.side_effect = [
        {"stacked_frame": 10, "dropped_frame": 1, "plate_solve": True, "hfd": 2.0},
        {"focus_pos": 1500, "tracking": True},
    ]
    mon = Tier1Monitor(alpaca, provenance=prov)
    snap = await mon.poll()

    assert isinstance(snap, Tier1Snapshot)
    assert snap.stacked == 10
    assert snap.rejected == 1
    assert snap.solve_ok is True
    assert snap.hfd == 2.0
    assert snap.focus_pos == 1500
    assert snap.tracking is True
    assert snap.raw["view"]["stacked_frame"] == 10
    assert snap.raw["device"]["focus_pos"] == 1500

    # First poll sets the focus baseline and appends to history.
    assert mon.focus_baseline == 1500
    assert len(mon.history) == 1

    # A provenance record was written for the poll.
    lines = (tmp_path / "prov.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["tool"] == "qa_tier1.poll"
    assert rec["args"]["stacked"] == 10
    assert rec["args"]["focus_pos"] == 1500


async def test_poll_survives_one_failed_subcall():
    alpaca = AsyncMock()
    alpaca.method_sync.side_effect = [
        AlpacaError(1024, "NotImplemented", "action"),
        {"focus_pos": 1500, "tracking": True},
    ]
    mon = Tier1Monitor(alpaca)
    snap = await mon.poll()

    # View failed, so its fields are None, but device fields still populate.
    assert snap.stacked is None
    assert snap.solve_ok is None
    assert snap.focus_pos == 1500
    assert snap.tracking is True
    # The failure is recorded in raw for audit.
    assert "view_error" in snap.raw


# --- trends ---------------------------------------------------------------


async def test_trends_across_two_polls():
    alpaca = AsyncMock()
    alpaca.method_sync.side_effect = [
        {"stacked": 100, "rejected": 5, "hfd": 2.0},
        {"focus_pos": 1500},
        {"stacked": 104, "rejected": 8, "hfd": 2.2},
        {"focus_pos": 1494},
    ]
    mon = Tier1Monitor(alpaca)
    await mon.poll()
    # Single-snapshot trends are safe.
    t1 = mon.trends()
    assert t1["stacked_delta"] is None
    assert t1["focus_delta"] == 0  # baseline == first focus

    await mon.poll()
    t2 = mon.trends()
    assert t2["stacked_delta"] == 4
    assert t2["rejected_delta"] == 3
    assert t2["focus_delta"] == -6  # 1494 - 1500 baseline
    assert abs(t2["hfd_delta"] - 0.2) < 1e-9


def test_trends_empty_history_safe():
    mon = Tier1Monitor(AsyncMock())
    t = mon.trends()
    assert t == {
        "stacked_delta": None,
        "rejected_delta": None,
        "focus_delta": None,
        "hfd_delta": None,
    }


# --- check (health flags, not verdicts) -----------------------------------


def _snap(**kwargs):
    return Tier1Snapshot(ts="2026-07-04T00:00:00+00:00", **kwargs)


def test_check_stacking_stalled():
    mon = Tier1Monitor(AsyncMock())
    mon.history = [_snap(stacked=50), _snap(stacked=50)]
    assert "stacking_stalled" in mon.check()


def test_check_rejection_spike():
    mon = Tier1Monitor(AsyncMock())
    mon.history = [_snap(stacked=50, rejected=2), _snap(stacked=54, rejected=15)]
    flags = mon.check()
    assert "rejection_spike" in flags
    assert "stacking_stalled" not in flags


def test_check_focus_drift():
    mon = Tier1Monitor(AsyncMock())
    mon.focus_baseline = 1500
    mon.history = [_snap(stacked=50, focus_pos=1500), _snap(stacked=54, focus_pos=1560)]
    assert "focus_drift" in mon.check()


def test_check_solve_lost():
    mon = Tier1Monitor(AsyncMock())
    mon.history = [_snap(stacked=50, solve_ok=False)]
    assert "solve_lost" in mon.check()


def test_check_hfd_rising():
    mon = Tier1Monitor(AsyncMock())
    mon.history = [_snap(stacked=50, hfd=2.0), _snap(stacked=54, hfd=2.8)]
    assert "hfd_rising" in mon.check()


def test_check_clean_history_no_flags():
    mon = Tier1Monitor(AsyncMock())
    mon.focus_baseline = 1500
    mon.history = [
        _snap(stacked=50, rejected=2, solve_ok=True, focus_pos=1500, hfd=2.0),
        _snap(stacked=54, rejected=3, solve_ok=True, focus_pos=1502, hfd=1.9),
    ]
    assert mon.check() == []


# --- status_line ----------------------------------------------------------


def test_status_line_renders_compact_string():
    mon = Tier1Monitor(AsyncMock())
    mon.focus_baseline = 100
    mon.history = [
        _snap(stacked=124, rejected=8, solve_ok=True, focus_pos=100, hfd=2.30),
        _snap(stacked=128, rejected=12, solve_ok=True, focus_pos=94, hfd=2.31),
    ]
    line = mon.status_line()
    assert line == "stacked 128 (+4) | rejected 12 | solve OK | focus Δ=-6 | hfd 2.31"


def test_status_line_omits_unknown_fields():
    mon = Tier1Monitor(AsyncMock())
    mon.history = [_snap(stacked=5)]
    assert mon.status_line() == "stacked 5"

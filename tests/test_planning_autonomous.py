"""Unit tests for the pure autonomous-night cores.

Both :func:`plan_night` and :func:`evaluate_guardrails` are pure/deterministic
(injected timestamps, no clock, no I/O), so these tests need no mocks.
"""

from seestar_mcp.planning.autonomous import evaluate_guardrails, plan_night

DW = ("2026-07-05T02:00:00Z", "2026-07-05T08:00:00Z")


def _t(id, s, e, subs=300):
    return {"id": id, "name": id, "best_window_utc": [s, e], "recommended_subs": subs}


def test_plan_night_orders_and_no_overlap():
    targets = [
        _t("A", "2026-07-05T02:30:00Z", "2026-07-05T04:00:00Z"),
        _t("B", "2026-07-05T04:30:00Z", "2026-07-05T06:00:00Z"),
        _t("C", "2026-07-05T01:00:00Z", "2026-07-05T01:30:00Z"),  # window before dark start
    ]
    sched = plan_night(targets, DW)
    ids = [s.target_id for s in sched]
    assert ids == ["A", "B"]  # C skipped (window passed before dark)
    assert sched[0].end_utc <= sched[1].start_utc  # non-overlapping, ordered


def test_plan_night_min_slot_and_empty():
    assert plan_night([], DW) == []
    tiny = [_t("A", "2026-07-05T02:00:00Z", "2026-07-05T02:05:00Z")]  # 5 min < min_slot 20
    assert plan_night(tiny, DW, min_slot_min=20.0) == []  # dropped: slot too short


def test_plan_night_recommended_subs_match_slot_not_input():
    # Input carries subs=300, but a 90-min slot at 10s must recompute to 540 —
    # the schedule's sub count reflects the allocated slot, not the ranker's
    # full sweet-band figure.
    targets = [_t("A", "2026-07-05T02:00:00Z", "2026-07-05T03:30:00Z", subs=9999)]
    sched = plan_night(targets, DW)
    assert len(sched) == 1
    assert sched[0].minutes == 90.0
    assert sched[0].recommended_subs == 540  # 90 * 60 / 10, not 9999


def test_guardrails_healthy_continues():
    v = evaluate_guardrails(
        now_utc="2026-07-05T04:00:00Z",
        dark_window_utc=DW,
        session_start_utc="2026-07-05T02:30:00Z",
        battery_pct=80,
        weather_go=True,
        connected=True,
        verified=True,
    )
    assert v.proceed is True and v.action == "continue" and v.hard_stops == []


def test_guardrails_each_hard_stop():
    base = dict(
        dark_window_utc=DW,
        session_start_utc="2026-07-05T02:30:00Z",
        battery_pct=80,
        weather_go=True,
        connected=True,
        verified=True,
    )
    # dawn margin (now within 15 min of 08:00)
    assert (
        evaluate_guardrails(now_utc="2026-07-05T07:50:00Z", **base).action
        == "park_and_stop"
    )
    # low battery
    assert (
        evaluate_guardrails(
            now_utc="2026-07-05T04:00:00Z", **{**base, "battery_pct": 10}
        ).action
        == "park_and_stop"
    )
    # disconnected
    assert (
        evaluate_guardrails(
            now_utc="2026-07-05T04:00:00Z", **{**base, "connected": False}
        ).action
        == "park_and_stop"
    )
    # weather no-go
    assert (
        evaluate_guardrails(
            now_utc="2026-07-05T04:00:00Z", **{**base, "weather_go": False}
        ).action
        == "park_and_stop"
    )
    # unknown battery is conservative but not a crash
    v = evaluate_guardrails(
        now_utc="2026-07-05T04:00:00Z", **{**base, "battery_pct": None}
    )
    assert isinstance(v.proceed, bool)

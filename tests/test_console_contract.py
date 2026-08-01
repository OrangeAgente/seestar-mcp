"""Contract tests for the SeeStar Console dashboard.

Generated from the field-dependency list the Console team supplied on
2026-08-01, which they derived from their own ``web/src/api/schemas.ts``.

**Why these exist.** Their client treats a parse failure as *the tool having
failed*, not as a degraded panel — and on the Live screen that renders as "the
scope is idle". They shipped that exact bug twice. So a change that drops a
required field does not thin out a UI; it takes out a screen and lies about the
telescope. These tests move that failure from their runtime to our build.

**Required vs nullable is the load-bearing distinction:**

* ``REQUIRED`` — the key must be present. Removing one breaks a screen.
* ``NULLABLE`` — the key must be present but may be ``None``. Removing one
  degrades a panel.

Anything not listed is free to change. This file encodes the consumer's needs,
not our preferences; when it conflicts with a refactor, the refactor is a
breaking change and needs a contract version bump plus a note to them.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from seestar_mcp.config import Settings
from seestar_mcp.server import SeestarController

FIXTURE_DIR = Path(__file__).parent / "fixtures"

#: Naive ISO-8601, no offset — the shape the planning layer emits.
#: The Console team had a helper that returned NaN for the offset-bearing form,
#: so a silent change here breaks date rendering rather than failing loudly.
NAIVE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")
#: Offset-bearing ISO-8601 — the shape the provenance layer emits.
OFFSET_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+\d{2}:\d{2}$")


def _controller(tmp_path) -> SeestarController:
    return SeestarController(
        settings=Settings(_env_file=None, data_dir=tmp_path),
        provenance=MagicMock(),
        alpaca=AsyncMock(),
        data=AsyncMock(),
        tier1=AsyncMock(),
    )


def _require(payload: dict, keys: list[str], where: str) -> None:
    """Every key present (value may be anything, including None if nullable)."""
    missing = [k for k in keys if k not in payload]
    assert not missing, f"{where}: missing required key(s) {missing}"


# --- get_view_state --------------------------------------------------------
# Their most-burned surface. Three pins, all from real breakage.


def test_get_view_state_accepts_an_idle_scope_with_empty_result(tmp_path):
    """``result: {}`` with no ``View`` key must stay valid.

    This is the most common real response — a connected, idle scope — and the
    Console team now treats ``View`` as optional-and-nullable rather than merely
    nullable because of it. If we ever start synthesising a ``View`` here, or
    start erroring on the empty shape, their idle path breaks.
    """
    c = _controller(tmp_path)
    c.alpaca.method_sync.return_value = {"jsonrpc": "2.0", "result": {}, "code": 0}

    out = asyncio.run(c.get_view_state())

    _require(out, ["ok", "view_state"], "get_view_state")
    assert out["ok"] is True
    assert out["view_state"]["result"] == {}


def test_get_view_state_preserves_the_annotation_nesting(tmp_path):
    """Plate-solve output stays at ``Stack.Annotate.result.annotations[]``.

    Their hand-authored fixture once had ``pixelx``/``pixely``/``radius`` flat on
    ``Annotate``, and agreed with a schema that was wrong the same way, so the
    overlay silently mis-rendered. The nesting is the contract.
    """
    c = _controller(tmp_path)
    c.alpaca.method_sync.return_value = {
        "result": {
            "View": {
                "stage": "Stack",
                "target_name": "M76",
                "lp_filter": True,
                "Stack": {
                    "stacked_frame": 614,
                    "dropped_frame": 0,
                    "can_annotate": True,
                    "Annotate": {
                        "state": "complete",
                        "result": {
                            "image_size": [1080, 1920],
                            "annotations": [
                                {
                                    "type": "ngc",
                                    "names": ["M 76"],
                                    "pixelx": 309.1,
                                    "pixely": 1190.6,
                                    "radius": 315.8,
                                }
                            ],
                        },
                    },
                },
            }
        },
        "code": 0,
    }

    view = asyncio.run(c.get_view_state())["view_state"]["result"]["View"]

    # NOTE: fields on View are deliberately NOT _require()d. A real mid-acquisition
    # payload carries `Initialise` and `stage` but NO `Stack` key at all (observed
    # on hardware 2026-07-31 during a 3PPA alignment), and the Console schema has
    # them nullish for the same reason. Pinning presence here would fail on a
    # legitimate payload, and the natural fix — deleting the pin — would take the
    # nesting assertions below with it. The NESTING is the contract; the presence
    # of any given field is not.
    _require(view["Stack"], ["stacked_frame", "dropped_frame"], "View.Stack")

    annotate = view["Stack"]["Annotate"]
    _require(annotate, ["result"], "Stack.Annotate")
    ann = annotate["result"]["annotations"][0]
    # Position lives INSIDE result.annotations[], not flat on Annotate — that is
    # the shape that bit them. Presence of individual keys is not pinned.
    assert {"pixelx", "pixely", "radius"} <= set(ann), (
        "plate-solve position must stay inside result.annotations[]"
    )
    # image_size is consumed as a two-element array to scale the overlay.
    assert len(annotate["result"]["image_size"]) == 2


# --- get_status ------------------------------------------------------------


def test_get_status_keys_are_present_even_when_unreadable(tmp_path):
    """Only ``ok`` is required, but the five fields must be PRESENT (may be None).

    They read all five; absence and null are different failures on their side.
    """
    c = _controller(tmp_path)
    c.alpaca.get_connected.return_value = True
    c.alpaca.get_ra.return_value = 1.7
    c.alpaca.get_dec.return_value = 51.5
    c.alpaca.get_tracking.return_value = True
    c.alpaca.is_slewing.return_value = False

    out = asyncio.run(c.get_status())
    _require(
        out,
        ["ok", "connected", "rightascension", "declination", "tracking", "slewing"],
        "get_status",
    )


# --- qa_tier2 --------------------------------------------------------------


def test_qa_tier2_subs_carry_metrics_and_keep_unanalysable_rows(tmp_path):
    """Per-sub metrics stay; a sub that could not be analysed stays in the array.

    They render those as "unanalysed" rows and join on ``name``, so filtering
    them out silently shortens a chart rather than reporting a gap.
    """
    c = _controller(tmp_path)
    paths = [
        str(FIXTURE_DIR / "good.fits"),
        str(FIXTURE_DIR / "bad_ecc.fits"),
        str(FIXTURE_DIR / "does_not_exist.fits"),  # forces metrics.error
    ]
    out = asyncio.run(c.qa_tier2(paths=paths))

    _require(out, ["ok", "summary", "keep_list"], "qa_tier2")
    summary = out["summary"]
    _require(
        summary,
        ["target", "total", "kept", "wfwhm", "medians", "dominant_reject_cause", "subs"],
        "qa_tier2.summary",
    )
    # The unanalysable sub is still present, with error set.
    assert len(summary["subs"]) == 3, "unanalysable subs must not be filtered out"
    by_name = {s["name"]: s for s in summary["subs"]}
    assert len(by_name) == 3, "sub names must be unique — they are the join key"

    for sub in summary["subs"]:
        _require(sub, ["name", "verdict", "reasons", "metrics"], "qa_tier2.subs[]")
        _require(
            sub["metrics"],
            [
                "star_count", "fwhm", "hfr", "eccentricity",
                "snr", "background", "scattered_light", "error",
            ],
            "qa_tier2.subs[].metrics",
        )
    # Strict JSON: no NaN/Infinity tokens may reach a consumer.
    json.loads(json.dumps(out, allow_nan=False))


# --- list_projects ---------------------------------------------------------


def test_list_projects_carries_full_session_history(tmp_path):
    """``sessions[]`` and its fields are required — the history table is built from it.

    Their archive de-duplicates by observing night using ``date_utc``. If a
    ``detail="summary"`` mode is added it MUST omit the ``sessions`` key entirely
    rather than return an empty list: omission fails their parse loudly, an empty
    list silently empties the table, which is the worse failure.
    """
    c = _controller(tmp_path)
    asyncio.run(c.log_session_result("M76", 102.3, 614, 614, notes="clean"))

    # detail="full" is what carries sessions[]; summary omits the key by design.
    out = asyncio.run(c.list_projects(detail="full"))
    _require(out, ["ok", "projects", "count"], "list_projects")

    project = out["projects"][0]
    _require(
        project,
        [
            "target_id", "target_name", "goal_minutes", "collected_minutes",
            "status", "created_utc", "updated_utc", "sessions", "notes",
        ],
        "list_projects.projects[]",
    )
    session = project["sessions"][0]
    _require(
        session,
        ["date_utc", "integration_minutes", "subs_total", "subs_kept", "notes",
         "median_fwhm"],
        "list_projects.projects[].sessions[]",
    )


# --- timestamp shapes ------------------------------------------------------


def test_timestamp_shapes_are_pinned_per_layer(tmp_path):
    """Two shapes exist and both are load-bearing — do not unify them silently.

    The planning layer emits NAIVE ISO (no offset); the projects/provenance layer
    emits OFFSET-BEARING ISO. The Console team asked us to pin "the naive shape",
    but that is only half the story: pinning the wrong one per field would break
    them just as effectively. Each is asserted where it actually occurs.
    """
    from seestar_mcp.planning.astro import dark_window
    from seestar_mcp.planning.site import SiteProfile

    # Planning layer: NAIVE (this is what feeds dark_window_utc / best_window_utc).
    start, end = dark_window(
        SiteProfile(name="x", lat_deg=45.0, lon_deg=-75.0), "2026-08-01T04:00:00Z"
    )
    for value in (start, end):
        assert NAIVE_ISO.match(value), (
            f"planning timestamps are naive (no offset); got {value!r}"
        )

    # Projects layer: OFFSET-BEARING.
    c = _controller(tmp_path)
    asyncio.run(c.log_session_result("M76", 10.0, 60, 60))
    session = asyncio.run(c.list_projects(detail="full"))["projects"][0]["sessions"][0]
    assert OFFSET_ISO.match(session["date_utc"]), (
        f"projects timestamps are offset-bearing; got {session['date_utc']!r}"
    )


# --- get_target_observability: invariants, not presence ---------------------


def test_observability_minutes_are_invariant_not_merely_present():
    """Pin the QUANTITY, not the key — presence cannot see semantic drift.

    The Console turns these values into geometry: the sweet-band timeline is
    positioned by arithmetic on the dark window and the ``dark_minutes_*`` pair.
    A units change (minutes → seconds), a sign flip, or a reference-frame change
    yields a number that is present, numeric, plausible and WRONG, and they draw a
    picture that looks correct. There is no absent state to fall back on, so
    ``_require`` is structurally blind to exactly the failure that matters here.

    Two of the three invariants they proposed hold. The third referenced a
    ``dark_minutes_total`` field that does not exist; the real upper bound is the
    dark window itself, which is both available and a stronger check — it ties the
    integrated minutes to the interval they were integrated over.
    """
    from datetime import datetime

    from seestar_mcp.planning.astro import dark_window, observability
    from seestar_mcp.planning.catalog import find_target
    from seestar_mcp.planning.site import SiteProfile

    # M82, not M31. At lat 45 its declination (+69.7) puts its MINIMUM altitude at
    # ~24.7 deg — above the 20-deg floor — so it is circumpolar-above-floor and
    # `above_floor` saturates the whole dark window at every season. That lets the
    # bound be a two-sided EQUALITY, which also catches a minutes-to-hours
    # deflation that a one-sided `<=` would pass. M31 (dec +41.3, min alt -3.7)
    # saturates only seasonally — 0 minutes in April, 332 in January — so it is a
    # fragile fixture for this assertion.
    site = SiteProfile(name="x", lat_deg=45.0, lon_deg=-75.0)
    when = "2026-08-01T04:00:00Z"
    obs = observability(site, find_target("M82"), when)

    # 1. Minutes, non-negative.
    assert obs.dark_minutes_above_floor >= 0
    assert obs.dark_minutes_in_sweet_band >= 0

    # 2. Bounded by the dark window they are integrated over (replaces the
    #    proposed dark_minutes_total, which is not a field we emit).
    start, end = dark_window(site, when)
    window_min = (
        datetime.fromisoformat(end) - datetime.fromisoformat(start)
    ).total_seconds() / 60.0

    # Slack is one 2-min grid step PLUS an epsilon. A bare `+ 2.0` has zero margin
    # and does fire on legitimate values: measured excesses of +2.0069 (M31,
    # 2026-10-15) and +2.0098 (M82, 2026-04-15) come from counting inclusive grid
    # samples against a non-multiple-of-step window, not from any error.
    GRID_STEP_MIN = 2.0
    EPS = 0.1
    assert obs.dark_minutes_above_floor <= window_min + GRID_STEP_MIN + EPS, (
        "minutes above the floor cannot exceed the dark window"
    )
    # Two-sided: for a circumpolar-above-floor target this must SATURATE the
    # window. A one-sided bound would pass a minutes->hours deflation silently.
    assert abs(obs.dark_minutes_above_floor - window_min) <= GRID_STEP_MIN + EPS, (
        "M82 is above the floor all night at this latitude; above_floor must equal "
        "the dark window. A units deflation would show here and nowhere else."
    )

    # 3. The sweet band is a SUBSET of above-floor, so it can never exceed it.
    #    Structural, not incidental — astro.py builds `sweet` as `above_floor`
    #    with the ceiling condition ANDed on.
    assert obs.dark_minutes_in_sweet_band <= obs.dark_minutes_above_floor

    # A units change to seconds would break (2); a sign flip breaks (1); a
    # reference-frame change that lifts the target out of the window breaks (2).


def test_summary_omits_sessions_rather_than_emptying_it(tmp_path):
    """``detail="summary"`` must REMOVE the key, not return an empty list.

    An empty list parses cleanly and renders an empty history table — silently
    wrong, and indistinguishable from a project that genuinely has no sessions.
    Omission makes a consumer that needs the history fail loudly instead. This is
    the same argument that applies to ``targets_remaining`` in run_state.
    """
    c = _controller(tmp_path)
    asyncio.run(c.log_session_result("M76", 102.3, 614, 614, notes="clean"))

    summary = asyncio.run(c.list_projects())["projects"][0]
    assert "sessions" not in summary, "summary must omit the key, not empty it"
    assert summary["sessions_count"] == 1
    assert summary["last_session_utc"] is not None

    # detail="full" reproduces the historical payload exactly.
    full = asyncio.run(c.list_projects(detail="full"))["projects"][0]
    _require(full, ["sessions"], "list_projects(detail=full)")
    assert len(full["sessions"]) == 1


def test_last_session_utc_is_the_latest_date_not_the_last_element(tmp_path):
    """A backfilled session must not report an older date as "last".

    ``now_utc`` is caller-supplied (the determinism rule) and nothing sorts the
    list on load, so appending a session for an earlier night puts an older date
    in the final slot. ``sessions[-1]`` would report it as the most recent.
    """
    from seestar_mcp.planning.projects import log_session_result as _log

    p = tmp_path / "projects.json"
    _log("M76", "M76", integration_minutes=10, subs_total=1, subs_kept=1,
         now_utc="2026-08-01T04:00:00Z", path=p)
    # A session backfilled for an EARLIER night, appended afterwards.
    _log("M76", "M76", integration_minutes=10, subs_total=1, subs_kept=1,
         now_utc="2026-07-05T04:00:00Z", path=p)

    from seestar_mcp.planning.projects import load_projects
    from seestar_mcp.server import _project_payload

    project = load_projects(p)["M76"]
    summary = _project_payload(project, "summary")
    assert summary["sessions_count"] == 2
    assert summary["last_session_utc"] == "2026-08-01T04:00:00Z", (
        "last_session_utc must be the latest date, not the last list element"
    )


def test_recommend_projects_has_the_same_detail_escape_hatch(tmp_path):
    """Both project tools take ``detail`` — one escape hatch is not enough.

    If only ``list_projects`` gained the parameter, a consumer hitting the new
    summary default on ``recommend_projects`` would have no way to opt back out.
    """
    c = _controller(tmp_path)
    asyncio.run(c.log_session_result("M76", 10.0, 60, 60))

    summary = asyncio.run(c.recommend_projects())["projects"][0]
    assert "sessions" not in summary
    assert summary["sessions_count"] == 1

    full = asyncio.run(c.recommend_projects(detail="full"))["projects"][0]
    _require(full, ["sessions"], "recommend_projects(detail=full)")

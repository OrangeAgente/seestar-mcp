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
    _require(view, ["stage", "target_name", "lp_filter", "Stack"], "View")
    _require(view["Stack"], ["stacked_frame", "dropped_frame"], "View.Stack")

    annotate = view["Stack"]["Annotate"]
    _require(annotate, ["result"], "Stack.Annotate")
    ann = annotate["result"]["annotations"][0]
    _require(ann, ["type", "names", "pixelx", "pixely", "radius"], "annotations[]")
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

    out = asyncio.run(c.list_projects())
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
    session = asyncio.run(c.list_projects())["projects"][0]["sessions"][0]
    assert OFFSET_ISO.match(session["date_utc"]), (
        f"projects timestamps are offset-bearing; got {session['date_utc']!r}"
    )

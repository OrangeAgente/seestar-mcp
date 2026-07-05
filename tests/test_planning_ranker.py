"""Tests for the reasoned target ranker (Task 6).

The ranker's astronomy is injected via ``observability_fn`` so these tests are
fully deterministic and never touch astropy: each case hands the ranker a
hand-built :class:`Observability` record through the ``_obs`` helper.
"""

from __future__ import annotations

from seestar_mcp.planning.astro import Observability
from seestar_mcp.planning.catalog import DsoTarget
from seestar_mcp.planning.ranker import TargetPlan, rank_targets
from seestar_mcp.planning.site import SiteProfile
from seestar_mcp.planning.weather import ConditionsAssessment


def _obs(minutes_band, above_ceiling=False, sep=90.0, usable_sub_minutes=40.0):
    return Observability(
        target_id="t",
        max_alt_deg=50.0,
        transit_utc="2026-07-05T04:00:00Z",
        rise_utc=None,
        set_utc=None,
        dark_minutes_above_floor=minutes_band + 30,
        dark_minutes_in_sweet_band=minutes_band,
        field_rotation_deg_per_hr_at_transit=10.0,
        usable_sub_minutes=usable_sub_minutes,
        transits_above_ceiling=above_ceiling,
        moon_sep_deg=sep,
        moon_alt_deg=20.0,
        moon_illum_frac=0.1,
        best_window_utc=("a", "b"),
    )


def _cond(go=True):
    return ConditionsAssessment(
        go=go,
        suitability=90 if go else 0,
        cloud_cover_pct=5,
        dew_risk="low",
        wind_kph=5,
        transparency="good",
        seeing="good",
        moon_illum_frac=0.1,
        dark_window_utc=("2026-07-05T02:00:00Z", "2026-07-05T08:00:00Z"),
        source="open-meteo",
        reasons=[],
    )


def test_more_sweet_band_time_ranks_higher():
    site = SiteProfile(name="x", lat_deg=40, lon_deg=-74, bortle=6)
    cat = [
        DsoTarget("A", "A", 0, 0, "emission_nebula", 20, 7),
        DsoTarget("B", "B", 0, 0, "emission_nebula", 20, 7),
    ]
    obs_map = {"A": _obs(120), "B": _obs(20)}
    plans = rank_targets(
        site,
        "2026-07-05T04:00:00Z",
        cat,
        _cond(),
        observability_fn=lambda s, t, w: obs_map[t.id],
    )
    assert [p.target.id for p in plans] == ["A", "B"]
    assert plans[0].reasons  # non-empty
    assert isinstance(plans[0], TargetPlan)


def test_never_up_target_excluded():
    site = SiteProfile(name="x", lat_deg=40, lon_deg=-74)
    cond = ConditionsAssessment(
        None, 0, None, "low", None, None, None, 0.1, ("a", "b"), "unknown", []
    )
    cat = [DsoTarget("Z", "Z", 0, -80, "galaxy", 10, 9)]
    plans = rank_targets(
        site,
        "2026-07-05T04:00:00Z",
        cat,
        cond,
        observability_fn=lambda s, t, w: _obs(0),  # zero sweet-band
    )
    assert plans == []  # dropped


def test_transits_above_ceiling_gets_field_rotation_reason():
    site = SiteProfile(name="x", lat_deg=40, lon_deg=-74, bortle=6)
    cat = [DsoTarget("A", "A", 0, 0, "emission_nebula", 20, 7)]
    plans = rank_targets(
        site,
        "2026-07-05T04:00:00Z",
        cat,
        _cond(),
        observability_fn=lambda s, t, w: _obs(90, above_ceiling=True),
    )
    assert len(plans) == 1
    joined = " ".join(plans[0].reasons).lower()
    assert "field rotation" in joined
    assert "ceiling" in joined


def test_sweet_band_target_has_no_sub_trail_reason():
    # A sweet-band target that never transits above the ceiling must NOT be
    # tagged "subs trail near transit" — that note is gated on near-zenith
    # transits (real hardware banks clean 10 s subs well below the ceiling).
    site = SiteProfile(name="x", lat_deg=40, lon_deg=-74, bortle=6)
    cat = [DsoTarget("A", "A", 0, 0, "emission_nebula", 20, 7)]
    plans = rank_targets(
        site,
        "2026-07-05T04:00:00Z",
        cat,
        _cond(),
        observability_fn=lambda s, t, w: _obs(
            90, above_ceiling=False, usable_sub_minutes=0.0
        ),
    )
    assert len(plans) == 1
    joined = " ".join(plans[0].reasons).lower()
    assert "subs trail" not in joined


def test_above_ceiling_target_gets_sub_trail_reason():
    # Near-zenith (above-ceiling) transit still gets both the field-rotation
    # reason and the sub-trail note.
    site = SiteProfile(name="x", lat_deg=40, lon_deg=-74, bortle=6)
    cat = [DsoTarget("A", "A", 0, 0, "emission_nebula", 20, 7)]
    plans = rank_targets(
        site,
        "2026-07-05T04:00:00Z",
        cat,
        _cond(),
        observability_fn=lambda s, t, w: _obs(90, above_ceiling=True),
    )
    assert len(plans) == 1
    joined = " ".join(plans[0].reasons).lower()
    assert "field rotation" in joined
    assert "subs trail" in joined


def test_recommended_subs_is_sweet_band_seconds_over_exposure():
    site = SiteProfile(name="x", lat_deg=40, lon_deg=-74, bortle=6)
    cat = [DsoTarget("A", "A", 0, 0, "emission_nebula", 20, 7)]
    plans = rank_targets(
        site,
        "2026-07-05T04:00:00Z",
        cat,
        _cond(),
        observability_fn=lambda s, t, w: _obs(120),
    )
    # 120 min sweet band at 10 s exposure -> 120 * 60 / 10 = 720 subs.
    assert plans[0].recommended_exposure_s == 10
    assert plans[0].recommended_subs == 720

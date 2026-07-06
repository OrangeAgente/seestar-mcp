"""Tests for the deterministic observability engine (astro.py).

Physics that is exact (the field-rotation formula) is hand-checked; astropy-
derived quantities (M27 transit altitude, moon values) are pinned to tolerances.
All calls take an explicit ``when_utc`` so the results are reproducible.
"""

from seestar_mcp.planning.astro import (
    _to_time,
    azalt_at,
    dark_window,
    field_rotation_rate,
    moon_illumination,
    observability,
)
from seestar_mcp.planning.catalog import find_target
from seestar_mcp.planning.site import SiteProfile


def test_to_time_accepts_offset_suffixed_iso():
    # datetime.now(timezone.utc).isoformat() yields '...+00:00' with microseconds,
    # which the controller passes to the planner whenever date=None. astropy's
    # Time() rejects the offset, so this must be normalized, not passed through raw.
    a = _to_time("2026-07-06T01:37:39.165314+00:00")
    b = _to_time("2026-07-06T01:37:39.165314")
    assert abs((a - b).sec) < 1e-6
    # 'Z' must still work, and a non-zero offset must be converted to real UTC.
    assert abs((_to_time("2026-07-06T01:37:39Z") - _to_time("2026-07-06T01:37:39")).sec) < 1e-6
    assert abs((_to_time("2026-07-06T06:37:39+05:00") - _to_time("2026-07-06T01:37:39")).sec) < 1e-6
    # The real crash path: an offset-suffixed 'now' flowing through dark_window.
    site = SiteProfile(name="x", lat_deg=45.42, lon_deg=-75.70)
    dusk, dawn = dark_window(site, "2026-07-06T01:37:39.165314+00:00")
    assert dusk < dawn


def test_field_rotation_formula_hand_check():
    # 15.041 * cos(40) * cos(0) / cos(45) = 16.29 deg/hr (exact formula check).
    r = field_rotation_rate(lat_deg=40.0, az_deg=0.0, alt_deg=45.0)
    assert abs(r - 16.29) < 0.1
    # az=90 (due east) -> cos(az)=0 -> rate ~0.
    assert field_rotation_rate(40.0, 90.0, 45.0) < 0.01


def test_m27_transit_altitude_and_ceiling_flag():
    # transit alt ~ 90 - |lat - dec|; M27 dec +22.72, lat 40 -> ~72.7 deg (> 60 ceiling).
    site = SiteProfile(name="x", lat_deg=40.0, lon_deg=-74.0, bortle=6)
    obs = observability(site, find_target("M27"), "2026-07-05T04:00:00Z")
    assert 71.0 < obs.max_alt_deg < 74.0
    assert obs.transits_above_ceiling is True
    assert obs.dark_minutes_in_sweet_band <= obs.dark_minutes_above_floor
    assert 0.0 <= obs.moon_illum_frac <= 1.0


def test_dark_window_is_night():
    site = SiteProfile(name="x", lat_deg=40.0, lon_deg=-74.0)
    dusk, dawn = dark_window(site, "2026-07-05T04:00:00Z")
    assert dusk < dawn  # ISO strings compare lexically for same-format UTC


def test_moon_illumination_is_a_fraction():
    # Illuminated fraction is always a physical 0..1 value at any instant.
    frac = moon_illumination("2026-07-05T04:00:00Z")
    assert 0.0 <= frac <= 1.0


def test_azalt_at_single_instant_matches_transit():
    # The single-instant az/alt helper must agree with the observability engine:
    # evaluated at the target's transit time it returns the max altitude.
    site = SiteProfile(name="x", lat_deg=40.0, lon_deg=-74.0)
    t = find_target("M27")
    obs = observability(site, t, "2026-07-05T04:00:00Z")
    az, alt = azalt_at(site, t, obs.transit_utc)
    assert 0.0 <= az <= 360.0
    assert abs(alt - obs.max_alt_deg) < 0.5

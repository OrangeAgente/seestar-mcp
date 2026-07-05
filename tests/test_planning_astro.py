"""Tests for the deterministic observability engine (astro.py).

Physics that is exact (the field-rotation formula) is hand-checked; astropy-
derived quantities (M27 transit altitude, moon values) are pinned to tolerances.
All calls take an explicit ``when_utc`` so the results are reproducible.
"""

from seestar_mcp.planning.astro import (
    dark_window,
    field_rotation_rate,
    moon_illumination,
    observability,
)
from seestar_mcp.planning.catalog import find_target
from seestar_mcp.planning.site import SiteProfile


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

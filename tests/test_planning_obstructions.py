"""Tests for the obstruction learner core + haversine location status.

Four discriminators (clouds cannot fake all four): cross-night persistence,
weather-gating, spatial isolation, altitude prior — plus location scoping so a
mask learned at home never surfaces at a different site.
"""

from seestar_mcp.planning.obstructions import (
    haversine_km,
    load_sky_log,
    location_status,
    record_sky_result,
    suggest_obstructions,
)
from seestar_mcp.planning.site import SiteProfile


def test_haversine_and_location_status():
    assert abs(haversine_km(40.0, -74.0, 40.0, -74.0)) < 0.01
    d = haversine_km(40.0, -74.0, 40.5, -74.0)  # ~55.6 km
    assert 50 < d < 60
    prof = SiteProfile(name="x", lat_deg=40.0, lon_deg=-74.0)  # default tol 1.0 km (added in T2)
    ok, dist = location_status(prof, 40.001, -74.0)  # ~0.1 km -> within
    assert ok is True


def test_clear_sky_failures_build_candidate(tmp_path):
    p = tmp_path / "sky.json"
    # a low-alt bin (az~90, alt~22) fails on distinct clear nights, neighbors OK
    for night in ("2026-07-04", "2026-07-05", "2026-07-06", "2026-07-07"):
        record_sky_result(
            92.0, 22.0, ok=False, weather_ok=True, now_utc=f"{night}T04:00:00Z",
            lat=40.0, lon=-74.0, path=p,
        )
        record_sky_result(
            60.0, 22.0, ok=True, weather_ok=True, now_utc=f"{night}T04:10:00Z",
            lat=40.0, lon=-74.0, path=p,
        )
    cands = suggest_obstructions(p, cur_lat=40.0, cur_lon=-74.0)
    assert cands and any(
        c.az_min_deg <= 92 <= c.az_max_deg and c.alt_min_deg >= 20 for c in cands
    )


def test_bad_weather_failures_excluded(tmp_path):
    p = tmp_path / "sky.json"
    for night in ("2026-07-04", "2026-07-05", "2026-07-06"):
        record_sky_result(
            92.0, 22.0, ok=False, weather_ok=False, now_utc=f"{night}T04:00:00Z",
            lat=40.0, lon=-74.0, path=p,
        )
    assert suggest_obstructions(p, cur_lat=40.0, cur_lon=-74.0) == []  # weather-excluded


def test_high_altitude_never_obstruction(tmp_path):
    p = tmp_path / "sky.json"
    for night in ("2026-07-04", "2026-07-05", "2026-07-06", "2026-07-07"):
        record_sky_result(
            92.0, 70.0, ok=False, weather_ok=True, now_utc=f"{night}T04:00:00Z",
            lat=40.0, lon=-74.0, path=p,
        )
    assert suggest_obstructions(p, cur_lat=40.0, cur_lon=-74.0) == []  # altitude prior


def test_far_location_records_excluded(tmp_path):
    p = tmp_path / "sky.json"
    for night in ("2026-07-04", "2026-07-05", "2026-07-06", "2026-07-07"):
        record_sky_result(
            92.0, 22.0, ok=False, weather_ok=True, now_utc=f"{night}T04:00:00Z",
            lat=10.0, lon=10.0, path=p,
        )
    assert suggest_obstructions(p, cur_lat=40.0, cur_lon=-74.0) == []  # different site


def test_missing_store_is_empty(tmp_path):
    assert load_sky_log(tmp_path / "nope.json") == {}

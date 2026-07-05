"""Tests for the observing-site profile (persistence, GPS fallback, horizon mask)."""

from seestar_mcp.planning.site import (
    SiteProfile,
    is_blocked,
    load_site,
    save_site,
    site_from_gps,
)


def test_profile_roundtrip(tmp_path):
    p = SiteProfile(
        name="Backyard",
        lat_deg=40.0,
        lon_deg=-74.0,
        bortle=6,
        horizon_mask=[(45.0, 135.0, 30.0)],
    )
    path = save_site(p, tmp_path / "site.json")
    got = load_site(path)
    assert got.name == "Backyard" and got.bortle == 6
    assert got.horizon_mask == [(45.0, 135.0, 30.0)]


def test_load_missing_returns_none(tmp_path):
    assert load_site(tmp_path / "nope.json") is None


def test_horizon_mask_blocking():
    p = SiteProfile(
        name="x", lat_deg=40, lon_deg=-74, horizon_mask=[(45.0, 135.0, 30.0)]
    )
    assert is_blocked(p, az_deg=90, alt_deg=25) is True  # in arc, below 30
    assert is_blocked(p, az_deg=90, alt_deg=35) is False  # in arc, above 30
    # az 200 is outside the 45-135 arc, and 25 > the global floor 20 -> not blocked
    assert is_blocked(p, az_deg=200, alt_deg=25) is False
    assert is_blocked(p, az_deg=200, alt_deg=15) is True  # below global floor


def test_site_from_gps():
    s = site_from_gps(37.5, -122.0)
    assert s.lat_deg == 37.5 and s.bortle is None


def test_profile_has_location_tolerance_default():
    assert SiteProfile(name="x", lat_deg=0, lon_deg=0).location_tolerance_km == 1.0


def test_location_tolerance_roundtrips(tmp_path):
    p = SiteProfile(
        name="Backyard", lat_deg=40.0, lon_deg=-74.0, location_tolerance_km=2.5
    )
    got = load_site(save_site(p, tmp_path / "site.json"))
    assert got.location_tolerance_km == 2.5

from seestar_mcp.planning.lightpollution import bortle_for, lp_suitability
from seestar_mcp.planning.site import SiteProfile


def test_bortle_uses_profile_then_default():
    assert bortle_for(SiteProfile(name="x", lat_deg=0, lon_deg=0, bortle=8)) == 8
    assert bortle_for(SiteProfile(name="x", lat_deg=0, lon_deg=0)) == 5


def test_lp_suitability_favours_emission_under_high_lp():
    # bright city (Bortle 8): emission/narrowband targets favoured over galaxies
    assert lp_suitability("emission_nebula", 8) > lp_suitability("galaxy", 8)
    # dark site (Bortle 3): galaxies fine
    assert lp_suitability("galaxy", 3) >= 0.8


def test_lp_suitability_clamps_to_unit_interval_for_extreme_bortle():
    for target_type in (
        "emission_nebula",
        "planetary_nebula",
        "supernova_remnant",
        "galaxy",
        "reflection_nebula",
        "open_cluster",
        "globular_cluster",
        "other",
        "totally_unknown_type",
    ):
        for bortle in (1, 9):
            value = lp_suitability(target_type, bortle)
            assert 0.0 <= value <= 1.0

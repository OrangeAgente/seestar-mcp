from seestar_mcp.planning.catalog import DsoTarget, find_target, load_catalog


def test_catalog_loads_known_targets():
    cat = load_catalog()
    assert len(cat) >= 30
    ids = {t.id for t in cat}
    assert {"M27", "M31", "M42", "M13", "M51"} <= ids
    for t in cat:
        assert -360 <= t.ra_deg <= 360 and -90 <= t.dec_deg <= 90
        assert t.type and t.size_arcmin > 0


def test_find_target_by_id_and_name():
    assert find_target("m27").id == "M27"
    assert find_target("Dumbbell Nebula").id == "M27"
    assert find_target("nonexistent") is None
    assert isinstance(find_target("m27"), DsoTarget)

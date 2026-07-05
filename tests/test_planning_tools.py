"""Tests for the 5 MCP planning tools (site / conditions / observability / plan).

These drive the :class:`SeestarController` directly and mock the astropy /
weather engine so the tool layer is exercised deterministically and offline.
The registration test confirms all 5 tools are exposed (bringing the server to
23 tools total).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import seestar_mcp.server as server_mod
from seestar_mcp.config import Settings
from seestar_mcp.planning.astro import Observability
from seestar_mcp.planning.catalog import DsoTarget
from seestar_mcp.planning.ranker import TargetPlan
from seestar_mcp.planning.weather import ConditionsAssessment
from seestar_mcp.server import SeestarController, mcp

PLANNING_TOOLS = {
    "get_site_profile",
    "set_site_profile",
    "assess_conditions",
    "get_target_observability",
    "plan_targets",
}


def _controller(tmp_path) -> SeestarController:
    """A controller whose site profile persists under ``tmp_path``."""
    return SeestarController(
        settings=Settings(_env_file=None, data_dir=tmp_path),
        provenance=MagicMock(),
        alpaca=AsyncMock(),
        data=AsyncMock(),
        tier1=AsyncMock(),
    )


async def _tool_names() -> set[str]:
    return {t.name for t in await mcp.list_tools()}


def test_planning_tools_registered():
    names = asyncio.run(_tool_names())
    assert PLANNING_TOOLS <= names
    assert len(asyncio.run(mcp.list_tools())) == 23


def test_set_then_get_site_profile(tmp_path):
    c = _controller(tmp_path)
    r = asyncio.run(c.set_site_profile(name="Yard", lat=40.0, lon=-74.0, bortle=6))
    assert r["ok"] is True
    assert r["profile"]["bortle"] == 6

    g = asyncio.run(c.get_site_profile())
    assert g["ok"] is True
    assert g["profile"]["bortle"] == 6
    assert g["profile"]["name"] == "Yard"


def test_get_site_profile_none_when_unset(tmp_path):
    c = _controller(tmp_path)
    g = asyncio.run(c.get_site_profile())
    assert g["ok"] is False
    assert "site" in g["error"].lower()


def _canned_conditions() -> ConditionsAssessment:
    return ConditionsAssessment(
        go=True,
        suitability=88,
        cloud_cover_pct=5.0,
        dew_risk="low",
        wind_kph=6.0,
        transparency="good",
        seeing="good",
        moon_illum_frac=0.1,
        dark_window_utc=("2026-07-05T02:00:00Z", "2026-07-05T08:00:00Z"),
        source="open-meteo",
        reasons=["cloud cover 5%"],
    )


def _canned_plan() -> TargetPlan:
    target = DsoTarget(
        id="M27", name="Dumbbell Nebula", ra_deg=299.9, dec_deg=22.7,
        type="planetary_nebula", size_arcmin=8.0, magnitude=7.4,
    )
    obs = Observability(
        target_id="M27", max_alt_deg=72.7, transit_utc="2026-07-05T04:00:00Z",
        rise_utc=None, set_utc=None, dark_minutes_above_floor=180.0,
        dark_minutes_in_sweet_band=120.4, field_rotation_deg_per_hr_at_transit=10.0,
        usable_sub_minutes=40.0, transits_above_ceiling=True, moon_sep_deg=95.3,
        moon_alt_deg=20.0, moon_illum_frac=0.1,
        best_window_utc=("2026-07-05T03:00:00Z", "2026-07-05T05:00:00Z"),
    )
    return TargetPlan(
        target=target, score=91, reasons=["120 min clean sweet-band time"],
        best_window_utc=obs.best_window_utc, recommended_subs=722,
        recommended_exposure_s=10, framing_note="fits FOV (8')", observability=obs,
    )


def test_plan_targets_with_mocked_engine(tmp_path, monkeypatch):
    c = _controller(tmp_path)
    assert asyncio.run(c.set_site_profile(name="Yard", lat=40.0, lon=-74.0, bortle=6))["ok"]

    monkeypatch.setattr(server_mod, "dark_window", lambda site, when: ("a", "b"))
    monkeypatch.setattr(server_mod, "moon_illumination", lambda when: 0.1)
    monkeypatch.setattr(server_mod, "load_catalog", lambda: [])

    async def _fake_assess(site, window, illum):
        return _canned_conditions()

    monkeypatch.setattr(server_mod, "assess_conditions_weather", _fake_assess)
    monkeypatch.setattr(
        server_mod, "rank_targets",
        lambda *a, **k: [_canned_plan()],
    )

    r = asyncio.run(c.plan_targets())
    assert r["ok"] is True
    assert r["count"] == 1
    assert r["conditions"] == {"go": True, "suitability": 88, "source": "open-meteo"}
    t = r["targets"][0]
    assert t["id"] == "M27"
    assert t["type"] == "planetary_nebula"
    assert t["score"] == 91
    assert t["recommended_subs"] == 722
    assert t["max_alt_deg"] == 72.7
    assert t["moon_sep_deg"] == 95.3
    assert t["sweet_band_min"] == 120
    # Compact: no bulky nested observability dumped per target.
    assert "observability" not in t


def test_unknown_target(tmp_path):
    c = _controller(tmp_path)
    asyncio.run(c.set_site_profile(name="Yard", lat=40.0, lon=-74.0))
    r = asyncio.run(c.get_target_observability("NotARealObject"))
    assert r["ok"] is False
    assert "unknown target" in r["error"].lower()

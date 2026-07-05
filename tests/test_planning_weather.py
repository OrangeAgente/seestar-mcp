"""Tests for seestar_mcp.planning.weather.

Uses ``respx`` to mock the single outbound Open-Meteo HTTPS GET (the only
network call in the whole planner). ``asyncio_mode=auto`` means async tests need
no decorator, but ``@respx.mock`` is applied per test. The key invariant under
test: a weather failure is NON-FATAL — it yields ``go=None``/``source="unknown"``
rather than raising, so planning can proceed on observability alone.
"""

from __future__ import annotations

import httpx
import respx

from seestar_mcp.planning.site import SiteProfile
from seestar_mcp.planning.weather import (
    ConditionsAssessment,
    OpenMeteoSource,
    assess_conditions,
)

SITE = SiteProfile(name="x", lat_deg=40.0, lon_deg=-74.0)
WINDOW = ("2026-07-05T02:00:00Z", "2026-07-05T08:00:00Z")


@respx.mock
async def test_clear_night_is_go():
    respx.get(url__startswith="https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-07-05T02:00", "2026-07-05T03:00"],
                    "cloudcover_low": [0, 5],
                    "cloudcover_mid": [0, 0],
                    "cloudcover_high": [10, 10],
                    "relativehumidity_2m": [60, 62],
                    "dewpoint_2m": [8, 8],
                    "temperature_2m": [18, 17],
                    "windspeed_10m": [5, 6],
                    "precipitation_probability": [0, 0],
                }
            },
        )
    )
    a = await assess_conditions(SITE, WINDOW, moon_illum_frac=0.1)
    assert a.go is True
    assert a.suitability >= 60
    assert a.source == "open-meteo"
    assert a.cloud_cover_pct == 10.0
    assert a.dew_risk == "low"
    assert a.wind_kph == 6.0
    assert a.moon_illum_frac == 0.1
    assert a.dark_window_utc == WINDOW


@respx.mock
async def test_heavily_clouded_is_no_go():
    respx.get(url__startswith="https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-07-05T02:00", "2026-07-05T03:00"],
                    "cloudcover_low": [90, 95],
                    "cloudcover_mid": [80, 85],
                    "cloudcover_high": [70, 60],
                    "relativehumidity_2m": [95, 96],
                    "dewpoint_2m": [16, 16],
                    "temperature_2m": [17, 17],
                    "windspeed_10m": [10, 12],
                    "precipitation_probability": [80, 90],
                }
            },
        )
    )
    a = await assess_conditions(SITE, WINDOW, moon_illum_frac=0.1)
    assert a.go is False
    assert a.cloud_cover_pct == 95.0
    # A cloud reason must be present and human-readable.
    assert any("cloud" in r.lower() for r in a.reasons)
    assert a.source == "open-meteo"


@respx.mock
async def test_network_failure_is_unknown_not_fatal():
    respx.get(url__startswith="https://api.open-meteo.com").mock(
        side_effect=httpx.ConnectError("down")
    )
    a = await assess_conditions(SITE, WINDOW, moon_illum_frac=0.1)
    assert isinstance(a, ConditionsAssessment)
    assert a.go is None
    assert a.source == "unknown"
    assert a.suitability == 0
    assert a.cloud_cover_pct is None
    assert a.dew_risk == "unknown"
    assert a.wind_kph is None
    assert "manual" in " ".join(a.reasons).lower()
    # Non-fatal: the caller-supplied moon + window still surface.
    assert a.moon_illum_frac == 0.1
    assert a.dark_window_utc == WINDOW


@respx.mock
async def test_malformed_json_is_unknown_not_fatal():
    # Shape error (missing "hourly") must be caught like a network error.
    respx.get(url__startswith="https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json={"unexpected": True})
    )
    a = await assess_conditions(SITE, WINDOW, moon_illum_frac=0.5)
    assert a.go is None
    assert a.source == "unknown"


@respx.mock
async def test_bright_moon_lowers_suitability_but_weather_drives_go():
    payload = {
        "hourly": {
            "time": ["2026-07-05T02:00", "2026-07-05T03:00"],
            "cloudcover_low": [0, 0],
            "cloudcover_mid": [0, 0],
            "cloudcover_high": [0, 0],
            "relativehumidity_2m": [50, 50],
            "dewpoint_2m": [5, 5],
            "temperature_2m": [18, 18],
            "windspeed_10m": [5, 5],
            "precipitation_probability": [0, 0],
        }
    }
    route = respx.get(url__startswith="https://api.open-meteo.com/v1/forecast")
    route.mock(return_value=httpx.Response(200, json=payload))
    dark = await assess_conditions(SITE, WINDOW, moon_illum_frac=0.0)
    route.mock(return_value=httpx.Response(200, json=payload))
    bright = await assess_conditions(SITE, WINDOW, moon_illum_frac=1.0)
    # Full moon costs some suitability but does not flip the weather verdict.
    assert bright.suitability < dark.suitability
    assert bright.go is True and dark.go is True


@respx.mock
async def test_openmeteo_source_directly_and_query_params():
    route = respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-07-05T01:00", "2026-07-05T02:00", "2026-07-05T09:00"],
                    "cloudcover_low": [50, 20, 99],
                    "cloudcover_mid": [0, 0, 0],
                    "cloudcover_high": [0, 0, 0],
                    "relativehumidity_2m": [70, 70, 99],
                    "dewpoint_2m": [10, 10, 10],
                    "temperature_2m": [12, 12, 12],
                    "windspeed_10m": [30, 30, 99],
                    "precipitation_probability": [10, 10, 99],
                }
            },
        )
    )
    a = await OpenMeteoSource().assess(SITE, WINDOW)
    assert route.called
    request = route.calls.last.request
    params = request.url.params
    assert params["latitude"] == "40.0"
    assert params["longitude"] == "-74.0"
    assert params["timezone"] == "UTC"
    assert params["forecast_days"] == "2"
    assert "cloudcover_low" in params["hourly"]
    # Only the in-window rows (02:00) are considered — the 01:00 and 09:00 rows,
    # with their extreme values, are filtered out.
    assert a.cloud_cover_pct == 20.0
    assert a.wind_kph == 30.0

"""Pre-session sky-conditions assessment (weather) for the observing planner.

This is the ONLY module in ``planning/`` that touches the network: a single
outbound HTTPS GET to ``api.open-meteo.com`` (a free, key-less forecast API).
Everything is built so a weather failure is **non-fatal** — on any network or
response-shape error the assessment degrades to ``go=None`` /
``source="unknown"`` and the caller is told to eyeball the sky manually, rather
than an exception aborting the whole plan.

Design choices (all documented inline so a verdict is never a black box):

* ``cloud_cover_pct`` is the worst-case cloud over the dark window: the *max*
  over the in-window hours of the *max* of the three cloud layers
  (low/mid/high). Any thick layer ruins a sub, so we take the pessimistic view
  rather than summing or averaging layers.
* ``dew_risk`` comes from the *minimum* temperature-minus-dewpoint spread over
  the window (the moment the optics are closest to dewing up).
* ``suitability`` (0..100) starts at 100 and subtracts cloud, a wind penalty and
  a precip penalty (see :func:`_score`).
* ``go`` requires a decent suitability AND a low precipitation probability.

The moon is folded in by :func:`assess_conditions` (the caller supplies its
illumination from the deterministic astro engine — this module never reads a
clock or computes ephemeris). A bright moon lowers ``suitability`` but does NOT
force a no-go: weather drives ``go``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import httpx

if TYPE_CHECKING:
    from .site import SiteProfile

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

#: Hourly variables requested from Open-Meteo (comma-joined into one param).
_HOURLY_VARS = (
    "cloudcover_low",
    "cloudcover_mid",
    "cloudcover_high",
    "relativehumidity_2m",
    "dewpoint_2m",
    "temperature_2m",
    "windspeed_10m",
    "precipitation_probability",
)

#: ``go`` needs suitability at or above this, and precip below _GO_PRECIP_PCT.
_GO_SUITABILITY = 50
_GO_PRECIP_PCT = 40.0

#: Moon: a full moon subtracts at most this many suitability points, scaled by
#: illuminated fraction. Tunable; kept modest so weather remains the driver.
_MOON_MAX_PENALTY = 15


@dataclass
class ConditionsAssessment:
    """A reason-tagged go/no-go sky verdict over a dark window.

    ``go`` is ``None`` when weather is unknown (offline) — planning proceeds on
    observability alone. Every non-obvious number is explained in ``reasons``.
    """

    go: bool | None
    suitability: int
    cloud_cover_pct: float | None
    dew_risk: str
    wind_kph: float | None
    transparency: str | None
    seeing: str | None
    moon_illum_frac: float
    dark_window_utc: tuple[str, str]
    source: str
    reasons: list[str]


@runtime_checkable
class WeatherSource(Protocol):
    """A pluggable weather backend. Open-Meteo is the Phase-1 default.

    The seam exists so a future keyed provider (reading its API key from the
    secret store, never config) can drop in without touching the planner.
    """

    async def assess(
        self, site: SiteProfile, window_utc: tuple[str, str]
    ) -> ConditionsAssessment:
        """Assess sky conditions for ``site`` over the ``(start, end)`` window."""
        ...


def _hour_key(iso: str) -> str:
    """Reduce an ISO timestamp to Open-Meteo's minute-resolution key.

    Window bounds arrive as e.g. ``"2026-07-05T02:00:00Z"`` while Open-Meteo
    returns ``"2026-07-05T02:00"``; both truncate to the same 16-char prefix,
    so the (fixed-width, UTC) strings compare lexically.
    """
    return iso[:16]


def _window_rows(hourly: dict, window_utc: tuple[str, str]) -> list[int]:
    """Return indices of hourly rows falling within ``window_utc``.

    Falls back to every row if none match (defensive: a mismatched window
    should still yield a best-effort assessment rather than an empty one).
    """
    times = hourly["time"]
    start, end = _hour_key(window_utc[0]), _hour_key(window_utc[1])
    idx = [i for i, t in enumerate(times) if start <= t <= end]
    return idx or list(range(len(times)))


def _dew_risk(min_spread: float) -> str:
    """Dew risk from the tightest temperature-minus-dewpoint spread (deg C)."""
    if min_spread < 2.0:
        return "high"
    if min_spread < 5.0:
        return "moderate"
    return "low"


def _transparency(cloud_pct: float, max_humidity: float) -> str:
    """Coarse atmospheric-transparency proxy from cloud and humidity.

    Thick cloud or very humid air scatters starlight (poor); a dry, clear sky
    is good; everything else is average. Thresholds are deliberately simple and
    tunable.
    """
    if cloud_pct >= 60.0 or max_humidity >= 85.0:
        return "poor"
    if cloud_pct <= 20.0 and max_humidity <= 70.0:
        return "good"
    return "average"


def _seeing(wind_kph: float) -> str:
    """Coarse seeing proxy from wind speed (calm air steadies the image)."""
    if wind_kph <= 15.0:
        return "good"
    if wind_kph <= 30.0:
        return "average"
    return "poor"


def _score(cloud_pct: float, wind_kph: float, max_precip: float) -> int:
    """Blend cloud, wind and precip into a clamped 0..100 suitability.

    Start at a perfect 100 and subtract: the full cloud percentage (the biggest
    lever), a wind penalty that only bites above 25 kph (capped at 20), and half
    the precipitation probability.
    """
    wind_penalty = min(20.0, max(0.0, wind_kph - 25.0))
    precip_penalty = 0.5 * max_precip
    raw = 100.0 - cloud_pct - wind_penalty - precip_penalty
    return int(max(0.0, min(100.0, raw)))


def _unknown(window_utc: tuple[str, str]) -> ConditionsAssessment:
    """The non-fatal fallback used whenever weather cannot be determined."""
    return ConditionsAssessment(
        go=None,
        suitability=0,
        cloud_cover_pct=None,
        dew_risk="unknown",
        wind_kph=None,
        transparency=None,
        seeing=None,
        moon_illum_frac=0.0,
        dark_window_utc=window_utc,
        source="unknown",
        reasons=["weather unavailable — assess the sky manually"],
    )


class OpenMeteoSource:
    """Default weather backend: one key-less HTTPS GET to Open-Meteo."""

    def __init__(
        self, *, client: httpx.AsyncClient | None = None, timeout_s: float = 30.0
    ) -> None:
        self._client = client
        self._timeout_s = timeout_s

    async def assess(
        self,
        site: SiteProfile,
        window_utc: tuple[str, str],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> ConditionsAssessment:
        """Fetch the forecast and derive a :class:`ConditionsAssessment`.

        Any network error (:class:`httpx.RequestError`) or malformed-response
        error (missing keys / bad shapes) is caught and turned into the
        non-fatal ``source="unknown"`` fallback — this call never raises.
        """
        params = {
            "latitude": site.lat_deg,
            "longitude": site.lon_deg,
            "hourly": ",".join(_HOURLY_VARS),
            "timezone": "UTC",
            "forecast_days": 2,
        }
        active = client or self._client
        try:
            if active is not None:
                response = await active.get(
                    _FORECAST_URL, params=params, timeout=self._timeout_s
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as owned:
                    response = await owned.get(_FORECAST_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.RequestError:
            # Network-level failure (DNS, connect, timeout, read) — non-fatal.
            return _unknown(window_utc)

        try:
            return self._interpret(payload, window_utc)
        except (KeyError, ValueError, TypeError, IndexError):
            # Unexpected JSON shape (missing "hourly", ragged arrays, etc.).
            return _unknown(window_utc)
        except Exception:  # noqa: BLE001 - any other shape surprise is non-fatal
            return _unknown(window_utc)

    @staticmethod
    def _interpret(
        payload: dict, window_utc: tuple[str, str]
    ) -> ConditionsAssessment:
        """Turn a parsed Open-Meteo payload into a scored assessment."""
        hourly = payload["hourly"]
        rows = _window_rows(hourly, window_utc)

        def col(name: str) -> list[float]:
            values = hourly[name]
            return [float(values[i]) for i in rows]

        low, mid, high = (
            col("cloudcover_low"),
            col("cloudcover_mid"),
            col("cloudcover_high"),
        )
        # Worst-case cloud: max over the window of the max of the three layers.
        cloud_cover_pct = max(
            max(a, b, c) for a, b, c in zip(low, mid, high, strict=True)
        )

        temps, dewpoints = col("temperature_2m"), col("dewpoint_2m")
        min_spread = min(
            t - d for t, d in zip(temps, dewpoints, strict=True)
        )
        dew_risk = _dew_risk(min_spread)

        wind_kph = max(col("windspeed_10m"))
        humidity = max(col("relativehumidity_2m"))
        max_precip = max(col("precipitation_probability"))

        suitability = _score(cloud_cover_pct, wind_kph, max_precip)
        go = suitability >= _GO_SUITABILITY and max_precip < _GO_PRECIP_PCT

        reasons = [
            f"cloud cover {cloud_cover_pct:.0f}% (worst over window)",
            f"dew risk {dew_risk} (min temp-dewpoint spread {min_spread:.1f} deg C)",
            f"wind up to {wind_kph:.0f} kph",
        ]
        if max_precip > 0:
            reasons.append(f"precipitation probability up to {max_precip:.0f}%")

        return ConditionsAssessment(
            go=go,
            suitability=suitability,
            cloud_cover_pct=cloud_cover_pct,
            dew_risk=dew_risk,
            wind_kph=wind_kph,
            transparency=_transparency(cloud_cover_pct, humidity),
            seeing=_seeing(wind_kph),
            moon_illum_frac=0.0,  # filled in by assess_conditions
            dark_window_utc=window_utc,
            source="open-meteo",
            reasons=reasons,
        )


# meteoblue's 1-hourly clouds package (clouds-1h) is PAID; on the free tier we
# request clouds at 3-hourly resolution (clouds-3h) alongside 1-hourly basics.
# basic-1h stays 1-hourly for precip/wind/temp; clouds land in a data_3h block.
_METEOBLUE_URL = "https://my.meteoblue.com/packages/basic-1h_clouds-3h"


def _dewpoint(temp_c: float, rh_pct: float) -> float:
    """Dewpoint (deg C) from temperature and relative humidity (Magnus formula).

    meteoblue does not return dewpoint directly (Open-Meteo does), so derive it
    to keep the shared dew-risk logic identical across both sources.
    """
    rh = max(1.0, min(100.0, rh_pct))
    a, b = 17.27, 237.7
    gamma = (a * temp_c) / (b + temp_c) + math.log(rh / 100.0)
    return (b * gamma) / (a - gamma)


class MeteoblueSource:
    """Keyed weather backend: meteoblue's multi-model forecast (one HTTPS GET).

    Same contract as :class:`OpenMeteoSource` — any network/shape error degrades
    to the non-fatal ``source="unknown"`` fallback, so it never raises. meteoblue
    returns hourly rows in the site's LOCAL time (with ``utc_timeoffset`` in the
    metadata), so times are shifted to UTC before matching the dark window; wind
    is m/s (converted to kph) and dewpoint is derived via :func:`_dewpoint`.
    """

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout_s = timeout_s

    async def assess(
        self,
        site: SiteProfile,
        window_utc: tuple[str, str],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> ConditionsAssessment:
        params = {
            "lat": site.lat_deg,
            "lon": site.lon_deg,
            "asl": site.elevation_m,
            "format": "json",
            "apikey": self._api_key,
        }
        active = client or self._client
        try:
            if active is not None:
                response = await active.get(
                    _METEOBLUE_URL, params=params, timeout=self._timeout_s
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as owned:
                    response = await owned.get(_METEOBLUE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.RequestError:
            return _unknown(window_utc)

        try:
            return self._interpret(payload, window_utc)
        except Exception:  # noqa: BLE001 - any shape surprise is non-fatal
            return _unknown(window_utc)

    @staticmethod
    def _interpret(
        payload: dict, window_utc: tuple[str, str]
    ) -> ConditionsAssessment:
        """Turn a parsed meteoblue payload into a scored assessment."""
        offset_h = float(payload.get("metadata", {}).get("utc_timeoffset", 0.0))

        def to_utc(times: list[str]) -> list[str]:
            # meteoblue times are LOCAL ("YYYY-MM-DD HH:MM"); shift to UTC ISO so
            # the shared, UTC-assuming window matcher selects the right rows.
            return [
                (
                    datetime.strptime(t, "%Y-%m-%d %H:%M")
                    - timedelta(hours=offset_h)
                ).strftime("%Y-%m-%dT%H:%M")
                for t in times
            ]

        data = payload["data_1h"]
        rows = _window_rows({"time": to_utc(data["time"])}, window_utc)

        def col(name: str) -> list[float]:
            values = data[name]
            return [float(values[i]) for i in rows]

        # Cloud layers come from the 3-hourly package: on meteoblue's free tier
        # the 1-hourly clouds package is paid, so clouds arrive in data_3h at a
        # coarser cadence and must be matched to the window on their own rows.
        clouds = payload.get("data_3h", data)
        crows = _window_rows({"time": to_utc(clouds["time"])}, window_utc)

        def ccol(name: str) -> list[float]:
            values = clouds[name]
            return [float(values[i]) for i in crows]

        low, mid, high = ccol("lowclouds"), ccol("midclouds"), ccol("highclouds")
        cloud_cover_pct = max(
            max(a, b, c) for a, b, c in zip(low, mid, high, strict=True)
        )

        temps, humid = col("temperature"), col("relativehumidity")
        min_spread = min(
            t - _dewpoint(t, h) for t, h in zip(temps, humid, strict=True)
        )
        dew_risk = _dew_risk(min_spread)

        wind_kph = max(col("windspeed")) * 3.6  # meteoblue m/s -> kph
        humidity = max(humid)
        max_precip = max(col("precipitation_probability"))

        suitability = _score(cloud_cover_pct, wind_kph, max_precip)
        go = suitability >= _GO_SUITABILITY and max_precip < _GO_PRECIP_PCT

        reasons = [
            f"cloud cover {cloud_cover_pct:.0f}% (worst over window)",
            f"dew risk {dew_risk} (min temp-dewpoint spread {min_spread:.1f} deg C)",
            f"wind up to {wind_kph:.0f} kph",
        ]
        if max_precip > 0:
            reasons.append(f"precipitation probability up to {max_precip:.0f}%")

        return ConditionsAssessment(
            go=go,
            suitability=suitability,
            cloud_cover_pct=cloud_cover_pct,
            dew_risk=dew_risk,
            wind_kph=wind_kph,
            transparency=_transparency(cloud_cover_pct, humidity),
            seeing=_seeing(wind_kph),
            moon_illum_frac=0.0,  # filled in by assess_conditions
            dark_window_utc=window_utc,
            source="meteoblue",
            reasons=reasons,
        )


def resolve_source(api_key: str | None) -> WeatherSource:
    """Pick the weather backend: meteoblue when an API key is present, else
    Open-Meteo. One place for source selection so the planner stays agnostic."""
    key = (api_key or "").strip()
    return MeteoblueSource(key) if key else OpenMeteoSource()


async def assess_conditions(
    site: SiteProfile,
    window_utc: tuple[str, str],
    moon_illum_frac: float,
    *,
    source: WeatherSource | None = None,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> ConditionsAssessment:
    """Assess conditions and fold in the caller-supplied moon illumination.

    The weather backend (default :class:`OpenMeteoSource`) produces the sky
    verdict; this function then records ``moon_illum_frac`` on the result and
    subtracts a moon penalty from ``suitability`` scaled by illumination. A
    bright moon therefore lowers suitability but does NOT change ``go`` — the
    weather (or its absence) alone drives the go/no-go decision.
    """
    backend = source or resolve_source(api_key)
    assessment = await backend.assess(site, window_utc, client=client)

    assessment.moon_illum_frac = moon_illum_frac
    assessment.dark_window_utc = window_utc

    penalty = round(_MOON_MAX_PENALTY * max(0.0, min(1.0, moon_illum_frac)))
    if penalty > 0:
        assessment.suitability = int(max(0, assessment.suitability - penalty))
    assessment.reasons.append(
        f"moon {moon_illum_frac * 100:.0f}% illuminated"
        + (f" (-{penalty} suitability)" if penalty else "")
    )
    return assessment

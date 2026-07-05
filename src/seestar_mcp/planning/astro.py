"""Deterministic observability engine (astropy) for the observing planner.

Everything here is a pure function of an explicitly injected timestamp
(``when_utc`` as an ISO-UTC string or :class:`astropy.time.Time`) — nothing
reads the wall clock, so results are reproducible and tests are stable.

Three public entry points:

* :func:`field_rotation_rate` — the alt-az field-rotation magnitude in deg/hr.
  This is the Seestar's key constraint: rotation is worst near the zenith
  (``cos(alt) -> 0``), so a target that transits high rotates fastest exactly
  when it is best placed.
* :func:`dark_window` — astronomical dusk/dawn (Sun altitude < -18 deg) for the
  night containing ``when_utc``.
* :func:`observability` — the full :class:`Observability` record for one target,
  sampled across the dark window: the bankable clean time in the sweet band
  ``[min_altitude_deg, field_rotation_ceiling_deg]``, moon geometry, and the
  field-rotation-limited usable sub length. Never raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians

import astropy.units as u
import numpy as np
from astropy.coordinates import (
    AltAz,
    EarthLocation,
    SkyCoord,
    get_body,
    get_sun,
)
from astropy.time import Time

from .catalog import DsoTarget
from .site import SiteProfile, is_blocked

# Angular field rotation (deg) accumulated at the frame edge that we treat as the
# ceiling for a "clean" sub before star trailing becomes visible. This is the
# tunable rotation-at-frame-edge smear budget: at ~0.03 deg of rotation a star
# near the ~0.65 deg edge of the Seestar FOV walks roughly a pixel, so this is a
# conservative single-sub trailing limit. Raise it to accept looser subs.
SMEAR_DEG = 0.03

# Sampling grids (minutes). The dark window is found on a coarse Sun grid; the
# target track is sampled on a finer grid so summed "minutes" are accurate.
_SUN_STEP_MIN = 5.0
_TARGET_STEP_MIN = 2.0

# Astronomical twilight: Sun below this altitude is "astro dark".
_ASTRO_DARK_ALT_DEG = -18.0

_ISO_FMT = "isot"  # astropy Time format for ISO strings, e.g. 2026-07-05T04:00:00


@dataclass
class Observability:
    """Everything the ranker needs to judge one target on one night.

    All times are ISO-UTC strings. ``dark_minutes_*`` are integrated over the
    astronomical-dark window on a fixed grid; the *sweet band* is the usable
    ``[min_altitude_deg, field_rotation_ceiling_deg]`` altitude range.
    """

    target_id: str
    max_alt_deg: float
    transit_utc: str | None  # ISO, or None if never above the floor
    rise_utc: str | None
    set_utc: str | None
    dark_minutes_above_floor: float  # above min_alt AND mask AND dark
    dark_minutes_in_sweet_band: float  # in [min_alt, ceiling] AND mask AND dark
    field_rotation_deg_per_hr_at_transit: float
    usable_sub_minutes: float  # minutes before field rotation smears a sub past SMEAR_DEG
    transits_above_ceiling: bool  # best altitude exceeds the field-rotation ceiling
    moon_sep_deg: float
    moon_alt_deg: float
    moon_illum_frac: float
    best_window_utc: tuple[str, str] | None  # longest contiguous sweet-band+unblocked span


def _to_time(when_utc: str | Time) -> Time:
    """Coerce an ISO-UTC string or :class:`Time` to a scalar UTC :class:`Time`."""
    if isinstance(when_utc, Time):
        return when_utc
    return Time(when_utc, scale="utc")


def _location(site: SiteProfile) -> EarthLocation:
    return EarthLocation.from_geodetic(
        lon=site.lon_deg,
        lat=site.lat_deg,
        height=site.elevation_m,
    )


def _iso(t: Time) -> str:
    """Render a scalar :class:`Time` as a compact ISO-UTC string (no scale suffix)."""
    return t.utc.isot


def field_rotation_rate(lat_deg: float, az_deg: float, alt_deg: float) -> float:
    """Magnitude of the alt-az field-rotation rate in deg/hr.

    ``|15.041 * cos(lat) * cos(az) / cos(alt)|`` (15.041 deg/hr is the sidereal
    rate). ``cos(alt)`` is clamped to >= 1e-6 to avoid a divide-by-zero at the
    zenith. The rate is zero due east/west (``cos(az) == 0``) and grows without
    bound toward the zenith.
    """
    cos_alt = cos(radians(alt_deg))
    if abs(cos_alt) < 1e-6:
        cos_alt = 1e-6 if cos_alt >= 0 else -1e-6
    return abs(15.041 * cos(radians(lat_deg)) * cos(radians(az_deg)) / cos_alt)


def moon_illumination(when_utc: str | Time) -> float:
    """Illuminated fraction of the Moon (0..1) at a single UTC instant.

    Standalone counterpart to the per-target moon geometry in
    :func:`observability`, so conditions can report the night's moon phase
    without resolving any target. Derived from the Sun-Moon elongation:
    ``0.5 * (1 - cos(elong))``.
    """
    t = _to_time(when_utc)
    moon = get_body("moon", t)
    sun = get_sun(t)
    elong = moon.separation(sun).radian
    return float(0.5 * (1.0 - np.cos(elong)))


def _sun_alt_grid(site: SiteProfile, center: Time) -> tuple[Time, np.ndarray]:
    """Sun altitude (deg) on a +/-12h grid centred on ``center``."""
    loc = _location(site)
    n = int((24 * 60) / _SUN_STEP_MIN) + 1
    offsets = np.linspace(-12.0, 12.0, n)  # hours
    times = center + offsets * u.hour
    altaz = AltAz(obstime=times, location=loc)
    alt = get_sun(times).transform_to(altaz).alt.deg
    return times, np.asarray(alt)


def dark_window(site: SiteProfile, when_utc: str | Time) -> tuple[str, str]:
    """Astronomical dusk/dawn (Sun < -18 deg) for the night containing ``when_utc``.

    Builds a +/-12h Sun-altitude grid at ~5-min steps, finds the darkest sample
    (Sun lowest), and returns the ISO-UTC start/end of the contiguous sub--18deg
    span that contains it. If the Sun never drops below -18deg (e.g. high summer
    latitudes), it falls back to the contiguous span around the darkest sample so
    it never raises.
    """
    center = _to_time(when_utc)
    times, alt = _sun_alt_grid(site, center)

    darkest = int(np.argmin(alt))
    below = alt < _ASTRO_DARK_ALT_DEG
    if not below[darkest]:
        # Sun never reaches astro dark; approximate with the span whose Sun
        # altitude is within 1 deg of the darkest sample so we still return a
        # sensible pair rather than raising.
        threshold = alt[darkest] + 1.0
        below = alt <= threshold

    lo = darkest
    while lo - 1 >= 0 and below[lo - 1]:
        lo -= 1
    hi = darkest
    while hi + 1 < len(below) and below[hi + 1]:
        hi += 1
    return _iso(times[lo]), _iso(times[hi])


def _empty_observability(target_id: str) -> Observability:
    """A zeroed record for the never-raise error path."""
    return Observability(
        target_id=target_id,
        max_alt_deg=0.0,
        transit_utc=None,
        rise_utc=None,
        set_utc=None,
        dark_minutes_above_floor=0.0,
        dark_minutes_in_sweet_band=0.0,
        field_rotation_deg_per_hr_at_transit=0.0,
        usable_sub_minutes=0.0,
        transits_above_ceiling=False,
        moon_sep_deg=0.0,
        moon_alt_deg=0.0,
        moon_illum_frac=0.0,
        best_window_utc=None,
    )


def observability(
    site: SiteProfile,
    target: DsoTarget,
    when_utc: str | Time,
) -> Observability:
    """Full :class:`Observability` for ``target`` over the night's dark window.

    Samples the target's alt-az track on a 2-min grid across the astronomical-
    dark window, integrating minutes above the floor and in the sweet band
    ``[min_altitude_deg, field_rotation_ceiling_deg]`` (both gated by the horizon
    mask via :func:`is_blocked`). Adds transit geometry, moon separation /
    altitude / illumination at transit, and the field-rotation-limited usable sub
    length. Never raises: on any error it returns a zeroed record.
    """
    try:
        return _observability(site, target, when_utc)
    except Exception:  # noqa: BLE001 - never-raise contract; degrade to a zeroed record
        return _empty_observability(target.id if target is not None else "unknown")


def _observability(
    site: SiteProfile,
    target: DsoTarget,
    when_utc: str | Time,
) -> Observability:
    loc = _location(site)
    dusk_iso, dawn_iso = dark_window(site, when_utc)
    dusk = _to_time(dusk_iso)
    dawn = _to_time(dawn_iso)

    span_min = float((dawn - dusk).to_value("min"))
    if span_min <= 0:
        return _empty_observability(target.id)
    n = max(2, int(span_min / _TARGET_STEP_MIN) + 1)
    offsets = np.linspace(0.0, span_min, n)  # minutes from dusk
    step_min = span_min / (n - 1)
    times = dusk + offsets * u.min

    altaz = AltAz(obstime=times, location=loc)
    coord = SkyCoord(ra=target.ra_deg, dec=target.dec_deg, unit="deg")
    track = coord.transform_to(altaz)
    alt = np.asarray(track.alt.deg)
    az = np.asarray(track.az.deg)

    floor = site.min_altitude_deg
    ceiling = site.field_rotation_ceiling_deg

    # Per-sample unblocked mask (horizon mask + floor).
    unblocked = np.array(
        [not is_blocked(site, float(az[i]), float(alt[i])) for i in range(n)]
    )
    above_floor = (alt >= floor) & unblocked
    sweet = (alt >= floor) & (alt <= ceiling) & unblocked

    dark_minutes_above_floor = float(np.count_nonzero(above_floor) * step_min)
    dark_minutes_in_sweet_band = float(np.count_nonzero(sweet) * step_min)

    # Transit = max-altitude sample.
    i_transit = int(np.argmax(alt))
    max_alt = float(alt[i_transit])
    az_transit = float(az[i_transit])
    transit_utc = _iso(times[i_transit])
    transits_above_ceiling = max_alt > ceiling

    # Rise/set = first/last sample above the floor (mask-independent altitude gate).
    above = alt >= floor
    if np.any(above):
        idx = np.nonzero(above)[0]
        rise_utc: str | None = _iso(times[int(idx[0])])
        set_utc: str | None = _iso(times[int(idx[-1])])
    else:
        rise_utc = None
        set_utc = None
        transit_utc = None

    # Field rotation at transit and the resulting usable sub length.
    rate = field_rotation_rate(site.lat_deg, az_transit, max_alt)
    if rate < 1e-6:
        usable_sub_minutes = span_min  # effectively unlimited; cap at the dark window
    else:
        usable_sub_minutes = min((SMEAR_DEG / rate) * 60.0, span_min)

    # Moon geometry at transit.
    t_transit = times[i_transit]
    moon = get_body("moon", t_transit, loc)
    sun = get_sun(t_transit)
    elongation = moon.separation(sun).radian
    moon_illum_frac = float(0.5 * (1.0 - np.cos(elongation)))
    moon_altaz = AltAz(obstime=t_transit, location=loc)
    moon_alt_deg = float(moon.transform_to(moon_altaz).alt.deg)
    moon_sep_deg = float(coord.separation(moon).deg)

    best_window_utc = _longest_run(sweet, times)

    return Observability(
        target_id=target.id,
        max_alt_deg=max_alt,
        transit_utc=transit_utc,
        rise_utc=rise_utc,
        set_utc=set_utc,
        dark_minutes_above_floor=dark_minutes_above_floor,
        dark_minutes_in_sweet_band=dark_minutes_in_sweet_band,
        field_rotation_deg_per_hr_at_transit=rate,
        usable_sub_minutes=usable_sub_minutes,
        transits_above_ceiling=transits_above_ceiling,
        moon_sep_deg=moon_sep_deg,
        moon_alt_deg=moon_alt_deg,
        moon_illum_frac=moon_illum_frac,
        best_window_utc=best_window_utc,
    )


def _longest_run(mask: np.ndarray, times: Time) -> tuple[str, str] | None:
    """Return (start_iso, end_iso) of the longest contiguous ``True`` run, or None."""
    best_len = 0
    best = None
    i = 0
    n = len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            if (j - i + 1) > best_len:
                best_len = j - i + 1
                best = (_iso(times[i]), _iso(times[j]))
            i = j + 1
        else:
            i += 1
    return best

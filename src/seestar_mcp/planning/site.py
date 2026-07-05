"""Observing-site profile: persistence, GPS fallback, and horizon-mask blocking.

The :class:`SiteProfile` captures everything the planner needs to know about
*where* the scope is: geographic position, sky darkness (Bortle / SQM), a
per-azimuth horizon mask (trees/buildings), and the usable-altitude band
``[min_altitude_deg, field_rotation_ceiling_deg]`` that bounds clean alt-az
imaging.

This module is pure/local: no clock, no network. ``save_site``/``load_site``
persist the profile as JSON (tuples are stored as lists and restored to tuples
on load). ``data/site_profile.json`` is local, gitignored state.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

_DEFAULT_PATH = Path("data") / "site_profile.json"


@dataclass
class SiteProfile:
    """An observing site. Angles in degrees, elevation in metres."""

    name: str
    lat_deg: float
    lon_deg: float
    elevation_m: float = 0.0
    bortle: int | None = None  # 1 (pristine) .. 9 (inner city)
    sqm: float | None = None  # optional sky-quality mag/arcsec^2
    # Each entry: (az_min_deg, az_max_deg, alt_min_deg) — below alt_min in that
    # azimuth arc is blocked (trees/buildings). Empty = flat open horizon.
    horizon_mask: list[tuple[float, float, float]] = field(default_factory=list)
    min_altitude_deg: float = 20.0  # lower usable-altitude floor (airmass/murk)
    # Upper field-rotation ceiling: above this, alt-az rotation degrades subs
    # (worst near the zenith). Sweet spot = [min_altitude_deg, this].
    field_rotation_ceiling_deg: float = 60.0
    # How far the scope may sit from this site (haversine km) before the saved
    # horizon mask is treated as stale and no longer applied (see location_status).
    location_tolerance_km: float = 1.0


def save_site(profile: SiteProfile, path: Path | None = None) -> Path:
    """Persist ``profile`` as JSON to ``path`` (default ``data/site_profile.json``).

    Parent directories are created as needed. Tuples in ``horizon_mask`` are
    stored as JSON lists. Returns the path written.
    """
    path = Path(path) if path is not None else _DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(profile)
    # Normalise horizon-mask tuples to plain lists for JSON.
    data["horizon_mask"] = [list(arc) for arc in profile.horizon_mask]
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def load_site(path: Path | None = None) -> SiteProfile | None:
    """Load a :class:`SiteProfile` from ``path``; ``None`` if the file is absent.

    ``horizon_mask`` is restored as a list of 3-tuples.
    """
    path = Path(path) if path is not None else _DEFAULT_PATH
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    data["horizon_mask"] = [tuple(arc) for arc in data.get("horizon_mask", [])]
    return SiteProfile(**data)


def site_from_gps(lat: float, lon: float, elevation_m: float = 0.0) -> SiteProfile:
    """Build a bare profile from scope GPS coordinates (the no-profile fallback)."""
    return SiteProfile(name="GPS", lat_deg=lat, lon_deg=lon, elevation_m=elevation_m)


def is_blocked(profile: SiteProfile, az_deg: float, alt_deg: float) -> bool:
    """Return ``True`` if ``(az_deg, alt_deg)`` is unusable for this site.

    Blocked when the altitude is below the global ``min_altitude_deg`` floor, or
    when the azimuth falls inside a horizon-mask arc ``(a0, a1, amin)`` and the
    altitude is below that arc's ``amin``.
    """
    if alt_deg < profile.min_altitude_deg:
        return True
    for a0, a1, amin in profile.horizon_mask:
        if a0 <= az_deg <= a1 and alt_deg < amin:
            return True
    return False

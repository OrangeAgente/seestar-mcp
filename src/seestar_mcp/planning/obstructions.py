"""Learned horizon mask: infer fixed obstructions from weather-gated, cross-night
plate-solve failures binned by ``(azimuth, altitude)`` — and never auto-apply.

A single cloud can blank one target then another; naive "blank frame -> blocked"
would wall off good sky. Obstruction inference is therefore robust via **four
discriminators** a cloud cannot fake all of:

1. **Cross-night persistence** — a bin must fail on >= ``min_nights`` *distinct*
   nights before it is even a candidate.
2. **Weather-gating** — a failure recorded while the weather was bad
   (``weather_ok is False``) is tagged ``weather_excluded`` and never counts
   toward obstruction evidence. Only clear-sky failures accumulate.
3. **Spatial isolation** — a candidate bearing must be bad while it is *not* the
   case that both same-altitude neighbour azimuth bins are equally bad (a whole
   bad altitude ring is weather/twilight, not a tree).
4. **Altitude prior** — obstructions are low: only bins whose altitude *ceiling*
   is <= ``max_obstruction_alt`` (default 40 deg) are eligible.

A mask is location-bound: each observation stamps its ``lat``/``lon`` and
``suggest_obstructions`` only aggregates records within ``location_tolerance_km``
of the queried location, so obstructions learned at home never surface at a
different site.

This module is pure/local and **deterministic**: every function that records a
timestamp takes an explicit ``now_utc`` (an ISO string); the tool layer supplies
``datetime.now(timezone.utc).isoformat()``. A missing or corrupt log is treated
as empty and never raises. Suggestions are advisory — nothing edits the mask.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

_DEFAULT_PATH = Path("data") / "sky_failures.json"

# Bin geometry: azimuth buckets AZ_BIN_DEG wide, altitude buckets ALT_BIN_DEG wide.
AZ_BIN_DEG = 10.0
ALT_BIN_DEG = 5.0

_EARTH_RADIUS_KM = 6371.0


@dataclass
class SkyBin:
    """Accumulated pointing outcomes for one ``(az_bin, alt_bin)`` bucket."""

    az_bin: int  # azimuth bucket index (AZ_BIN_DEG-wide)
    alt_bin: int  # altitude bucket index (ALT_BIN_DEG-wide)
    attempts: int = 0  # clear-sky attempts (weather-excluded ones not counted here)
    failures: int = 0  # clear-sky failures (solve/no-stars) in this bin
    fail_nights: list[str] = field(default_factory=list)  # distinct clear-sky fail nights
    weather_excluded: int = 0  # failures dropped because weather was bad (audit only)
    last_utc: str = ""
    # Representative coordinates of the contributing observations (location scope).
    lat: float | None = None
    lon: float | None = None


@dataclass
class ObstructionCandidate:
    """A suggested horizon-mask arc, with the evidence that produced it."""

    az_min_deg: float
    az_max_deg: float
    alt_min_deg: float  # suggested horizon-mask ceiling for the arc
    confidence: float  # 0..1
    reasons: list[str]  # evidence: nights, attempts, failure rate, isolation


def _bin_key(az_bin: int, alt_bin: int) -> str:
    """String key for the JSON store (JSON object keys must be strings)."""
    return f"{az_bin},{alt_bin}"


def load_sky_log(path: Path | None = None) -> dict[str, SkyBin]:
    """Load the store as ``{"az,alt": SkyBin}``; ``{}`` if missing or corrupt.

    Never raises: a missing file or unparseable/invalid JSON yields ``{}`` so the
    learner degrades gracefully to "no evidence yet".
    """
    path = Path(path) if path is not None else _DEFAULT_PATH
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return {key: SkyBin(**data) for key, data in raw.items()}
    except (json.JSONDecodeError, TypeError, ValueError, OSError):
        return {}


def save_sky_log(log: dict[str, SkyBin], path: Path | None = None) -> Path:
    """Persist ``log`` as JSON to ``path`` (default ``data/sky_failures.json``).

    Parent directories are created as needed. Returns the path written.
    """
    path = Path(path) if path is not None else _DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {key: asdict(skybin) for key, skybin in log.items()}
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def record_sky_result(
    az_deg: float,
    alt_deg: float,
    ok: bool,
    *,
    weather_ok: bool,
    now_utc: str,
    lat: float | None = None,
    lon: float | None = None,
    path: Path | None = None,
) -> None:
    """Record one pointing outcome into the weather-gated ``(az, alt)`` histogram.

    Bucketing: ``az_bin = int(az_deg // AZ_BIN_DEG)``,
    ``alt_bin = int(alt_deg // ALT_BIN_DEG)``. The "night id" is the UTC date of
    ``now_utc`` (its first 10 chars, ``YYYY-MM-DD``; a trailing ``Z`` is fine).

    Weather-gating: if ``weather_ok is False`` and the pointing failed, only the
    audit-only ``weather_excluded`` counter is bumped — the failure never counts
    toward ``attempts``/``failures``. Otherwise ``attempts`` is incremented and,
    on failure, ``failures`` is incremented and the distinct night id is added to
    ``fail_nights``. The contributing ``lat``/``lon`` are stamped on the bin (a
    representative coordinate) for later location scoping. ``last_utc`` always
    updates. Never raises.
    """
    az_bin = int(az_deg // AZ_BIN_DEG)
    alt_bin = int(alt_deg // ALT_BIN_DEG)
    key = _bin_key(az_bin, alt_bin)
    log = load_sky_log(path)
    skybin = log.get(key) or SkyBin(az_bin=az_bin, alt_bin=alt_bin)

    if weather_ok is False and not ok:
        skybin.weather_excluded += 1
    else:
        skybin.attempts += 1
        if not ok:
            skybin.failures += 1
            night = now_utc[:10]
            if night not in skybin.fail_nights:
                skybin.fail_nights.append(night)

    # Stamp a representative coordinate whenever one is supplied.
    if lat is not None:
        skybin.lat = lat
    if lon is not None:
        skybin.lon = lon
    skybin.last_utc = now_utc

    log[key] = skybin
    save_sky_log(log, path)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points (Earth R=6371 km)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def location_status(profile, cur_lat: float, cur_lon: float) -> tuple[bool, float]:
    """``(within_tolerance, distance_km)`` between ``profile`` and current GPS.

    Uses ``getattr(profile, "location_tolerance_km", 1.0)`` so it works before the
    ``SiteProfile.location_tolerance_km`` field is added.
    """
    distance = haversine_km(profile.lat_deg, profile.lon_deg, cur_lat, cur_lon)
    tolerance = getattr(profile, "location_tolerance_km", 1.0)
    return distance <= tolerance, distance


def _in_scope(
    skybin: SkyBin,
    cur_lat: float | None,
    cur_lon: float | None,
    location_tolerance_km: float,
) -> bool:
    """True if ``skybin`` is within scope of the queried location.

    No query location -> always in scope. A bin with no recorded coordinates is
    kept (best-effort). A bin with coordinates beyond ``location_tolerance_km`` is
    excluded (it belongs to another site).
    """
    if cur_lat is None or cur_lon is None:
        return True
    if skybin.lat is None or skybin.lon is None:
        return True
    return haversine_km(skybin.lat, skybin.lon, cur_lat, cur_lon) <= location_tolerance_km


def _meets_failure_thresholds(
    skybin: SkyBin, min_nights: int, min_attempts: int, min_failure_rate: float
) -> bool:
    """Persistence + attempts + failure-rate gate (no altitude/isolation/location)."""
    if len(set(skybin.fail_nights)) < min_nights:
        return False
    if skybin.attempts < min_attempts:
        return False
    if skybin.attempts <= 0:
        return False
    return skybin.failures / skybin.attempts >= min_failure_rate


def suggest_obstructions(
    log_or_path=None,
    *,
    cur_lat: float | None = None,
    cur_lon: float | None = None,
    min_nights: int = 3,
    min_attempts: int = 4,
    min_failure_rate: float = 0.7,
    max_obstruction_alt: float = 40.0,
    location_tolerance_km: float = 1.0,
) -> list[ObstructionCandidate]:
    """Pure inference of obstruction arcs from the sky-failure log.

    ``log_or_path`` may be an already-loaded ``{"az,alt": SkyBin}`` dict, a path,
    or ``None`` (default store). A bin becomes a candidate iff, within the queried
    location scope, ALL hold:

    * **Altitude prior** — the bin's altitude ceiling
      ``(alt_bin + 1) * ALT_BIN_DEG`` is <= ``max_obstruction_alt``.
    * **Persistence** — ``len(distinct fail_nights) >= min_nights``.
    * **Sampling** — ``attempts >= min_attempts``.
    * **Failure rate** — ``failures / attempts >= min_failure_rate``.
    * **Spatial isolation** — it is NOT the case that both same-altitude neighbour
      azimuth bins (``az_bin ± 1``) also meet the failure thresholds. A uniformly
      bad altitude ring is weather/twilight, so such bins are dropped.

    Adjacent qualifying azimuth bins at the same altitude bin are merged into one
    arc ``(az_min = az_bin*AZ_BIN_DEG, az_max = (max_az_bin+1)*AZ_BIN_DEG,
    alt_min = (alt_bin+1)*ALT_BIN_DEG)``. ``confidence`` is
    ``min(1.0, (nights / (min_nights * 2)) * rate)`` over the merged evidence.
    Never raises; a thin log yields ``[]``.
    """
    try:
        if isinstance(log_or_path, dict):
            log = log_or_path
        else:
            log = load_sky_log(log_or_path)

        # Bins that pass the raw failure thresholds (used for the isolation test).
        bad: dict[tuple[int, int], SkyBin] = {
            (b.az_bin, b.alt_bin): b
            for b in log.values()
            if _meets_failure_thresholds(b, min_nights, min_attempts, min_failure_rate)
        }

        # Per-bin qualification: in scope, low enough, and spatially isolated.
        qualifying: dict[int, list[SkyBin]] = {}  # alt_bin -> list of qualifying bins
        for (az_bin, alt_bin), skybin in bad.items():
            if (alt_bin + 1) * ALT_BIN_DEG > max_obstruction_alt:
                continue  # altitude prior
            if not _in_scope(skybin, cur_lat, cur_lon, location_tolerance_km):
                continue  # different site
            left_bad = (az_bin - 1, alt_bin) in bad
            right_bad = (az_bin + 1, alt_bin) in bad
            if left_bad and right_bad:
                continue  # whole-ring / not isolated -> weather/twilight
            qualifying.setdefault(alt_bin, []).append(skybin)

        candidates: list[ObstructionCandidate] = []
        for alt_bin, bins in qualifying.items():
            bins.sort(key=lambda b: b.az_bin)
            # Merge runs of adjacent azimuth bins into single arcs.
            run: list[SkyBin] = []
            for skybin in bins:
                if run and skybin.az_bin != run[-1].az_bin + 1:
                    candidates.append(_build_candidate(run, alt_bin, min_nights))
                    run = []
                run.append(skybin)
            if run:
                candidates.append(_build_candidate(run, alt_bin, min_nights))

        candidates.sort(key=lambda c: (c.alt_min_deg, c.az_min_deg))
        return candidates
    except Exception:  # noqa: BLE001 — inference must never raise on the tool path
        return []


def _build_candidate(
    run: list[SkyBin], alt_bin: int, min_nights: int
) -> ObstructionCandidate:
    """Merge a run of adjacent azimuth bins at ``alt_bin`` into one candidate arc."""
    az_min = min(b.az_bin for b in run) * AZ_BIN_DEG
    az_max = (max(b.az_bin for b in run) + 1) * AZ_BIN_DEG
    alt_min = (alt_bin + 1) * ALT_BIN_DEG

    nights = set()
    for b in run:
        nights.update(b.fail_nights)
    n_nights = len(nights)
    attempts = sum(b.attempts for b in run)
    failures = sum(b.failures for b in run)
    rate = failures / attempts if attempts else 0.0

    confidence = min(1.0, (n_nights / (min_nights * 2)) * rate)
    reasons = [
        f"failed on {n_nights} distinct clear night(s)",
        f"{failures}/{attempts} clear-sky attempts failed ({rate * 100:.0f}%)",
        f"isolated bearing {az_min:.0f}-{az_max:.0f}deg below {alt_min:.0f}deg "
        "(neighbouring azimuths not equally bad)",
    ]
    return ObstructionCandidate(
        az_min_deg=az_min,
        az_max_deg=az_max,
        alt_min_deg=alt_min,
        confidence=confidence,
        reasons=reasons,
    )

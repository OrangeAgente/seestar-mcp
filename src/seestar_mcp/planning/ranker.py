"""Reasoned target ranker for the observing planner.

Fuses the deterministic :class:`~seestar_mcp.planning.astro.Observability`
record with light-pollution suitability, moon geometry and framing into a single
0..100 score per target, and explains every score in ``TargetPlan.reasons``.

Design notes (all weights are documented module constants so the blend is tunable
without touching the math):

* The **primary** driver is bankable clean time in the field-rotation sweet band
  (``dark_minutes_in_sweet_band``). Raw high altitude is *not* rewarded — a
  near-zenith transit rotates fastest exactly when it is best placed, so we lean
  on the sweet-band minutes and a field-rotation-health term instead.
* **Sub-count (corrected):** ``recommended_subs`` is the total clean sweet-band
  time divided by the exposure — ``int(dark_minutes_in_sweet_band * 60 /
  recommended_exposure_s)``. It is deliberately NOT scaled by
  ``usable_sub_minutes`` (which is the at-transit worst-case per-sub smear
  window, tiny for near-zenith targets, not a total-time budget).
* Targets with zero sweet-band time are dropped entirely.

This module is pure/local: no clock, no network. ``observability_fn`` is
injectable so tests stay deterministic without astropy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from .astro import Observability, observability
from .lightpollution import bortle_for, lp_suitability

if TYPE_CHECKING:
    from .catalog import DsoTarget
    from .site import SiteProfile
    from .weather import ConditionsAssessment

# --- Scoring weights (0..1 blend, sum to 1.0). Documented + tunable. ---------
#: Bankable clean time in the sweet band — the primary driver.
W_SWEET_BAND = 0.40
#: Light-pollution fit of the target type at the site's Bortle class.
W_LP_FIT = 0.20
#: Moon term: far from a dim moon is best; near a bright moon hurts.
W_MOON = 0.15
#: Framing fit: how well the target's angular size suits the Seestar FOV.
W_FRAMING = 0.10
#: Field-rotation health: fraction of usable time that is actually in the sweet
#: band (a target that only clears the horizon above the ceiling scores low).
W_FIELD_ROT = 0.15

#: Sweet-band minutes that normalize the time term to 1.0 (a full clean night).
SWEET_BAND_FULL_MINUTES = 240.0

#: Seestar S50 field of view long dimension (~1.3 deg x 0.7 deg ~= 78' x 42').
FOV_LONG_ARCMIN = 78.0
#: Below this angular size a target is small in the frame.
FOV_SMALL_ARCMIN = 5.0

#: Default Seestar sub exposure (seconds). Recorded on every plan.
RECOMMENDED_EXPOSURE_S = 10


@dataclass
class TargetPlan:
    """A scored, reasoned plan for one target on one night (JSON-serializable)."""

    target: DsoTarget
    score: int  # 0..100
    reasons: list[str]  # every score is explained
    best_window_utc: tuple[str, str] | None
    recommended_subs: int
    recommended_exposure_s: int
    framing_note: str
    observability: Observability


def _framing_note(size_arcmin: float) -> str:
    """Describe how ``size_arcmin`` sits in the Seestar FOV."""
    if size_arcmin > FOV_LONG_ARCMIN:
        return "larger than FOV — consider a mosaic"
    if size_arcmin < FOV_SMALL_ARCMIN:
        return "small in FOV"
    return f"fits FOV ({size_arcmin:g}')"


def _framing_fit(size_arcmin: float) -> float:
    """0..1 framing-fit term: best when the target comfortably fills the frame."""
    if size_arcmin > FOV_LONG_ARCMIN:
        # Too big for one frame; falls off as it grows past the FOV.
        return max(0.3, FOV_LONG_ARCMIN / size_arcmin)
    if size_arcmin < FOV_SMALL_ARCMIN:
        # Tiny in frame; scales up toward the small-target threshold.
        return max(0.3, size_arcmin / FOV_SMALL_ARCMIN)
    return 1.0


def _score_target(
    site: SiteProfile,
    target: DsoTarget,
    obs: Observability,
) -> tuple[int, float, float, float, float]:
    """Return ``(score, time_term, lp_fit, moon_term, field_rot_term)``."""
    time_term = min(1.0, obs.dark_minutes_in_sweet_band / SWEET_BAND_FULL_MINUTES)
    lp_fit = lp_suitability(target.type, bortle_for(site))
    moon_term = min(1.0, obs.moon_sep_deg / 90.0) * (1.0 - 0.5 * obs.moon_illum_frac)
    framing = _framing_fit(target.size_arcmin)
    field_rot = obs.dark_minutes_in_sweet_band / max(1.0, obs.dark_minutes_above_floor)

    blend = (
        W_SWEET_BAND * time_term
        + W_LP_FIT * lp_fit
        + W_MOON * moon_term
        + W_FRAMING * framing
        + W_FIELD_ROT * field_rot
    )
    score = int(max(0, min(100, round(blend * 100))))
    return score, time_term, lp_fit, moon_term, field_rot


def _reasons(
    site: SiteProfile,
    target: DsoTarget,
    obs: Observability,
    lp_fit: float,
    framing_note: str,
    recommended_subs: int,
) -> list[str]:
    """Name every contributing factor behind a target's score."""
    reasons: list[str] = []

    if obs.best_window_utc is not None:
        start, end = obs.best_window_utc
        reasons.append(f"best window {start}–{end} UTC")

    reasons.append(
        f"{obs.dark_minutes_in_sweet_band:.0f} min clean sweet-band time"
        f" ({recommended_subs} subs at {RECOMMENDED_EXPOSURE_S}s)"
    )

    if obs.transits_above_ceiling:
        reasons.append(
            f"transit {obs.max_alt_deg:.0f}° — field rotation above ceiling"
        )

    if obs.usable_sub_minutes * 60.0 < RECOMMENDED_EXPOSURE_S:
        reasons.append("subs trail near transit")

    reasons.append(
        f"{obs.moon_sep_deg:.0f}° from a {obs.moon_illum_frac * 100:.0f}%-lit moon"
    )

    reasons.append(
        f"{target.type.replace('_', ' ')} suits Bortle {bortle_for(site)}"
        f" (LP fit {lp_fit:.2f})"
    )

    reasons.append(framing_note)
    return reasons


def _plan_target(
    site: SiteProfile,
    target: DsoTarget,
    obs: Observability,
) -> TargetPlan:
    """Build a fully-reasoned :class:`TargetPlan` for one scored target."""
    score, _time, lp_fit, _moon, _frot = _score_target(site, target, obs)

    recommended_subs = int(
        obs.dark_minutes_in_sweet_band * 60.0 / RECOMMENDED_EXPOSURE_S
    )
    framing_note = _framing_note(target.size_arcmin)
    reasons = _reasons(site, target, obs, lp_fit, framing_note, recommended_subs)

    return TargetPlan(
        target=target,
        score=score,
        reasons=reasons,
        best_window_utc=obs.best_window_utc,
        recommended_subs=recommended_subs,
        recommended_exposure_s=RECOMMENDED_EXPOSURE_S,
        framing_note=framing_note,
        observability=obs,
    )


def rank_targets(
    site: SiteProfile,
    when_utc: str,
    catalog: list[DsoTarget],
    conditions: ConditionsAssessment,
    *,
    types: list[str] | None = None,
    min_alt: float | None = None,
    limit: int | None = None,
    observability_fn: Callable[..., Observability] = observability,
) -> list[TargetPlan]:
    """Rank ``catalog`` for ``site`` on ``when_utc``, best score first.

    Filters by ``types`` (if given), computes observability for each surviving
    target via ``observability_fn`` (injectable for deterministic tests), drops
    any target with no sweet-band time, scores the rest on a documented weighted
    blend and returns them sorted descending by score (top ``limit`` if given).

    ``conditions`` is accepted for the conditions caveat / API symmetry; the
    go/no-go verdict does not exclude targets (planning still runs on a no-go
    night — the caller annotates the caveat). Never raises.
    """
    try:
        selected = catalog
        if types:
            wanted = set(types)
            selected = [t for t in catalog if t.type in wanted]

        plans: list[TargetPlan] = []
        for target in selected:
            try:
                obs = observability_fn(site, target, when_utc)
            except Exception:  # noqa: BLE001 - a bad target must not sink the batch
                continue
            if obs.dark_minutes_in_sweet_band <= 0:
                continue  # never up / no clean sweet-band time — excluded
            plans.append(_plan_target(site, target, obs))

        plans.sort(key=lambda p: p.score, reverse=True)
        if limit is not None and limit >= 0:
            plans = plans[:limit]
        return plans
    except Exception:  # noqa: BLE001 - tool-facing never-raise contract
        return []

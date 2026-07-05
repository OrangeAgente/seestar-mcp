"""Light-pollution suitability: Bortle resolution + target-type weighting.

The Seestar S50 has a fixed aperture and no filter wheel (its light-pollution
mode is a soft software filter), so the *type* of a deep-sky object drives how
much a bright sky hurts it:

* Narrowband-friendly emission sources (H-alpha / OIII line emitters — emission
  nebulae, planetary nebulae, supernova remnants) punch through skyglow: their
  signal sits in narrow lines while light pollution is broadband, so they stay
  highly suitable even under an inner-city sky.
* Broadband, low-surface-brightness targets (galaxies, reflection nebulae, and
  faint open clusters) are washed out as the sky brightens: their contrast
  against the background collapses as Bortle rises.
* Globular clusters are compact and bright, so they are fairly robust to light
  pollution — they sit in the middle.

Bortle is an integer 1 (pristine dark) .. 9 (inner-city). ``lp_suitability``
returns a 0..1 multiplier the ranker folds into a target's score.
"""

from __future__ import annotations

from seestar_mcp.planning.site import SiteProfile

#: Default Bortle assumed when a site profile does not record sky darkness.
#: 5 is a typical suburban sky — a deliberately middle-of-the-road guess that
#: neither over-rewards broadband targets (as a dark default would) nor
#: over-penalises them (as a city default would).
DEFAULT_BORTLE = 5

#: Per-behaviour light-pollution model. Each target type maps to a ``(kind,
#: base)`` rule evaluated against Bortle:
#:
#: * ``"narrowband"`` — stays high: ``base`` at any Bortle (line emission beats
#:   broadband skyglow), with only a token high-Bortle nudge downward.
#: * ``"broadband"`` — falls with Bortle: ``max(0.2, base - 0.12*(bortle-3))``.
#:   At the reference dark-ish sky (Bortle 3) suitability is ``base``; each step
#:   brighter costs 0.12, with a 0.2 floor so a target is never fully excluded.
#: * ``"robust"`` — compact/bright targets (globulars): a gentle decline that
#:   keeps them mid-range even in the city.
#: * ``"neutral"`` — unknown/other: a flat middle value.
#:
#: The mapping is intentionally coarse and documented so it can be tuned without
#: touching the scoring math.
LP_MODEL: dict[str, tuple[str, float]] = {
    "emission_nebula": ("narrowband", 1.0),
    "planetary_nebula": ("narrowband", 1.0),
    "supernova_remnant": ("narrowband", 1.0),
    "galaxy": ("broadband", 1.0),
    "reflection_nebula": ("broadband", 1.0),
    "open_cluster": ("broadband", 1.0),
    "globular_cluster": ("robust", 1.0),
    "other": ("neutral", 0.6),
}

#: Fallback rule for target types not present in :data:`LP_MODEL`.
_UNKNOWN_RULE: tuple[str, float] = ("neutral", 0.6)


def bortle_for(site: SiteProfile) -> int:
    """Return the site's Bortle class, or :data:`DEFAULT_BORTLE` if unrecorded."""
    return site.bortle if site.bortle is not None else DEFAULT_BORTLE


def lp_suitability(target_type: str, bortle: int) -> float:
    """Return a 0..1 multiplier for how well ``target_type`` suits ``bortle``.

    1.0 means the sky brightness barely matters for this target; values toward 0
    mean skyglow strongly degrades it. See :data:`LP_MODEL` for the model.
    """
    kind, base = LP_MODEL.get(target_type, _UNKNOWN_RULE)

    if kind == "narrowband":
        # Line emitters shrug off skyglow; only a token high-Bortle nudge.
        value = base - 0.02 * max(0, bortle - 6)
    elif kind == "broadband":
        # Low-surface-brightness targets wash out as the sky brightens.
        value = max(0.2, base - 0.12 * (bortle - 3))
    elif kind == "robust":
        # Compact, bright globulars decline gently and stay mid-range.
        value = max(0.4, base - 0.06 * (bortle - 3))
    else:  # "neutral"
        value = base

    return max(0.0, min(1.0, value))

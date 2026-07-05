"""Bundled deep-sky-object (DSO) catalog for the observing planner.

The catalog is a small, committed JSON file (``data/dso_catalog.json``) holding
all 110 Messier objects plus a handful of popular non-Messier showpieces. Every
row carries J2000 right ascension / declination in **degrees**, a coarse
``type`` from a fixed vocabulary, the largest angular extent in arcminutes and a
visual magnitude (or ``null``).

This module is pure/local: no clock, no network. It only reads the bundled JSON
relative to ``__file__`` so it works identically whether installed or run from a
checkout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_DATA_PATH = Path(__file__).parent / "data" / "dso_catalog.json"

# The allowed ``type`` vocabulary (documented; the ranker/LP layers key off it).
TARGET_TYPES = frozenset(
    {
        "emission_nebula",
        "planetary_nebula",
        "galaxy",
        "open_cluster",
        "globular_cluster",
        "supernova_remnant",
        "reflection_nebula",
        "other",
    }
)


@dataclass
class DsoTarget:
    """One deep-sky object. Coordinates are J2000 in degrees."""

    id: str  # e.g. "M27", "NGC7000", "C33"
    name: str  # common name, e.g. "Dumbbell Nebula"
    ra_deg: float
    dec_deg: float
    type: str  # one of TARGET_TYPES
    size_arcmin: float  # largest angular extent
    magnitude: float | None = None


def _normalize(text: str) -> str:
    """Fold case and drop spaces for tolerant id/name matching."""
    return text.strip().lower().replace(" ", "")


def load_catalog() -> list[DsoTarget]:
    """Load the bundled DSO catalog as a list of :class:`DsoTarget`."""
    with _DATA_PATH.open(encoding="utf-8") as fh:
        rows = json.load(fh)
    return [DsoTarget(**row) for row in rows]


def find_target(name_or_id: str) -> DsoTarget | None:
    """Return the target whose ``id`` (then ``name``) matches, else ``None``.

    Matching is case-insensitive and space-insensitive. ``id`` is tried first so
    a catalog id always wins over an incidental name collision.
    """
    if not name_or_id:
        return None
    needle = _normalize(name_or_id)
    catalog = load_catalog()
    for target in catalog:
        if _normalize(target.id) == needle:
            return target
    for target in catalog:
        if _normalize(target.name) == needle:
            return target
    return None

"""Observing-planner package for seestar-mcp.

Local-computation planning: a bundled DSO catalog, site profile, a
deterministic astropy observability engine, weather assessment, light-pollution
suitability and a reasoned target ranker. Every verdict/score is reason-tagged;
only weather touches the network.
"""

from __future__ import annotations

from .catalog import DsoTarget, find_target, load_catalog

__all__ = ["DsoTarget", "find_target", "load_catalog"]

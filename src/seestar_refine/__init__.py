"""seestar-refine: a separate FastMCP service that turns the QA keep-list into
a stacked master + preview (DeepSkyStacker) with an optional PixInsight path.

Runs on the single Windows/RTX-4090 processing host as ``python -m
seestar_refine.server``. Shares this repo, its ``uv`` env, and the data dir with
``seestar_mcp`` but is a distinct MCP server because refinement is a separate
concern with external desktop-app dependencies (DSS / PixInsight).
"""

from __future__ import annotations

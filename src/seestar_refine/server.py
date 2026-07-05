"""FastMCP server for seestar-refine: keep-list → stacked master + preview.

A separate MCP service from ``seestar-mcp`` (refinement is a distinct concern
with external desktop-app dependencies). Same two-layer split for testability:

- :class:`RefineController` — plain-async business logic; every method returns a
  JSON-serializable dict and never raises out of a tool path.
- Thin ``@mcp.tool()`` wrappers — one line each; the docstrings are the tool
  descriptions the model sees, written to be honest about side effects.

Transport is stdio (``mcp.run()`` default): this server opens NO inbound network
port. It shells out only to the user-configured DSS/PixInsight executables (never
an arbitrary path from tool args) and provenance-logs every external invocation.
"""

from __future__ import annotations

from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from seestar_mcp.provenance import ProvenanceLog

from .backends import detect_backends
from .config import RefineSettings, get_settings


class RefineController:
    """Testable business logic behind the seestar-refine MCP tools."""

    def __init__(
        self, settings: RefineSettings, *, provenance: ProvenanceLog
    ) -> None:
        self.settings = settings
        self.provenance = provenance

    @classmethod
    def from_settings(
        cls, settings: RefineSettings | None = None
    ) -> RefineController:
        """Build a controller from ``RefineSettings`` (default: cached)."""
        if settings is None:
            settings = get_settings()
        provenance = ProvenanceLog(settings.output_dir / "refine_provenance.jsonl")
        return cls(settings, provenance=provenance)

    async def check_backends(self) -> dict:
        """Report which refinement backends are available on this host."""
        try:
            backends = detect_backends(self.settings)
            self.provenance.log_call(tool="check_backends", args={})
            return {"ok": True, "backends": asdict(backends)}
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}


# ===========================================================================
# Thin MCP registration. Transport is stdio (mcp.run() default): NO inbound
# network port is opened. External DSS/PixInsight paths come only from
# SEESTAR_REFINE_* config, never from tool arguments.
# ===========================================================================

mcp = FastMCP("seestar-refine")

_controller: RefineController | None = None


def get_controller() -> RefineController:
    """Return the lazily-built, cached controller singleton."""
    global _controller
    if _controller is None:
        _controller = RefineController.from_settings()
    return _controller


def set_controller(controller: RefineController | None) -> None:
    """Inject/replace the controller singleton (tests, lifespan management)."""
    global _controller
    _controller = controller


@mcp.tool()
async def check_backends() -> dict:
    """Report available refinement backends: DSS CLI, PixInsight, pixinsight-mcp.

    Read-only. Pure filesystem checks against the SEESTAR_REFINE_* config so the
    image-refinement skill can pick a stacking path.
    """
    return await get_controller().check_backends()


def main() -> None:
    """Run the MCP server over stdio (no inbound network port is opened)."""
    mcp.run()


if __name__ == "__main__":
    main()

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

import re
from dataclasses import asdict
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from seestar_mcp.provenance import ProvenanceLog

from . import dss, wbpp
from .backends import detect_backends
from .config import RefineSettings, get_settings
from .handoff import to_xisf, write_pixinsight_config
from .keeplist import KeepList, load_keep_list
from .preview import make_preview


def _slug(text: str) -> str:
    """Filesystem-safe slug for a target name (letters/digits/dashes)."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return slug or "session"


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

    # --- stacking ---------------------------------------------------------

    def _resolve_keep_list(self, target: str) -> KeepList:
        """Resolve a target's keep-list from the newest QA report, else a glob.

        Preference order (best-effort, never raises):

        1. The newest ``qa_session_report`` JSON under ``data_dir`` whose filename
           mentions the target — parsed via :func:`load_keep_list` (its
           ``keep_list`` resolved against ``data_dir``).
        2. Fallback: every ``.fit``/``.fits`` sub under ``<data_dir>/<target>``,
           wrapped through :func:`load_keep_list` as a synthetic report.
        """
        data_dir = Path(self.settings.data_dir)
        report = self._newest_qa_report(target, data_dir)
        if report is not None:
            keep_list = load_keep_list(report, data_dir=data_dir)
            if keep_list.sub_paths:
                return keep_list

        # Fallback: glob the conventional per-target sub directory.
        sub_dir = data_dir / target
        subs: list[str] = []
        if sub_dir.is_dir():
            for pattern in ("*.fit", "*.fits"):
                subs.extend(str(p) for p in sorted(sub_dir.glob(pattern)))
        return load_keep_list(
            {"target": target, "keep_list": subs}, data_dir=sub_dir
        )

    @staticmethod
    def _newest_qa_report(target: str, data_dir: Path) -> Path | None:
        """Newest QA report JSON for ``target`` under ``data_dir``, else None."""
        if not data_dir.is_dir():
            return None
        slug = _slug(target)
        matches: list[Path] = []
        try:
            for path in data_dir.rglob("*.json"):
                name = path.name.lower()
                if "qa" not in name or "report" not in name:
                    continue
                if slug in name or target.lower() in name:
                    matches.append(path)
        except OSError:
            return None
        if not matches:
            return None
        return max(matches, key=lambda p: p.stat().st_mtime)

    async def stack_keep_list(self, target: str, engine: str = "auto") -> dict:
        """Stack a target's QA keep-list into a master via the chosen engine.

        ``engine``: ``"dss"``/``"auto"`` route to DeepSkyStacker (the default,
        always-available path); ``"wbpp"`` routes to the PixInsight WBPP runner
        (best-effort, requires PixInsight — the skill decides when to use it).
        Resolves the keep-list (newest QA report, else a per-target glob), runs
        the stack, and provenance-logs the external invocation. Never raises.
        """
        try:
            keep_list = self._resolve_keep_list(target)
            if not keep_list.sub_paths:
                return {
                    "ok": False,
                    "engine": engine,
                    "target": target,
                    "error": (
                        f"no keep-list found for {target} — run qa_session_report "
                        "first (or place subs under <data_dir>/<target>)"
                    ),
                }
            # Prefer the report's own target name when it resolved one.
            eff_target = keep_list.target or target

            if engine == "wbpp":
                # PixInsight WBPP path (no auto-preview — the creative finish is
                # done later by the external pixinsight-mcp).
                result = wbpp.run_wbpp(keep_list, self.settings)
                self.provenance.log_call(
                    tool="stack_keep_list",
                    args={
                        "target": eff_target,
                        "engine": result.engine,
                        "n_subs": result.n_subs,
                        "master_path": result.master_path,
                        "preview_path": None,
                    },
                )
                return {
                    "ok": result.ok,
                    "engine": result.engine,
                    "target": result.target,
                    "n_subs": result.n_subs,
                    "master_path": result.master_path,
                    "preview_path": result.preview_path,
                    "stats": result.stats,
                    "error": result.error,
                }

            # "dss" or "auto" -> DeepSkyStacker (WBPP is opt-in via engine).
            result = dss.stack(keep_list, self.settings)

            # Best-effort auto-preview: on a successful stack with a master,
            # write a stretched PNG next to it. A preview failure must NEVER
            # fail the stack (log a note and carry on).
            preview_note: str | None = None
            if result.ok and result.master_path:
                master = Path(result.master_path)
                out_png = Path(self.settings.output_dir) / f"{master.stem}.png"
                preview = make_preview(master, out_png)
                if preview.get("ok"):
                    result.preview_path = preview["preview_path"]
                else:
                    preview_note = (
                        f"preview failed: {preview.get('error', 'unknown')}"
                    )

            self.provenance.log_call(
                tool="stack_keep_list",
                args={
                    "target": eff_target,
                    "engine": result.engine,
                    "n_subs": result.n_subs,
                    "master_path": result.master_path,
                    "preview_path": result.preview_path,
                },
                note=preview_note,
            )
            return {
                "ok": result.ok,
                "engine": result.engine,
                "target": result.target,
                "n_subs": result.n_subs,
                "master_path": result.master_path,
                "preview_path": result.preview_path,
                "stats": result.stats,
                "error": result.error,
            }
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    # --- preview stretch --------------------------------------------------

    async def stretch_master(
        self, master_path: str, params: dict | None = None
    ) -> dict:
        """Auto-stretch a stacked master into an 8-bit PNG preview.

        Delegates to :func:`seestar_refine.preview.make_preview`, writing
        ``<output_dir>/<stem>.png``. ``params`` may carry ``black_point_sigma`` /
        ``midtone`` overrides. Provenance-logs the invocation. Never raises.
        """
        try:
            master = Path(master_path)
            out_png = Path(self.settings.output_dir) / f"{master.stem}.png"
            result = make_preview(master, out_png, params=params)
            self.provenance.log_call(
                tool="stretch_master",
                args={
                    "master_path": str(master),
                    "preview_path": result.get("preview_path"),
                    "ok": result.get("ok"),
                },
            )
            return result
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    # --- PixInsight handoff ----------------------------------------------

    async def prepare_pixinsight_handoff(
        self, master_path: str, target: str
    ) -> dict:
        """Prepare a master for the EXTERNAL ``pixinsight-mcp`` creative finish.

        Writes the ``pixinsight-mcp`` JSON config (target + absolute channel
        paths + output dir) via :func:`write_pixinsight_config` and best-effort
        converts the master to XISF via :func:`to_xisf` (degrades to a FITS
        fallback when the optional ``xisf`` package is absent). Does NOT run
        PixInsight — the creative processing is the external server's job.
        Provenance-logs the invocation. Never raises.
        """
        try:
            out_dir = Path(self.settings.output_dir)
            cfg = write_pixinsight_config(master_path, target, out_dir)
            xisf_out = out_dir / f"{Path(master_path).stem}.xisf"
            xisf_result = to_xisf(master_path, xisf_out)
            self.provenance.log_call(
                tool="prepare_pixinsight_handoff",
                args={
                    "master_path": str(master_path),
                    "target": target,
                    "config_path": cfg.get("config_path"),
                    "xisf_ok": xisf_result.get("ok"),
                },
            )
            return {
                "ok": cfg.get("ok", False),
                "config_path": cfg.get("config_path"),
                "config": cfg.get("config"),
                "xisf": xisf_result,
                "error": cfg.get("error"),
            }
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    async def list_masters(self) -> dict:
        """List masters/previews produced under the configured output dir.

        Read-only. Matches master/preview patterns (``*.fit*``, ``*.tif*``,
        ``*.xisf``, ``*.png``) with size + mtime, newest first. Never raises.
        """
        try:
            out_dir = Path(self.settings.output_dir)
            masters: list[dict] = []
            if out_dir.is_dir():
                seen: set[Path] = set()
                for pattern in ("*.fit*", "*.tif*", "*.xisf", "*.png"):
                    for path in out_dir.glob(pattern):
                        if not path.is_file() or path in seen:
                            continue
                        seen.add(path)
                        st = path.stat()
                        masters.append(
                            {
                                "path": str(path),
                                "name": path.name,
                                "size": st.st_size,
                                "mtime": st.st_mtime,
                            }
                        )
                masters.sort(key=lambda m: m["mtime"], reverse=True)
            self.provenance.log_call(
                tool="list_masters", args={"count": len(masters)}
            )
            return {"ok": True, "masters": masters}
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


@mcp.tool()
async def stack_keep_list(target: str, engine: str = "auto") -> dict:
    """Stack a target's QA keep-list into a master frame.

    SIDE EFFECT: runs a LONG external stacking process (DeepSkyStacker) and WRITES
    files (a DSS file list + the integrated master) under the configured output
    dir. Refines ONLY the keep-list (never rejected subs). ``engine``: ``"dss"``
    or ``"auto"`` use DeepSkyStacker (default/always-available); ``"wbpp"``
    (PixInsight) is not available until a later task. Returns the master path +
    basic stats, or a structured error (e.g. DSS not configured). The external
    invocation is provenance-logged.
    """
    return await get_controller().stack_keep_list(target, engine)


@mcp.tool()
async def stretch_master(master_path: str, params: dict | None = None) -> dict:
    """Auto-stretch a stacked master into an 8-bit PNG preview for review.

    SIDE EFFECT: WRITES a ``<stem>.png`` preview under the configured output dir.
    Reads ``master_path`` (FITS via astropy; TIFF/other via Pillow), applies a
    sigma-clipped midtone-transfer-function auto-stretch, and saves the PNG.
    ``params`` optionally carries ``black_point_sigma`` / ``midtone`` overrides.
    Returns the preview path + basic stats, or a structured error (e.g. an
    unreadable master). The invocation is provenance-logged.
    """
    return await get_controller().stretch_master(master_path, params)


@mcp.tool()
async def prepare_pixinsight_handoff(master_path: str, target: str) -> dict:
    """Prepare a stacked master for the EXTERNAL pixinsight-mcp creative finish.

    SIDE EFFECT: WRITES a ``<target>_pixinsight.json`` config (and, if the
    optional ``xisf`` package is installed, an ``.xisf`` copy of the master)
    under the configured output dir. This tool does NOT run PixInsight — the
    creative processing (gradient/deconvolution/denoise/stretch) is performed by
    the user's separate, EXTERNAL ``pixinsight-mcp`` server, which the skill
    drives with the returned config. When ``xisf`` is absent, the handoff
    degrades to the FITS master (documented fallback). Provenance-logged.
    """
    return await get_controller().prepare_pixinsight_handoff(
        master_path, target
    )


@mcp.tool()
async def list_masters() -> dict:
    """List masters/previews produced under the configured output dir.

    Read-only. Returns each file's path, name, size, and mtime (newest first),
    matching master/preview patterns (FITS/TIFF/XISF/PNG) so the skill can show
    what has been produced.
    """
    return await get_controller().list_masters()


def main() -> None:
    """Run the MCP server over stdio (no inbound network port is opened)."""
    mcp.run()


if __name__ == "__main__":
    main()

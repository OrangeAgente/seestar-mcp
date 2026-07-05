"""FastMCP server for seestar-mcp: 18 auditable Seestar S50 control/QA tools.

Two layers, deliberately separated for testability:

- :class:`SeestarController` — plain-async business logic. Every method returns a
  JSON-serializable dict and catches :class:`AlpacaError`, returning
  ``{"ok": False, "error": ...}`` instead of raising. This is unit-testable
  without any MCP machinery.
- The thin ``@mcp.tool()`` wrappers below — one per controller method, each a
  one-line ``return await get_controller().<method>(...)``. Tool docstrings are
  the tool *descriptions* the model sees, so they are written to be honest and
  explicit about side effects (anti tool-poisoning: a misleading description is a
  security defect, not just a docs nit).

Transport / network posture
----------------------------
``main()`` runs FastMCP over **stdio** (``mcp.run()`` default). This server opens
**no inbound network port**: Claude Code spawns it as a subprocess and speaks
stdio, and (for a Remote Control session) must register it *before* the session
starts. The only network egress is the outbound HTTP this process makes to
``seestar_alp`` (:5555) and the device data ports — those localhost/LAN-only bind
concerns are handled in :mod:`seestar_mcp.config` and
:mod:`seestar_mcp.data_client`, not here. No secret is imported into any tool
signature; credentials live only in :mod:`seestar_mcp.secrets`.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from .alpaca_client import AlpacaClient, AlpacaError, AlpacaNotImplemented
from .data_client import DataClient
from .provenance import ProvenanceLog, SessionManifest
from .qa_tier1 import Tier1Monitor
from .qa_tier2 import analyze_session, write_report

if TYPE_CHECKING:
    from .config import Settings


def _slug(text: str) -> str:
    """Filesystem/id-safe slug from a target name (letters, digits, dashes)."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text.strip()).strip("-").lower()
    return slug or "session"


class SeestarController:
    """Testable business logic behind the MCP tools.

    Holds the wired clients plus mutable per-session state (``session_id``,
    ``manifest``, ``target``). Async methods each return a JSON-serializable dict
    and never raise :class:`AlpacaError` — they catch it and return
    ``{"ok": False, "error": ..., "error_number": ...}``.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        provenance: ProvenanceLog,
        alpaca: AlpacaClient,
        data: DataClient,
        tier1: Tier1Monitor,
    ) -> None:
        self.settings = settings
        self.provenance = provenance
        self.alpaca = alpaca
        self.data = data
        self.tier1 = tier1

        # Per-session state.
        self.session_id: str | None = None
        self.manifest: SessionManifest | None = None
        self.target: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> SeestarController:
        """Build a fully-wired controller from ``Settings`` (default: cached)."""
        if settings is None:
            from .config import get_settings

            settings = get_settings()
        provenance = ProvenanceLog(settings.provenance_log)
        alpaca = AlpacaClient.from_settings(settings, provenance)
        data = DataClient.from_settings(settings, alpaca, provenance)
        tier1 = Tier1Monitor(alpaca, provenance=provenance)
        return cls(
            settings,
            provenance=provenance,
            alpaca=alpaca,
            data=data,
            tier1=tier1,
        )

    # --- internal helpers -------------------------------------------------

    def _config_summary(self) -> dict[str, Any]:
        """Non-secret settings subset recorded into a session manifest.

        Only endpoints/thresholds — never a secret. The manifest additionally
        runs this through ``redact`` on construction as defence in depth.
        """
        s = self.settings
        return {
            "alpaca_base_url": s.alpaca_base_url,
            "alpaca_device_num": s.alpaca_device_num,
            "data_dir": str(s.data_dir),
            "manifest_dir": str(s.manifest_dir),
            "qa_fwhm_sigma": s.qa_fwhm_sigma,
            "qa_eccentricity_reject": s.qa_eccentricity_reject,
            "qa_snr_floor_factor": s.qa_snr_floor_factor,
            "qa_starcount_floor_factor": s.qa_starcount_floor_factor,
        }

    @staticmethod
    async def _maybe(coro: Any) -> Any:
        """Await ``coro``; on ``AlpacaNotImplemented`` return None.

        Used by :meth:`get_status` so the ~4-of-52 GETs the Seestar lacks resolve
        to ``None`` for that field rather than failing the whole status read.
        Other :class:`AlpacaError`s propagate to the method-level guard.
        """
        try:
            return await coro
        except AlpacaNotImplemented:
            return None

    # --- control / state --------------------------------------------------

    async def connect_telescope(self) -> dict:
        """Connect to the telescope via seestar_alp (Alpaca ``connected``)."""
        try:
            await self.alpaca.set_connected(True)
            connected = await self._maybe(self.alpaca.get_connected())
            return {"ok": True, "connected": connected}
        except AlpacaError as exc:
            return _err(exc)

    async def get_status(self) -> dict:
        """Read connection + pointing + tracking/slewing state.

        Each field is read independently; a ``NotImplemented`` GET (expected for
        a few standard ASCOM properties the Seestar lacks) resolves to ``None``.
        """
        try:
            status = {
                "connected": await self._maybe(self.alpaca.get_connected()),
                "rightascension": await self._maybe(self.alpaca.get_ra()),
                "declination": await self._maybe(self.alpaca.get_dec()),
                "tracking": await self._maybe(self.alpaca.get_tracking()),
                "slewing": await self._maybe(self.alpaca.is_slewing()),
            }
            return {"ok": True, **status}
        except AlpacaError as exc:
            return _err(exc)

    async def get_view_state(self) -> dict:
        """Read the device's live ``get_view_state`` telemetry (native method)."""
        try:
            state = await self.alpaca.method_sync("get_view_state")
            if (bad := _native_fail(state)) is not None:
                return bad
            return {"ok": True, "view_state": state}
        except AlpacaError as exc:
            return _err(exc)

    async def goto_target(
        self,
        name: str,
        ra: float,
        dec: float,
        use_lp_filter: bool = False,
        session_id: str | None = None,
    ) -> dict:
        """Slew to a target and start a session (creates the session manifest).

        This commands telescope MOTION. It derives a deterministic session id
        (target + UTC timestamp, unless ``session_id`` is supplied), opens a
        :class:`SessionManifest`, and issues the native goto.
        """
        try:
            if session_id is None:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                session_id = f"{_slug(name)}-{stamp}"
            self.session_id = session_id
            self.target = name
            self.manifest = SessionManifest(
                session_id,
                self.settings.manifest_dir,
                target=name,
                config_summary=self._config_summary(),
            )
            self.manifest.set_meta(
                target=name, ra=ra, dec=dec, lp_filter=bool(use_lp_filter)
            )
            # FIRMWARE-DEPENDENT: native goto method + param shape (unconfirmed
            # against hardware; single updatable point).
            result = await self.alpaca.method_sync(
                "iscope_start_view",
                {
                    "mode": "star",
                    "target_ra_dec": [ra, dec],
                    "target_name": name,
                    "lp_filter": bool(use_lp_filter),
                },
            )
            # A native "Error: ..." result means the scope did NOT start the view
            # (e.g. "Exceeded allotted wait time"). Surface ok:false so the
            # run-session flow won't proceed to solve/stack on a phantom goto.
            if (bad := _native_fail(
                result,
                session_id=session_id,
                target=name,
                ra=ra,
                dec=dec,
                lp_filter=bool(use_lp_filter),
            )) is not None:
                return bad
            return {
                "ok": True,
                "session_id": session_id,
                "target": name,
                "ra": ra,
                "dec": dec,
                "lp_filter": bool(use_lp_filter),
                "result": result,
            }
        except AlpacaError as exc:
            return _err(exc)

    async def start_stack(self) -> dict:
        """Start live-stacking exposures (begins capturing/integrating subs)."""
        try:
            # FIRMWARE-DEPENDENT: native start-stack method name.
            result = await self.alpaca.method_sync("iscope_start_stack")
            if (bad := _native_fail(result)) is not None:
                return bad
            return {"ok": True, "result": result}
        except AlpacaError as exc:
            return _err(exc)

    async def stop_view(self, mode: str = "Stack") -> dict:
        """Stop the current view/stack (``"Stack"`` or ``"ContinuousExposure"``)."""
        try:
            # FIRMWARE-DEPENDENT: native stop method + arg shape.
            result = await self.alpaca.method_sync("iscope_stop_view", [mode])
            if (bad := _native_fail(result, mode=mode)) is not None:
                return bad
            return {"ok": True, "mode": mode, "result": result}
        except AlpacaError as exc:
            return _err(exc)

    async def run_autofocus(self) -> dict:
        """Run the autofocus routine; seed the Tier-1 focus baseline afterwards."""
        try:
            result = await self.alpaca.method_sync("start_auto_focus")
            if (bad := _native_fail(result)) is not None:
                return bad
            focus_pos = None
            try:
                focus = await self.alpaca.method_sync(
                    "get_focuser_position", {"ret_obj": True}
                )
                focus_pos = _extract_focus_pos(focus)
                if focus_pos is not None:
                    self.tier1.set_focus_baseline(focus_pos)
            except AlpacaError:
                # Best-effort baseline only; do not fail autofocus on this.
                focus_pos = None
            return {"ok": True, "result": result, "focus_pos": focus_pos}
        except AlpacaError as exc:
            return _err(exc)

    async def get_focuser_position(self) -> dict:
        """Read the current focuser position (native ``get_focuser_position``)."""
        try:
            focus = await self.alpaca.method_sync(
                "get_focuser_position", {"ret_obj": True}
            )
            if (bad := _native_fail(focus)) is not None:
                return bad
            return {"ok": True, "focuser": focus, "focus_pos": _extract_focus_pos(focus)}
        except AlpacaError as exc:
            return _err(exc)

    async def plate_solve(self) -> dict:
        """Plate-solve the current field: start a solve, then read the result."""
        try:
            # FIRMWARE-DEPENDENT: solve method names.
            await self.alpaca.method_sync("start_solve")
            result = await self.alpaca.method_sync("get_solve_result")
            if (bad := _native_fail(result)) is not None:
                return bad
            return {"ok": True, "solve_result": result}
        except AlpacaError as exc:
            return _err(exc)

    async def set_filter(self, position: int) -> dict:
        """Set the filter wheel position (LP / IR-Cut / Dark, by index)."""
        try:
            result = await self.alpaca.method_sync("set_wheel_position", [position])
            if (bad := _native_fail(result, position=position)) is not None:
                return bad
            return {"ok": True, "position": position, "result": result}
        except AlpacaError as exc:
            return _err(exc)

    async def set_dew_heater(self, on: bool) -> dict:
        """Turn the dew heater on/off.

        Enabling the heater warms the sensor and INVALIDATES any darks built at a
        colder temperature — the caller should rebuild darks / re-run enhancement
        after toggling this.
        """
        try:
            # FIRMWARE-DEPENDENT: setting key for the dew heater.
            result = await self.alpaca.method_sync(
                "set_setting", {"heater": bool(on)}
            )
            if (bad := _native_fail(result, heater=bool(on))) is not None:
                return bad
            return {
                "ok": True,
                "heater": bool(on),
                "result": result,
                "note": (
                    "Enabling the dew heater changes sensor temperature and "
                    "invalidates existing dark frames; rebuild darks afterwards."
                ),
            }
        except AlpacaError as exc:
            return _err(exc)

    async def park(self) -> dict:
        """Park the telescope (stops tracking and moves the mount to park)."""
        try:
            # FIRMWARE-DEPENDENT: native park method name.
            result = await self.alpaca.method_sync("scope_park")
            if (bad := _native_fail(result)) is not None:
                return bad
            return {"ok": True, "result": result}
        except AlpacaError as exc:
            return _err(exc)

    async def shutdown(self) -> dict:
        """Power down the Seestar. This TERMINATES the seestar_alp control link.

        After this returns, no further tool calls will reach the device until it
        is physically powered back on and seestar_alp reconnects.
        """
        try:
            result = await self.alpaca.method_sync("pi_shutdown")
            if (bad := _native_fail(result)) is not None:
                return bad
            return {
                "ok": True,
                "result": result,
                "note": (
                    "Seestar shutdown issued; this ends the seestar_alp control "
                    "link until the device is powered back on."
                ),
            }
        except AlpacaError as exc:
            return _err(exc)

    # --- data -------------------------------------------------------------

    async def list_subs(self, target: str | None = None) -> dict:
        """List RAW FITS subs the device has saved (optionally one target)."""
        try:
            subs = await self.data.list_subs(target)
            return {
                "ok": True,
                "count": len(subs),
                "subs": [dataclasses.asdict(s) for s in subs],
            }
        except AlpacaError as exc:
            return _err(exc)

    async def download_subs(
        self,
        target: str | None = None,
        names: list[str] | None = None,
        dest: str | None = None,
    ) -> dict:
        """Download RAW subs to local storage (HTTP with SMB fallback).

        Resolves the sub list (optionally one ``target``), optionally filters to
        ``names``, downloads each, and hashes it into the provenance chain.
        """
        try:
            subs = await self.data.list_subs(target)
            if names is not None:
                wanted = set(names)
                subs = [s for s in subs if s.name in wanted]
            results = await self.data.download_subs(subs, dest)
            return {"ok": True, "count": len(results), "downloaded": results}
        except AlpacaError as exc:
            return _err(exc)

    # --- QA ---------------------------------------------------------------

    async def qa_tier1(self) -> dict:
        """Poll firmware telemetry once; return a compact snapshot + health flags.

        Read-only. The bulky raw telemetry is kept in the provenance log but
        trimmed from the returned snapshot to keep tool output small. The flags
        are neutral HEALTH signals, not quality verdicts.
        """
        try:
            snap = await self.tier1.poll()
            snap_dict = dataclasses.asdict(snap)
            snap_dict.pop("raw", None)  # keep output compact; raw stays in provenance
            return {
                "ok": True,
                "snapshot": snap_dict,
                "flags": self.tier1.check(),
                "status_line": self.tier1.status_line(),
                "trends": self.tier1.trends(),
            }
        except AlpacaError as exc:
            return _err(exc)

    def _resolve_paths(
        self, target: str | None, paths: list[str] | None
    ) -> list[Path]:
        """Resolve explicit ``paths`` else glob ``data_dir`` for target FITS."""
        if paths is not None:
            return [Path(p) for p in paths]
        data_dir = Path(self.settings.data_dir)
        if not data_dir.exists():
            return []
        matches: list[Path] = []
        for pattern in ("*.fit", "*.fits"):
            for p in sorted(data_dir.glob(pattern)):
                if target is None or target.lower() in p.name.lower():
                    matches.append(p)
        return matches

    @staticmethod
    def _compact_report(report: Any) -> dict:
        """Summarize a SessionReport without dumping every metric field."""
        return {
            "target": report.target,
            "total": report.total,
            "kept": report.kept,
            "wfwhm": report.wfwhm,
            "medians": report.medians,
            "dominant_reject_cause": report.dominant_reject_cause,
            "subs": [
                {"name": v.name, "verdict": v.verdict, "reasons": v.reasons}
                for v in report.subs
            ],
        }

    async def qa_tier2(
        self, target: str | None = None, paths: list[str] | None = None
    ) -> dict:
        """Score RAW subs (photutils FWHM/ecc/SNR/stars) into PASS/MARGINAL/REJECT.

        Read-only analysis. Returns a compact per-sub verdict summary + keep-list;
        does not dump full metrics for every sub.
        """
        try:
            resolved = self._resolve_paths(target, paths)
            report = analyze_session(
                resolved, self.settings, target=target, provenance=self.provenance
            )
            return {
                "ok": True,
                "summary": self._compact_report(report),
                "keep_list": report.keep_list,
            }
        except AlpacaError as exc:
            return _err(exc)

    async def qa_session_report(
        self, target: str | None = None, paths: list[str] | None = None
    ) -> dict:
        """Full session wind-down: score subs, write JSON+MD report + manifest.

        Like ``qa_tier2`` but also records every verdict into the session manifest
        (creating a fallback manifest if no session is open), writes a Markdown +
        JSON QA report, and writes the manifest. Returns the artifact paths.
        """
        try:
            resolved = self._resolve_paths(target, paths)
            eff_target = target or self.target
            manifest = self.manifest
            if manifest is None:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                sid = self.session_id or f"{_slug(eff_target or 'session')}-{stamp}"
                self.session_id = sid
                manifest = SessionManifest(
                    sid,
                    self.settings.manifest_dir,
                    target=eff_target,
                    config_summary=self._config_summary(),
                )
                self.manifest = manifest

            report = analyze_session(
                resolved,
                self.settings,
                target=eff_target,
                provenance=self.provenance,
                manifest=manifest,
            )

            reports_dir = Path(self.settings.data_dir) / "reports"
            stem = f"qa_report_{self.session_id}"
            md_path, json_path = write_report(report, reports_dir, stem=stem)
            manifest_path = manifest.write()

            return {
                "ok": True,
                "summary": self._compact_report(report),
                "keep_list": report.keep_list,
                "report_json": str(json_path),
                "report_md": str(md_path),
                "manifest": str(manifest_path),
            }
        except AlpacaError as exc:
            return _err(exc)

    # --- lifecycle --------------------------------------------------------

    async def aclose(self) -> None:
        """Close underlying async clients."""
        await self.alpaca.aclose()
        close = getattr(self.data, "aclose", None)
        if close is not None:
            await self.data.aclose()


def _err(exc: AlpacaError) -> dict:
    """Uniform error envelope for a caught :class:`AlpacaError`."""
    return {
        "ok": False,
        "error": str(exc),
        "error_number": getattr(exc, "error_number", None),
    }


def _native_error(value: Any) -> str | None:
    """Return an error string if a native action result signals failure, else None.

    seestar_alp tunnels native JSON-RPC results verbatim inside an otherwise-ok
    Alpaca envelope. When the device is idle/slow it can return a result *string*
    like ``"Error: Exceeded allotted wait time for result"`` even though the
    Alpaca ``ErrorNumber`` is 0. Detect that so the controller surfaces it as
    ``ok:false`` instead of a false ``ok:true``. Handles both a bare string and a
    dict whose ``"result"`` is such a string.
    """
    if isinstance(value, str) and value.strip().lower().startswith("error"):
        return value
    if isinstance(value, dict):
        result = value.get("result")
        if isinstance(result, str) and result.strip().lower().startswith("error"):
            return result
    return None


def _native_fail(value: Any, **extra: Any) -> dict | None:
    """Return an ``ok:false`` envelope if ``value`` is a native error, else None.

    Wraps :func:`_native_error` so every controller method that hands a native
    ``method_sync`` result back to the caller can guard it uniformly::

        if (bad := _native_fail(result)) is not None:
            return bad

    ``extra`` carries through any context fields (e.g. ``session_id`` on a goto)
    so a surfaced error still reports what was attempted.
    """
    err = _native_error(value)
    if err is not None:
        return {"ok": False, "error": err, "raw": value, **extra}
    return None


def _extract_focus_pos(focus: Any) -> int | None:
    """Best-effort focuser-position int from a native focuser response."""
    if isinstance(focus, (int, float)):
        return int(focus)
    if isinstance(focus, dict):
        for key in ("step", "focus_pos", "focuser_position", "position", "value"):
            val = focus.get(key)
            if isinstance(val, (int, float)):
                return int(val)
    return None


# ===========================================================================
# Thin MCP registration. The transport is stdio (mcp.run() default): this
# server opens NO inbound network port — Claude Code spawns it and speaks stdio,
# and must register it BEFORE a Remote Control session starts. Localhost/LAN
# bind concerns for seestar_alp (:5555) and the device data ports are handled in
# config.py / data_client.py, not here.
# ===========================================================================

mcp = FastMCP("seestar-mcp")

_controller: SeestarController | None = None


def get_controller() -> SeestarController:
    """Return the lazily-built, cached controller singleton."""
    global _controller
    if _controller is None:
        _controller = SeestarController.from_settings()
    return _controller


def set_controller(controller: SeestarController | None) -> None:
    """Inject/replace the controller singleton (tests, lifespan management)."""
    global _controller
    _controller = controller


@mcp.tool()
async def connect_telescope() -> dict:
    """Connect to the Seestar via seestar_alp. No motion; safe to call anytime."""
    return await get_controller().connect_telescope()


@mcp.tool()
async def get_status() -> dict:
    """Read connection, RA/Dec pointing, and tracking/slewing state. Read-only."""
    return await get_controller().get_status()


@mcp.tool()
async def get_view_state() -> dict:
    """Read the device's live view/stacking telemetry. Read-only."""
    return await get_controller().get_view_state()


@mcp.tool()
async def goto_target(
    name: str, ra: float, dec: float, use_lp_filter: bool = False
) -> dict:
    """Slew the telescope to a target and start a session.

    SIDE EFFECT: commands telescope MOTION and opens a new session manifest.
    ``ra``/``dec`` are the target coordinates; ``use_lp_filter`` toggles the
    light-pollution filter.
    """
    return await get_controller().goto_target(name, ra, dec, use_lp_filter)


@mcp.tool()
async def start_stack() -> dict:
    """Start live-stacking. SIDE EFFECT: begins capturing/integrating exposures."""
    return await get_controller().start_stack()


@mcp.tool()
async def stop_view(mode: str = "Stack") -> dict:
    """Stop the current view/stack. SIDE EFFECT: halts capture for the given mode.

    ``mode`` is ``"Stack"`` or ``"ContinuousExposure"``.
    """
    return await get_controller().stop_view(mode)


@mcp.tool()
async def run_autofocus() -> dict:
    """Run the autofocus routine. SIDE EFFECT: moves the focuser to refocus."""
    return await get_controller().run_autofocus()


@mcp.tool()
async def get_focuser_position() -> dict:
    """Read the current focuser position. Read-only."""
    return await get_controller().get_focuser_position()


@mcp.tool()
async def plate_solve() -> dict:
    """Plate-solve the current field and return the solution. Read-only pointing."""
    return await get_controller().plate_solve()


@mcp.tool()
async def set_filter(position: int) -> dict:
    """Set the filter wheel position (LP / IR-Cut / Dark).

    SIDE EFFECT: physically moves the filter wheel to the given index.
    """
    return await get_controller().set_filter(position)


@mcp.tool()
async def set_dew_heater(on: bool) -> dict:
    """Turn the dew heater on or off.

    SIDE EFFECT: changes sensor temperature; enabling it INVALIDATES existing
    dark frames (rebuild darks afterwards).
    """
    return await get_controller().set_dew_heater(on)


@mcp.tool()
async def park() -> dict:
    """Park the telescope. SIDE EFFECT: stops tracking and moves the mount to park."""
    return await get_controller().park()


@mcp.tool()
async def shutdown() -> dict:
    """Power down the Seestar.

    SIDE EFFECT: this TERMINATES the seestar_alp control link — no further tool
    calls reach the device until it is powered back on.
    """
    return await get_controller().shutdown()


@mcp.tool()
async def list_subs(target: str | None = None) -> dict:
    """List RAW FITS subs saved on the device (optionally one target). Read-only."""
    return await get_controller().list_subs(target)


@mcp.tool()
async def download_subs(
    target: str | None = None,
    names: list[str] | None = None,
    dest: str | None = None,
) -> dict:
    """Download RAW subs to local storage (HTTP, SMB fallback).

    SIDE EFFECT: writes FITS files to the local data directory (hashed into the
    provenance log). Optionally filter by ``target`` and/or explicit ``names``.
    """
    return await get_controller().download_subs(target, names, dest)


@mcp.tool()
async def qa_tier1() -> dict:
    """Poll firmware telemetry once; return a snapshot + neutral health flags.

    Read-only. Flags are HEALTH signals for the anomaly playbook, not quality
    verdicts.
    """
    return await get_controller().qa_tier1()


@mcp.tool()
async def qa_tier2(target: str | None = None, paths: list[str] | None = None) -> dict:
    """Score RAW subs into PASS/MARGINAL/REJECT with per-sub reasons + keep-list.

    Read-only FITS analysis (photutils). Provide explicit ``paths`` or a
    ``target`` to glob the local data directory.
    """
    return await get_controller().qa_tier2(target, paths)


@mcp.tool()
async def qa_session_report(
    target: str | None = None, paths: list[str] | None = None
) -> dict:
    """Wind down a session: score subs, then WRITE a JSON+MD report and manifest.

    SIDE EFFECT: writes report and manifest files to local storage. Returns the
    keep-list and the artifact paths.
    """
    return await get_controller().qa_session_report(target, paths)


def main() -> None:
    """Run the MCP server over stdio (no inbound network port is opened)."""
    mcp.run()


if __name__ == "__main__":
    main()

"""FastMCP server for seestar-mcp: 33 auditable Seestar S50 control/QA/planning tools.

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
from .planning.astro import azalt_at, dark_window, moon_illumination, observability
from .planning.autonomous import evaluate_guardrails, plan_night
from .planning.catalog import find_target, load_catalog
from .planning.obstructions import (
    location_status,
    record_sky_result,
    suggest_obstructions,
)
from .planning.projects import (
    get_project as _get_project,
    load_projects,
    log_session_result as _log_session_result,
    recommend_projects as _recommend_projects,
    upsert_project,
)
from .planning.ranker import rank_targets
from .planning.site import SiteProfile, load_site, save_site
from .planning.weather import assess_conditions as assess_conditions_weather
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

    # --- planning ---------------------------------------------------------

    def _site_path(self) -> Path:
        """Path of the persisted site profile under the configured data dir."""
        return self.settings.data_dir / "site_profile.json"

    def _projects_path(self) -> Path:
        """Path of the persisted projects/history store under the data dir."""
        return self.settings.data_dir / "projects.json"

    def _sky_log_path(self) -> Path:
        """Path of the local weather-gated sky-failure histogram under the data dir."""
        return self.settings.data_dir / "sky_failures.json"

    async def _current_gps(self) -> tuple[float, float] | None:
        """Best-effort scope GPS ``(lat, lon)`` from ``get_device_state``; None if
        unavailable.

        This is I/O at the tool layer (like the weather read) — the planning cores
        stay pure/deterministic. Any device fault or an empty/unparseable state
        resolves to ``None`` (GPS unknown → fail-safe: assume the saved site).
        """
        try:
            dev = await self.alpaca.method_sync("get_device_state")
            return _parse_gps(dev)
        except Exception:  # noqa: BLE001 - any device fault → GPS unknown
            return None

    async def _location_block(self, site: SiteProfile) -> dict:
        """Reconcile the scope's live GPS against the saved site and DISCLOSE any
        mismatch, so a stale horizon mask is never silently applied at a new site.

        Returns ``{"matched", "distance_km", "site_name", "mask_applied",
        "warning"}``:

        * GPS unknown (``_current_gps`` None) → ``matched=None``,
          ``mask_applied=True`` (assume the saved site) + an "unverified" note.
        * within ``location_tolerance_km`` → ``matched=True``, ``mask_applied=True``.
        * beyond tolerance → ``matched=False``, ``mask_applied=False`` + a warning
          that the mask was NOT applied and a new profile should be set/confirmed.
        """
        gps = await self._current_gps()
        if gps is None:
            return {
                "matched": None,
                "distance_km": None,
                "site_name": site.name,
                "mask_applied": True,
                "warning": f"GPS unverified — assuming saved site '{site.name}'.",
            }
        ok, dist = location_status(site, gps[0], gps[1])
        if ok:
            return {
                "matched": True,
                "distance_km": round(dist, 1),
                "site_name": site.name,
                "mask_applied": True,
                "warning": None,
            }
        return {
            "matched": False,
            "distance_km": round(dist, 1),
            "site_name": site.name,
            "mask_applied": False,
            "warning": (
                f"Scope is ~{dist:.0f} km from saved site '{site.name}' — horizon "
                "mask NOT applied. Set/confirm a profile for this location."
            ),
        }

    async def get_site_profile(self) -> dict:
        """Return the persisted observing-site profile, if one has been set.

        Read-only. Returns ``ok:false`` (not an error) when no profile exists so
        the caller can prompt for one via ``set_site_profile``.
        """
        try:
            self.provenance.log_call(tool="get_site_profile", args={})
            profile = load_site(self._site_path())
            if profile is None:
                return {"ok": False, "error": "no site profile set — use set_site_profile"}
            return {"ok": True, "profile": dataclasses.asdict(profile)}
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    async def set_site_profile(
        self,
        name: str,
        lat: float,
        lon: float,
        elevation_m: float = 0.0,
        bortle: int | None = None,
        sqm: float | None = None,
        horizon_mask: list[list[float]] | None = None,
        min_altitude_deg: float = 20.0,
        field_rotation_ceiling_deg: float = 60.0,
    ) -> dict:
        """Persist the observing-site profile used by every planning tool.

        Records position, sky-darkness (Bortle/SQM), a horizon mask
        (``[[az_min, az_max, alt_min], ...]``) and the usable-altitude band.
        Writes JSON under the data dir; no device motion.
        """
        try:
            self.provenance.log_call(
                tool="set_site_profile",
                args={
                    "name": name,
                    "lat": lat,
                    "lon": lon,
                    "elevation_m": elevation_m,
                    "bortle": bortle,
                    "sqm": sqm,
                    "min_altitude_deg": min_altitude_deg,
                    "field_rotation_ceiling_deg": field_rotation_ceiling_deg,
                },
            )
            mask = [tuple(arc) for arc in (horizon_mask or [])]
            profile = SiteProfile(
                name=name,
                lat_deg=lat,
                lon_deg=lon,
                elevation_m=elevation_m,
                bortle=bortle,
                sqm=sqm,
                horizon_mask=mask,
                min_altitude_deg=min_altitude_deg,
                field_rotation_ceiling_deg=field_rotation_ceiling_deg,
            )
            save_site(profile, self._site_path())
            return {"ok": True, "profile": dataclasses.asdict(profile)}
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    async def assess_conditions(self, date: str | None = None) -> dict:
        """Go/no-go sky verdict for tonight: weather + moon over the dark window.

        Reads the clock only to resolve "tonight" when ``date`` is omitted. A
        weather outage degrades to ``go=None`` (non-fatal); planning still runs.
        """
        from datetime import datetime, timezone

        try:
            self.provenance.log_call(tool="assess_conditions", args={"date": date})
            when = date or datetime.now(timezone.utc).isoformat()
            site = load_site(self._site_path())
            if site is None:
                return {"ok": False, "error": "no site profile set"}
            block = await self._location_block(site)
            site_for_engine = (
                site
                if block["mask_applied"]
                else dataclasses.replace(site, horizon_mask=[])
            )
            window = dark_window(site_for_engine, when)
            illum = moon_illumination(when)
            assessment = await assess_conditions_weather(site_for_engine, window, illum)
            return {"ok": True, "location": block, **dataclasses.asdict(assessment)}
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    async def get_target_observability(
        self, target: str, date: str | None = None
    ) -> dict:
        """Observability of one named DSO tonight (altitude, sweet band, moon).

        Reads the clock only to resolve "tonight" when ``date`` is omitted.
        Read-only; no device motion.
        """
        from datetime import datetime, timezone

        try:
            self.provenance.log_call(
                tool="get_target_observability",
                args={"target": target, "date": date},
            )
            when = date or datetime.now(timezone.utc).isoformat()
            site = load_site(self._site_path())
            if site is None:
                return {"ok": False, "error": "no site profile set"}
            t = find_target(target)
            if t is None:
                return {"ok": False, "error": f"unknown target: {target}"}
            obs = observability(site, t, when)
            return {
                "ok": True,
                "target": dataclasses.asdict(t),
                "observability": dataclasses.asdict(obs),
            }
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    async def plan_targets(
        self,
        date: str | None = None,
        types: list[str] | None = None,
        min_alt: float | None = None,
        limit: int = 10,
        avoid_recent_days: int = 2,
        prefer_projects: bool = True,
    ) -> dict:
        """Rank tonight's best DSO targets — a scored, reasoned shortlist.

        Reads the clock only to resolve "tonight" when ``date`` is omitted.
        Returns a compact per-target summary (id/name/type/score/reasons/window
        + key observability numbers) rather than the full nested record.

        When ``prefer_projects`` (default), the persisted projects/history store
        is loaded and threaded into the ranker so active projects still short of
        their goal are boosted and targets imaged within ``avoid_recent_days``
        are suppressed. Set ``prefer_projects=False`` for pure Phase-1 ranking.
        """
        from datetime import datetime, timezone

        try:
            self.provenance.log_call(
                tool="plan_targets",
                args={
                    "date": date,
                    "types": types,
                    "min_alt": min_alt,
                    "limit": limit,
                    "avoid_recent_days": avoid_recent_days,
                    "prefer_projects": prefer_projects,
                },
            )
            when = date or datetime.now(timezone.utc).isoformat()
            site = load_site(self._site_path())
            if site is None:
                return {"ok": False, "error": "no site profile set"}
            # GPS reconcile: if the scope has moved off the saved site, disclose it
            # and run the astronomy against a mask-stripped copy (keep the altitude
            # floor; drop the stale obstruction arcs) so blocked sky is not dropped.
            block = await self._location_block(site)
            site_for_engine = (
                site
                if block["mask_applied"]
                else dataclasses.replace(site, horizon_mask=[])
            )
            illum = moon_illumination(when)
            window = dark_window(site_for_engine, when)
            conditions = await assess_conditions_weather(site_for_engine, window, illum)
            projects = (
                load_projects(self._projects_path()) if prefer_projects else None
            )
            plans = rank_targets(
                site_for_engine,
                when,
                load_catalog(),
                conditions,
                types=types,
                min_alt=min_alt,
                limit=limit,
                projects=projects,
                now_utc=when,
                recent_days=avoid_recent_days,
            )
            return {
                "ok": True,
                "location": block,
                "conditions": {
                    "go": conditions.go,
                    "suitability": conditions.suitability,
                    "source": conditions.source,
                },
                "count": len(plans),
                "targets": [
                    {
                        "id": p.target.id,
                        "name": p.target.name,
                        "type": p.target.type,
                        "score": p.score,
                        "reasons": p.reasons,
                        "best_window_utc": p.best_window_utc,
                        "recommended_subs": p.recommended_subs,
                        "recommended_exposure_s": p.recommended_exposure_s,
                        "framing_note": p.framing_note,
                        "max_alt_deg": round(p.observability.max_alt_deg, 1),
                        "transit_utc": p.observability.transit_utc,
                        "sweet_band_min": round(
                            p.observability.dark_minutes_in_sweet_band
                        ),
                        "moon_sep_deg": round(p.observability.moon_sep_deg, 1),
                    }
                    for p in plans
                ],
            }
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    # --- learned horizon mask (obstruction inference) ---------------------

    async def log_sky_result(
        self,
        target: str | None = None,
        az: float | None = None,
        alt: float | None = None,
        solved: bool = True,
        weather_go: bool | None = None,
    ) -> dict:
        """Record one plate-solve outcome into the weather-gated obstruction log.

        The pointing is taken from explicit ``az``/``alt`` when given, else derived
        from ``target`` (catalog + saved site) at *now*. Bad-weather failures are
        excluded from obstruction inference (see :func:`record_sky_result`). Reads
        the clock only for the record's timestamp. Writes the local sky-failure
        histogram; no device motion.
        """
        try:
            self.provenance.log_call(
                tool="log_sky_result",
                args={
                    "target": target,
                    "az": az,
                    "alt": alt,
                    "solved": solved,
                    "weather_go": weather_go,
                },
            )
            now = datetime.now(timezone.utc).isoformat()
            site = load_site(self._site_path())
            if site is None:
                return {"ok": False, "error": "no site profile set"}

            if az is None or alt is None:
                if not target:
                    return {
                        "ok": False,
                        "error": "need target+site or explicit az/alt",
                    }
                t = find_target(target)
                if t is None:
                    return {"ok": False, "error": f"unknown target: {target}"}
                az, alt = azalt_at(site, t, now)

            if weather_go is None:
                # Best-effort weather read so a bad-sky failure is not mislearnt as
                # an obstruction; an outage leaves weather_go None (counts as ok).
                try:
                    weather_go = (
                        await assess_conditions_weather(
                            site, dark_window(site, now), 0.0
                        )
                    ).go
                except Exception:  # noqa: BLE001 - weather outage is non-fatal
                    weather_go = None

            weather_ok = weather_go is not False
            record_sky_result(
                az,
                alt,
                ok=bool(solved),
                weather_ok=weather_ok,
                now_utc=now,
                lat=site.lat_deg,
                lon=site.lon_deg,
                path=self._sky_log_path(),
            )
            return {
                "ok": True,
                "az": round(az, 1),
                "alt": round(alt, 1),
                "solved": bool(solved),
                "weather_ok": weather_ok,
            }
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    async def suggest_horizon_mask(self) -> dict:
        """Suggest horizon-mask arcs learned from cross-night, weather-gated fails.

        READ-ONLY: inference only — it never edits the saved mask (use
        ``add_horizon_mask`` to accept a suggestion). Location-scoped to the saved
        site so obstructions learned elsewhere never surface here.
        """
        try:
            self.provenance.log_call(tool="suggest_horizon_mask", args={})
            site = load_site(self._site_path())
            if site is None:
                return {"ok": False, "error": "no site profile set"}
            cands = suggest_obstructions(
                self._sky_log_path(),
                cur_lat=site.lat_deg,
                cur_lon=site.lon_deg,
                location_tolerance_km=getattr(site, "location_tolerance_km", 1.0),
            )
            return {
                "ok": True,
                "candidates": [dataclasses.asdict(c) for c in cands],
                "count": len(cands),
            }
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    async def add_horizon_mask(
        self, az_min: float, az_max: float, alt_min: float
    ) -> dict:
        """Append one horizon-mask arc to the saved site profile (user confirm step).

        SIDE EFFECT: persists an added ``(az_min, az_max, alt_min)`` arc to the
        site profile — the ONLY path that edits the mask, always by explicit user
        action (suggestions never auto-apply).
        """
        try:
            self.provenance.log_call(
                tool="add_horizon_mask",
                args={"az_min": az_min, "az_max": az_max, "alt_min": alt_min},
            )
            site = load_site(self._site_path())
            if site is None:
                return {"ok": False, "error": "no site profile set"}
            site.horizon_mask.append((float(az_min), float(az_max), float(alt_min)))
            save_site(site, self._site_path())
            return {"ok": True, "profile": dataclasses.asdict(site)}
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    # --- autonomous night -------------------------------------------------

    async def simulate_night(
        self,
        date: str | None = None,
        types: list[str] | None = None,
        limit: int | None = None,
    ) -> dict:
        """Dry-run tonight's autonomous plan as an ordered target schedule.

        Reads/computes only — issues NO device motion. Ranks tonight's targets
        via :meth:`plan_targets`, then greedily packs them into the dark window
        with the pure :func:`plan_night` sequencer. Reads the clock only to
        resolve "tonight" when ``date`` is omitted.
        """
        try:
            self.provenance.log_call(
                tool="simulate_night",
                args={"date": date, "types": types, "limit": limit},
            )
            when = date or datetime.now(timezone.utc).isoformat()
            site = load_site(self._site_path())
            if site is None:
                return {"ok": False, "error": "no site profile set"}
            plan = await self.plan_targets(date=when, types=types, limit=limit)
            if not plan.get("ok"):
                return plan
            dark = dark_window(site, when)
            sched = plan_night(plan["targets"], dark)
            return {
                "ok": True,
                "conditions": plan.get("conditions"),
                # Re-surface the GPS/location reconcile computed by plan_targets so
                # a stale-mask disclosure rides along on the dry-run schedule too.
                "location": plan.get("location"),
                "dark_window_utc": dark,
                "schedule": [dataclasses.asdict(s) for s in sched],
                "projected_targets": len(sched),
            }
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    async def check_night_guardrails(
        self,
        session_start_utc: str,
        max_session_hours: float = 10.0,
        battery_floor_pct: float = 20.0,
        dawn_margin_min: float = 15.0,
    ) -> dict:
        """Evaluate the hard-stop safety conditions for one autonomous iteration.

        Gathers live device health and weather best-effort, then hands them to
        the pure :func:`evaluate_guardrails`. If device health cannot be
        confirmed (no ``get_device_state``), it fails SAFE — ``connected=False``
        forces a ``park_and_stop`` verdict. Reads the clock only for "now".
        """
        try:
            self.provenance.log_call(
                tool="check_night_guardrails",
                args={
                    "session_start_utc": session_start_utc,
                    "max_session_hours": max_session_hours,
                    "battery_floor_pct": battery_floor_pct,
                    "dawn_margin_min": dawn_margin_min,
                },
            )
            now = datetime.now(timezone.utc).isoformat()
            site = load_site(self._site_path())
            if site is None:
                return {"ok": False, "error": "no site profile set"}
            dark = dark_window(site, now)

            # Live device health — fail SAFE to (disconnected, unverified,
            # unknown) on ANY failure so a lost link parks the run.
            try:
                dev = await self.alpaca.method_sync("get_device_state")
                connected, verified, battery = _parse_device_health(dev)
            except Exception:  # noqa: BLE001 - any device fault → fail safe
                connected, verified, battery = (False, False, None)

            # Weather is best-effort and non-fatal: an outage → unknown, which
            # the guardrail core treats as observability-only, not a hard stop.
            try:
                weather_go = (await assess_conditions_weather(site, dark, 0.0)).go
            except Exception:  # noqa: BLE001 - weather outage is non-fatal
                weather_go = None

            verdict = evaluate_guardrails(
                now_utc=now,
                dark_window_utc=dark,
                session_start_utc=session_start_utc,
                battery_pct=battery,
                weather_go=weather_go,
                connected=connected,
                verified=verified,
                max_session_hours=max_session_hours,
                battery_floor_pct=battery_floor_pct,
                dawn_margin_min=dawn_margin_min,
            )
            return {"ok": True, **dataclasses.asdict(verdict)}
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    # --- projects ---------------------------------------------------------

    async def list_projects(self) -> dict:
        """Return every persisted project (goals + accumulated integration).

        Read-only. Degrades to an empty list when no store exists yet.
        """
        try:
            self.provenance.log_call(tool="list_projects", args={})
            projects = load_projects(self._projects_path())
            return {
                "ok": True,
                "projects": [dataclasses.asdict(p) for p in projects.values()],
                "count": len(projects),
            }
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    async def get_project(self, target: str) -> dict:
        """Return one project's goal, progress and session history by target id.

        Read-only. Returns ``ok:false`` (not an error) when no project exists.
        """
        try:
            self.provenance.log_call(tool="get_project", args={"target": target})
            proj = _get_project(target, path=self._projects_path())
            if proj is None:
                return {"ok": False, "error": f"no project for {target}"}
            return {"ok": True, "project": dataclasses.asdict(proj)}
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    async def set_project_goal(self, target: str, goal_minutes: float) -> dict:
        """Create/update a target's integration goal (minutes) for the planner.

        Persists to the local projects store. Resolves a display name from the
        catalog when possible. Does not touch accumulated integration or history.
        """
        try:
            self.provenance.log_call(
                tool="set_project_goal",
                args={"target": target, "goal_minutes": goal_minutes},
            )
            now = datetime.now(timezone.utc).isoformat()
            t = find_target(target)
            name = t.name if t is not None else target
            proj = upsert_project(
                target,
                name,
                goal_minutes=goal_minutes,
                now_utc=now,
                path=self._projects_path(),
            )
            return {"ok": True, "project": dataclasses.asdict(proj)}
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    async def log_session_result(
        self,
        target: str,
        integration_minutes: float,
        subs_total: int,
        subs_kept: int,
        median_fwhm: float | None = None,
        notes: str = "",
    ) -> dict:
        """Record a finished imaging session into a target's project history.

        Appends a session, accumulates kept integration toward the goal, and
        auto-completes the project when the goal is met. Creates the project if
        it does not yet exist.
        """
        try:
            self.provenance.log_call(
                tool="log_session_result",
                args={
                    "target": target,
                    "integration_minutes": integration_minutes,
                    "subs_total": subs_total,
                    "subs_kept": subs_kept,
                    "median_fwhm": median_fwhm,
                    "notes": notes,
                },
            )
            now = datetime.now(timezone.utc).isoformat()
            t = find_target(target)
            name = t.name if t is not None else target
            proj = _log_session_result(
                target,
                name,
                integration_minutes=integration_minutes,
                subs_total=subs_total,
                subs_kept=subs_kept,
                median_fwhm=median_fwhm,
                notes=notes,
                now_utc=now,
                path=self._projects_path(),
            )
            return {"ok": True, "project": dataclasses.asdict(proj)}
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

    async def recommend_projects(self, limit: int | None = None) -> dict:
        """Active projects still needing data, most-needed first.

        Read-only. Answers "what should I image more of?" for the planner.
        """
        try:
            self.provenance.log_call(
                tool="recommend_projects", args={"limit": limit}
            )
            projects = _recommend_projects(path=self._projects_path(), limit=limit)
            return {
                "ok": True,
                "projects": [dataclasses.asdict(p) for p in projects],
                "count": len(projects),
            }
        except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
            return {"ok": False, "error": str(exc)}

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


def _parse_gps(dev: Any) -> tuple[float, float] | None:
    """Extract the scope's ``(lat, lon)`` from a ``get_device_state`` response.

    HARDWARE-VALIDATED (Seestar S50, firmware 7.75): the GPS lives at
    ``result.location_lon_lat`` as a ``[lon, lat]`` pair (longitude FIRST). That
    validated shape is tried first. For resilience across firmware, the older
    guessed shapes are kept as fallbacks: ``setting.lat``/``lon``,
    ``location.lat``/``lon``, or top-level ``lat``/``lon``. Any malformed/empty
    input or a missing/non-numeric pair returns ``None`` (GPS unknown → the
    caller fails safe by assuming the saved site).
    """
    if not isinstance(dev, dict) or not dev:
        return None
    root = dev.get("result") if isinstance(dev.get("result"), dict) else dev
    if not isinstance(root, dict):
        return None

    def _num(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    # Validated shape (fw 7.75): [lon, lat].
    pair = root.get("location_lon_lat")
    if isinstance(pair, (list, tuple)) and len(pair) == 2 and _num(pair[0]) and _num(pair[1]):
        return (float(pair[1]), float(pair[0]))

    for src in (root.get("setting"), root.get("location"), root):
        if not isinstance(src, dict):
            continue
        lat, lon = src.get("lat"), src.get("lon")
        if _num(lat) and _num(lon):
            return (float(lat), float(lon))
    return None


def _parse_device_health(dev: Any) -> tuple[bool, bool, float | None]:
    """Extract ``(connected, verified, battery_pct)`` from ``get_device_state``.

    # FIRMWARE-DEPENDENT: the ``get_device_state`` schema is unconfirmed against
    hardware — the identity-verification flag and the battery-level key are best
    guesses isolated to this single helper (update here when the real shape is
    known). ``connected`` is True only when we got a usable dict back; a battery
    field is searched across the plausible key names below. Any malformed/empty
    input fails SAFE to ``(False, False, None)`` so a lost link parks the run.
    """
    if not isinstance(dev, dict) or not dev:
        return (False, False, None)
    verified = bool(dev.get("is_verified", dev.get("verified", False)))
    battery: float | None = None
    # FIRMWARE-DEPENDENT: real battery key unconfirmed — try the likely names.
    for key in ("battery_capacity", "battery", "bat_capacity"):
        val = dev.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            battery = float(val)
            break
    return (True, verified, battery)


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


@mcp.tool()
async def get_site_profile() -> dict:
    """Return the saved observing-site profile (position, Bortle, horizon mask).

    Read-only. Returns ``ok:false`` if no profile has been set yet.
    """
    return await get_controller().get_site_profile()


@mcp.tool()
async def set_site_profile(
    name: str,
    lat: float,
    lon: float,
    elevation_m: float = 0.0,
    bortle: int | None = None,
    sqm: float | None = None,
    horizon_mask: list[list[float]] | None = None,
    min_altitude_deg: float = 20.0,
    field_rotation_ceiling_deg: float = 60.0,
) -> dict:
    """Save the observing-site profile every planning tool reads.

    SIDE EFFECT: writes a JSON profile to local storage. Captures position,
    sky darkness (Bortle/SQM), a horizon mask ``[[az_min, az_max, alt_min], ...]``
    and the usable-altitude sweet band. No device motion.
    """
    return await get_controller().set_site_profile(
        name,
        lat,
        lon,
        elevation_m,
        bortle,
        sqm,
        horizon_mask,
        min_altitude_deg,
        field_rotation_ceiling_deg,
    )


@mcp.tool()
async def assess_conditions(date: str | None = None) -> dict:
    """Go/no-go sky verdict for tonight from weather + moon over the dark window.

    Read-only. Only external call is one HTTPS GET to Open-Meteo; a weather
    outage is non-fatal (``go=null`` — assess the sky manually). ``date`` (ISO
    UTC) overrides "tonight". Every verdict is reason-tagged.
    """
    return await get_controller().assess_conditions(date)


@mcp.tool()
async def get_target_observability(target: str, date: str | None = None) -> dict:
    """Observability of one named DSO tonight: altitude, sweet-band time, moon.

    Read-only, offline (deterministic astropy ephemeris). ``target`` is a
    catalog id or common name (e.g. ``"M27"`` / ``"Dumbbell Nebula"``); ``date``
    (ISO UTC) overrides "tonight".
    """
    return await get_controller().get_target_observability(target, date)


@mcp.tool()
async def plan_targets(
    date: str | None = None,
    types: list[str] | None = None,
    min_alt: float | None = None,
    limit: int = 10,
    avoid_recent_days: int = 2,
    prefer_projects: bool = True,
) -> dict:
    """Rank tonight's best DSO targets for the Seestar given site, sky conditions,
    moon, light pollution, and alt-az field rotation. Returns a scored, reasoned
    shortlist.

    Read-only. Optionally filter by ``types`` and ``min_alt`` and cap the count
    with ``limit``. ``date`` (ISO UTC) overrides "tonight". When
    ``prefer_projects`` (default) the projects/history store boosts targets that
    still need data and suppresses ones imaged within ``avoid_recent_days``.
    """
    return await get_controller().plan_targets(
        date, types, min_alt, limit, avoid_recent_days, prefer_projects
    )


@mcp.tool()
async def list_projects() -> dict:
    """List all imaging projects with their goals and accumulated integration.

    Read-only. Empty when no projects have been created yet.
    """
    return await get_controller().list_projects()


@mcp.tool()
async def get_project(target: str) -> dict:
    """Return one project's goal, collected integration, and session history.

    Read-only. ``target`` is a catalog id (e.g. ``"M31"``). Returns ``ok:false``
    if no project exists for it yet.
    """
    return await get_controller().get_project(target)


@mcp.tool()
async def set_project_goal(target: str, goal_minutes: float) -> dict:
    """Set a target's total integration goal (minutes) for the observing planner.

    SIDE EFFECT: writes to the local projects store. Use ``goal_minutes=0`` for
    an open-ended project. Does not change already-collected integration.
    """
    return await get_controller().set_project_goal(target, goal_minutes)


@mcp.tool()
async def log_session_result(
    target: str,
    integration_minutes: float,
    subs_total: int,
    subs_kept: int,
    median_fwhm: float | None = None,
    notes: str = "",
) -> dict:
    """Record a finished imaging session for a target (integration + kept/total
    subs) into its project history; call at wind-down.

    SIDE EFFECT: appends a session and accumulates integration toward the goal in
    the local projects store (auto-completing the project when the goal is met).
    """
    return await get_controller().log_session_result(
        target, integration_minutes, subs_total, subs_kept, median_fwhm, notes
    )


@mcp.tool()
async def recommend_projects(limit: int | None = None) -> dict:
    """Recommend active projects that still need data, most-needed first.

    Read-only. Answers "what should I image more of tonight?". ``limit`` caps the
    count.
    """
    return await get_controller().recommend_projects(limit)


@mcp.tool()
async def simulate_night(
    date: str | None = None,
    types: list[str] | None = None,
    limit: int | None = None,
) -> dict:
    """Dry-run tonight's autonomous plan as an ordered target schedule WITHOUT
    moving the telescope. Use this to preview/confirm an autonomous night before
    it starts.

    Read-only/compute-only: ranks tonight's targets and packs them into the dark
    window, issuing zero device motion. ``date`` (ISO UTC) overrides "tonight".
    """
    return await get_controller().simulate_night(date, types, limit)


@mcp.tool()
async def check_night_guardrails(
    session_start_utc: str,
    max_session_hours: float = 10.0,
    battery_floor_pct: float = 20.0,
    dawn_margin_min: float = 15.0,
) -> dict:
    """Evaluate hard-stop conditions for an unattended run (approaching dawn, low
    battery, weather no-go, lost connection, max session duration). Returns
    whether to continue or park-and-stop.

    Read-only: gathers live device health + weather and returns a verdict; fails
    SAFE to ``park_and_stop`` if the scope's health cannot be confirmed.
    """
    return await get_controller().check_night_guardrails(
        session_start_utc, max_session_hours, battery_floor_pct, dawn_margin_min
    )


@mcp.tool()
async def log_sky_result(
    target: str | None = None,
    az: float | None = None,
    alt: float | None = None,
    solved: bool = True,
    weather_go: bool | None = None,
) -> dict:
    """Log one plate-solve outcome so the learner can infer fixed obstructions.

    SIDE EFFECT: appends to the local weather-gated sky-failure histogram (no
    device motion). Pass explicit ``az``/``alt`` or a ``target`` (resolved against
    the saved site at now). ``solved=False`` records a failure; bad-weather
    failures (``weather_go=False``) are excluded from obstruction inference.
    """
    return await get_controller().log_sky_result(target, az, alt, solved, weather_go)


@mcp.tool()
async def suggest_horizon_mask() -> dict:
    """Suggest horizon-mask arcs learned from cross-night, weather-gated failures.

    READ-ONLY: returns candidate arcs with their evidence but NEVER edits the
    saved mask. Location-scoped to the saved site. Confirm a suggestion by calling
    ``add_horizon_mask`` explicitly.
    """
    return await get_controller().suggest_horizon_mask()


@mcp.tool()
async def add_horizon_mask(az_min: float, az_max: float, alt_min: float) -> dict:
    """Append one horizon-mask arc to the saved site profile (user confirm step).

    SIDE EFFECT: persists the ``(az_min, az_max, alt_min)`` arc to the site
    profile. This is the ONLY way the mask changes — suggestions never auto-apply.
    """
    return await get_controller().add_horizon_mask(az_min, az_max, alt_min)


def main() -> None:
    """Run the MCP server over stdio (no inbound network port is opened)."""
    mcp.run()


if __name__ == "__main__":
    main()

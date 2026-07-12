# Live-session fixes — 2026-07-12 autonomous night

A full unattended autonomous-night run surfaced three firmware-integration bugs
(all previously flagged `# FIRMWARE-DEPENDENT` and guessed wrong), plus a
scheduler limitation. Each was diagnosed against real hardware and fixed. This
doc records the symptom → root cause → fix → verification for each, and the
operational lessons folded into the skills.

Fixed on branch `fix/live-session-bugs-2026-07-12`; tests: 55 pass, ruff clean.

## The scope
- Hardware: ZWO Seestar S50, firmware 7.75, alt-az, Bortle 8 (<city>).
- Driven via `seestar-mcp` MCP tools over the `seestar_alp` Alpaca bridge
  (`method_sync` tunnel, device number 1).

## Bug 1 — goto RA units (degrees vs hours) · the big one

**Symptom.** Every `goto_target` returned `code 0` / `ok:true`, but the mount
never slewed. `get_view_state` showed the target name updating while the pointing
stayed put; the stage fell to `ContinuousExposure` with 0 frames. Looked like an
alignment/park problem for hours; it wasn't.

**Root cause.** The firmware's `iscope_start_view` `target_ra_dec` expects **RA in
hours** and **Dec in degrees**. The tool passed RA in **degrees** straight through.
An RA like `202.47` is out of range as "hours", so the goto silently no-ops.
Confirmed by sending M51 as RA `13.498` h (= 202.47°/15): the mount slewed, solved,
and stacked immediately.

**Fix.** `SeestarController.goto_target` now converts `target_ra_dec = [ra/15, dec]`.
`goto_target` takes catalog **J2000 degrees**; callers must not pre-convert.
(`src/seestar_mcp/server.py`)

## Bug 2 — dew heater method

**Symptom.** `set_dew_heater(True)` returned `unexpected param` (code 109); the
heater never engaged.

**Root cause.** The dew heater is a **power-output channel**, not a `set_setting`
key. Both `set_setting {"heater":true}` and `{"heater_enable":true}` are rejected.

**Fix.** Use `pi_output_set2 {"heater": {"state": <bool>, "value": <pct>}}`
(90% on, 0 off). Confirmed live: `heater_enable` in `get_device_state` flips both
directions. (`src/seestar_mcp/server.py`)

## Bug 3 — guardrail health parse (false park-and-stop)

**Symptom.** `check_night_guardrails` returned `park_and_stop` with "Scope
identity unverified" + "battery unknown" even though the scope was verified and at
100 %. The fail-safe fired on healthy hardware, blocking the autonomous loop.

**Root cause.** `_parse_device_health` read `is_verified` and `battery_capacity`
at the **top level** of `get_device_state`. In reality `is_verified` is nested at
`result.device.is_verified`, and **battery is not in `get_device_state` at all** —
it lives in `pi_get_info` at `result.battery_capacity`.

**Fix.** `_parse_device_health` now reads the nested `result.device.is_verified`
(with a flat fallback for mocks) and returns `(connected, verified)`; a new
`_parse_battery` reads `pi_get_info`; `check_night_guardrails` makes both calls.
(`src/seestar_mcp/server.py`)

## Bug 4 — planner packs a single target

**Symptom.** `simulate_night` scheduled ONE target for the entire dark window
(e.g. an open cluster for 3.5 h) instead of a multi-target rotation.

**Root cause.** `plan_night` is greedy: a target whose sweet-band spans the night
consumes the whole window and the cursor reaches the end, leaving no room.

**Fix.** Added `max_slot_min` to cap each slot so the night rotates through the
ranked list; `simulate_night` passes a 45-min cap.
(`src/seestar_mcp/planning/autonomous.py`)

## Operational lessons (skills: `run-session`, `autonomous-night`)

These cost real time and are now written into the run-books:

- **A goto returning ok ≠ the mount slewed.** Verify: `stage` must reach `Stack`
  and pointing must approach the target.
- **Obstructed targets hang in `AutoGoto`/`Initialise` with 0 frames** (low, behind
  a roof/tree). Skip them; prefer high, clear targets.
- **`park` strands the pointing model** — the OTA points at its cradle and can't
  plate-solve, so later gotos silently fail. **Park only at wind-down.** To
  pause/resume mid-night use `stop_view`. Recovery from a mid-session park needs a
  power-cycle; the first goto then runs a 3-point `Initialise` alignment (a few
  minutes) that also fixes framing.
- **Confirm framing with a real image, not telemetry.** Pull a sub JPG off the SMB
  share early each target (`<Target>_sub/Light_*_<IRCUT|LP>_*.jpg`; `_LP_` confirms
  the dual-band filter). Frame counts don't prove the object is in frame; an
  edge-cut means a bad alignment → power-cycle.
- **Summer / high-latitude twilight:** astronomical dark can be short, with no true
  darkness before sunrise. Put broadband targets in the real dark and LP/dual-band
  nebulae into twilight; expect the drop rate to climb toward civil dawn.

## Night outcome
Once RA-in-hours and a power-cycle re-alignment were in place, the run collected,
image-verified and logged: Bubble (NGC7635, 204 subs / 34 min), Wizard (NGC7380,
169 / 28 min), M101 (132 / 22 min), M76 (82 / 14 min) — parking cleanly at civil
dawn.

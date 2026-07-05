---
name: run-session
description: >
  Run-book for executing a Seestar S50 imaging session via the seestar-mcp tools.
  Use whenever the user wants to start, monitor, or wind down an imaging session —
  e.g. "image the Veil Nebula tonight", "start a session on M31", "slew to NGC 7000
  and start stacking", "wrap up the session and pull the subs". Covers pre-flight
  checks, target acquisition, autofocus, stacking, in-session monitoring cadence,
  and clean shutdown. Defers QA scoring decisions to the qa-policy skill and fault
  response to the anomaly-playbook skill.
---

# Seestar S50 Session Run-Book

This skill governs how to run an imaging session end to end using the `seestar-mcp`
tools. Follow the phases in order. Do not skip pre-flight. Treat every motion command
(goto, autofocus, park) as state-changing and confirm it succeeded before proceeding.

## Operating assumptions
- `seestar-mcp` is already registered and the Claude Code session is running on the
  Jetson. seestar_alp is up on :5555 and the Seestar is on a stable LAN IP.
- The user is typically monitoring from the Claude phone app via Remote Control, so
  keep status messages compact and scannable on a small screen. Lead with state, not
  prose. One-line status beats a paragraph.
- "Save each frame in enhancing" should be ON so Tier-2 QA has subs to score. If the
  user has not confirmed this, remind them once at session start; do not nag.

## Phase 0 — Pre-flight (always run before goto)
1. `get_status` — confirm the mount is connected and not already slewing. If not
   connected, `connect_telescope` first.
2. `get_view_state` — confirm no session is already in progress. If one is, ask the
   user whether to stop it (`stop_view`) before starting a new target.
3. Confirm thermal/dark readiness: the S50 builds darks at startup and they are
   temperature-linked. If the scope was just powered on or just moved indoors→outdoors,
   advise a 10–15 min acclimation before relying on stacked output. If the user plans
   to use the dew heater, note that enabling it after darks were built invalidates them
   — set it BEFORE the session and let darks rebuild, or accept re-enhancement later.
4. Note the mount mode. If Alt-Az (default), warn that field rotation will cause rising
   frame rejection after ~15 min and a spiral border on the stack — this is expected,
   not a fault. EQ mode (wedge + polar align) avoids this.

## Phase 1 — Acquire target
1. Resolve the target to RA/Dec if the user gave a name (use known catalog coordinates;
   if uncertain, say so and ask). Pass JNow coordinates.
2. `goto_target(name, ra, dec, use_lp_filter)` — choose the LP filter only for emission
   nebulae under light pollution; leave it off for galaxies, clusters, and broadband
   targets unless the user asks. State which choice you made and why in one line.
3. After goto, `plate_solve` to confirm pointing. If the solve fails, do not start
   stacking — hand off to the anomaly-playbook skill (pointing/transparency branch).

## Phase 2 — Focus
1. `run_autofocus`. Then `get_focuser_position` and record the value as the session
   focus baseline (it feeds drift detection in monitoring).
2. If autofocus fails or returns an implausible position, retry once; if it fails again,
   hand off to the anomaly-playbook skill (focus branch).

## Phase 3 — Stack
1. `start_stack`. Confirm via `get_view_state` that the stacking count begins
   incrementing within ~one sub-exposure interval.
2. Record session start time, target, filter, focus baseline, and mount mode to the
   session manifest (the provenance layer logs commands automatically; this is the
   human-facing summary).

## Phase 4 — Monitor (the core loop)
Poll `qa_tier1` on a cadence — every 60–120 s is reasonable; tighten to ~30 s in the
first few minutes and after any focus event. On each poll, report a compact status line:

  `[mm:ss] stacked N (+k) | rejected R | solve OK | focus Δ=±x`

Watch these signals and route faults to the anomaly-playbook skill rather than
diagnosing inline:
- Stacking count flat across two polls → clouds / tracking loss.
- Rejected count climbing fast → trailing (expected late in Alt-Az), dew, or wind.
- Focus drifting from baseline → temperature change; consider a mid-session refocus.
- Plate-solve dropping out → pointing / transparency.

Run `qa_tier2` opportunistically (e.g. once enough new subs exist, or when Tier-1
shows something marginal) to get real per-sub FWHM/eccentricity/SNR. Apply the
qa-policy skill to interpret the numbers and decide keep/marginal/reject. Do not
invent thresholds here — qa-policy owns them.

Only interrupt the user proactively for: a fault the anomaly-playbook says needs a
decision, a quality collapse, or a requested milestone (e.g. "ping me at 1 hour
integration"). Otherwise let the session run quietly.

## Phase 5 — Wind down
1. `stop_view("Stack")` to end stacking cleanly.
2. `download_subs(target, dest, since=session_start)` to pull the session's subs to the
   local data dir.
3. Run `qa_session_report(target)` to produce the JSON+Markdown report and the keep-list.
   Summarize for the user: total integration, kept vs rejected counts, median FWHM,
   the dominant rejection cause if any, and where the report and keep-list were written.
4. If the user is done for the night, `park` the mount (and `shutdown` only if they ask
   — shutdown ends the seestar_alp link).

## Hard rules
- Never start stacking on a failed plate-solve.
- Never claim data is "good" without Tier-2 numbers; Tier-1 telemetry is a health
  signal, not a quality verdict.
- Treat late-session Alt-Az rejection as expected; do not raise it as a fault.
- Confirm each motion command's success before issuing the next.

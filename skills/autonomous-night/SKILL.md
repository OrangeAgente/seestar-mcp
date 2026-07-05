---
name: autonomous-night
description: >
  Unattended full-night run-book for a Seestar S50: assess, run the ranked plan
  target-by-target, react to conditions/QA, and wind down + park at dawn — on its own,
  behind hard safety guardrails and a mandatory dry-run confirmation. Use whenever the
  user wants to hand over the whole night — e.g. "run the whole night", "image
  unattended", "run an autonomous session", "image all night on your own", "run my
  target list unattended", "let it run itself till dawn". Starts with a NO-MOTION
  `simulate_night` projection that the user must explicitly approve before the first
  motion command. Orchestrates the existing tools in a visible loop — it decides
  *whether to keep going and what's next*; it does not re-implement motion (that is
  `run-session`).
---

# Seestar S50 Autonomous Night

This skill runs a whole night unattended: propose a plan, get one explicit go-ahead,
then loop target-by-target under hard guardrails, and park at dawn or on any hard stop.
Autonomy here is **Claude driving the existing audited tools in a visible loop** — not a
hidden background engine. Every decision is logged and surfaced. When in doubt, **stop
and park (fail safe).**

## Operating assumptions
- The user is usually watching from the Claude phone app via Remote Control on a small
  screen. Every message is one line, lead with state, not prose. Notify on each target
  change and every stop — the phone is how they know what happened overnight.
- This skill owns *sequencing and safety*, not motion. Planning judgment lives in
  **`observing-planner`**; execution (goto → focus → stack → monitor) lives in
  **`run-session`**; faults live in **`anomaly-playbook`**; QA thresholds live in
  **`qa-policy`**. Do not re-implement any of those here.
- Provenance: the tools log their calls; you add a one-line human-readable note per
  guardrail decision and target switch.

## Phase A — Propose (NO MOTION, mandatory confirmation gate)
1. `simulate_night` (optionally pass `types` / `limit` if the user asked). This is a
   **dry run — it issues NO motion.** It returns the conditions verdict, the dark
   window, and the ordered projected schedule.
2. Present, compactly:
   - the **one-line conditions verdict** (from `simulate_night`'s `conditions`);
   - the **ordered schedule** — per `ScheduledTarget`, one line: name · window (UTC) ·
     minutes · `subs × 10s` · the one-line reason, e.g.
     `1. M27 · 22:40–00:10 UTC · 90 min · 540×10s · long sweet-band pass, suits site.`
   - the **guardrail defaults** that will apply: dawn margin (15 min), battery floor
     (20%), max session (10 h), weather no-go stops.
3. **State plainly that this is a dry run and REQUIRE explicit user confirmation before
   ANY motion command.** Say it in one line, e.g.
   `Dry run only — nothing has moved. Reply "go" to start the run; I'll park at dawn or on any hard stop.`
   This confirmation gate is **mandatory and non-skippable.** Do not slew, focus, stack,
   or otherwise command motion until the user explicitly says to begin.
4. If conditions are **no-go** (`simulate_night` returns `ok:false`, an empty schedule,
   or a no-go verdict), say so in one line and **do not start.** Offer to re-simulate
   later or for a clearing window, but issue no motion.

## Phase B — Loop (per target)
Record the run's `session_start_utc` at first go-ahead. Then, for each target:

1. **Guardrail check FIRST — every iteration, no exceptions.** Call
   `check_night_guardrails(session_start_utc=...)`. If it returns
   `action: "park_and_stop"`, go straight to **Phase C** and quote the hard-stop reason
   in one line (from `hard_stops` / `reasons`). **Never skip this check between
   targets.**
2. Otherwise take the **next `ScheduledTarget`** from the schedule and hand it to the
   **`run-session`** skill: goto → plate-solve → focus → stack → monitor (the `qa_tier1`
   cadence plus the Phase-2 live reactivity — conditions watch and sweet-band watch).
   Notify the user of the target change in one line.
3. **End the target's slot** when any of these happen: its scheduled window ends, it
   leaves its sweet band (nearing the field-rotation ceiling or the altitude floor), or
   QA collapses. Then call `log_session_result(...)` for it (integration, sub counts,
   median FWHM per the wind-down in `run-session`) and **re-enter the loop** at step 1
   for the next target.
4. **Faults → `anomaly-playbook`.** Route any mid-target fault (stall, solve/focus
   failure, tracking loss, connection drop, weather flip) there. If it resolves, resume
   the loop. If it is an **unrecoverable fault or a hard guardrail stop**, go to Phase C
   — end in `park`. Re-check guardrails on any anomaly, not just at slot boundaries.

## Phase C — Wind down + park
Reached on any hard stop, unrecoverable fault, end of schedule, or user stop.
1. `stop_view` to end stacking cleanly.
2. `log_session_result(...)` for the **in-progress** target so its integration is not
   lost.
3. **`park`** the mount (stops tracking, optics to horizontal). Parking is
   non-negotiable on any hard stop.
4. **Summarize the night** in a compact block and **notify the user**: targets imaged,
   integration on each, projects advanced, and the reason the run ended (dawn / battery
   / weather / connection / max duration / schedule complete / user stop).
5. Only `shutdown` if the user **pre-authorized** it (shutdown ends the seestar_alp
   link). Otherwise leave the scope parked and connected.

## Hard rules
- **The dry-run + explicit confirmation before motion is MANDATORY and non-skippable.**
  Nothing moves in Phase A. The first motion command only follows an explicit user
  go-ahead.
- **Five HARD stops, each of which ALWAYS ends in `park`:** astronomical **dawn**
  (within the margin), **low battery** (below floor), **precipitation / hard weather
  no-go**, **lost connection / unverified scope**, **max session duration** exceeded.
  Hard stops are non-negotiable.
- **Never skip `check_night_guardrails` between targets** — call it at the top of every
  loop iteration and on any anomaly.
- **Log every session** (`log_session_result`), including the in-progress target at
  wind-down, so integration accumulates across nights.
- **Keep the user notified** of each target change and every stop (Remote Control
  surfaces these on the phone).
- **Fail safe: when in doubt, stop and park.** If scope health can't be confirmed
  (`check_night_guardrails` can't read device state → treated as disconnected), the run
  stops and parks. Never leave the mount slewed or tracking on a fault.
- **This skill decides *whether to keep going and what's next*; it does not re-implement
  motion** (that is `run-session`). Planning = `observing-planner`, execution =
  `run-session`, faults = `anomaly-playbook`, QA = `qa-policy`.

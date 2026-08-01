# Leaner MCP, Continuity, and Device-Load Reduction — Design Spec

**Date:** 2026-07-31
**Project:** seestar-mcp
**Status:** Approved direction (from discussion) — ready for implementation planning
**Scope:** context cost of the MCP surface, stdio disconnects, run continuity, device-call
efficiency, and how the SeeStar Console dashboard and this server stay in sync.

---

## Goal

Cut what the MCP server costs an agent session, stop it dropping its connection, make a
run survivable across a server restart, and reduce redundant device traffic — **without
breaking the Console dashboard**, and without merging the two projects.

## Findings (all measured, not estimated)

| Finding | Measurement |
|---|---|
| Tool schemas loaded every session | 33 tools, **15,693 chars ≈ 3,900 tokens** |
| `list_projects()` / `recommend_projects()` | **10,586 chars ≈ 2,650 tokens per call**, and grows with every night logged |
| `get_project("M31")` (single, detailed) | 1,378 chars ≈ 344 tokens — proportionate |
| `seestar-refine` (post-processing) | 5 tools, 3,377 chars ≈ **844 tokens**, plus 4 deps used by nothing else |
| MCP disconnects | idle timeout — see below |
| Run state across a restart | **not persisted at all** (`session_id` is in-memory only) |
| `get_status` device amplification | 800 calls → **4,000** Alpaca property GETs (5×) |
| `check_night_guardrails` battery read | **610 redundant `pi_get_info` calls** |

### The disconnects are an idle timeout

Reconstructed from the provenance log, using the `client` field added the previous day.
Each distinct client id is one server-process lifetime:

- The dashboard's server: **24.8 calls/min sustained → lived 8.8 hours**, no drop.
- This agent's servers: **1.7, 2.4, 7.0, 11.3, 16.4 min** lifetimes, each preceded by long
  silence while background monitors ran (longest gap before a death: **62 minutes**).

The transport is not unstable. Connections die when they go quiet. A workflow that runs
long non-MCP background work is precisely what triggers it.

### Context cost is an agent-side problem only

The dashboard runs in its own process with its own context. Its 13,121 calls cost this
agent nothing. **Reducing agent context therefore requires no coordination with the
dashboard** — the two concerns are independent and this spec keeps them that way.

---

## Changes

### 1. Lean list payloads, opt-in detail

`list_projects` and `recommend_projects` return every project with its full `sessions[]`
history and notes. That is ~2,650 tokens per call today and grows without bound — a year of
imaging makes it 5–10× worse.

Both gain `detail: "summary" | "full"`, **defaulting to `"summary"`**: target id, name,
status, `collected_minutes`, `goal_minutes`, session **count**, and last session date — no
session bodies, no notes. `get_project(target)` is unchanged and remains the way to get one
project in full.

Impact on the dashboard: it called `list_projects` **once** in 13,121 calls. Effectively nil.

### 2. Drop post-processing from the MCP surface

`seestar-refine` (`stack_keep_list`, `stretch_master`, `prepare_pixinsight_handoff`,
`list_masters`, `check_backends`) is removed from the registered surface. It shares no code
with `seestar_mcp` (verified: zero imports), so this is a clean cut.

**Image *assessment* stays** — `qa_tier1`, `qa_tier2`, `qa_session_report` are unaffected.
Judging data quality is part of running a night; producing finished images is not.

`astroalign`, `astroscrappy`, `ccdproc` and `pillow` move to an optional dependency group so
a default install stops carrying them.

### 3. ~~Tighten tool descriptions~~ — RETRACTED 2026-08-02

**This item was wrong and is not being implemented.** It claimed "15,693 chars of schema has
real slack" and set a ~30% target. That was asserted without reading the descriptions.

Having read them, the slack is not there. The longest entries are, in order: `get_run_state`
(the tri-state semantics a consumer needs to avoid reading `unknown` as "the scope is free"),
`plan_targets`, `qa_tier2`, `log_sky_result` (the weather-gating rule that stops a caller
corrupting the obstruction learner), and `check_night_guardrails`. What they contain is
`SIDE EFFECT` labels — a safety contract — parameter formats a caller cannot guess
(`[[az_min, az_max, alt_min], ...]`), and semantics that prevent misuse.

The arithmetic does not favour cutting either: the whole surface is ~4,100 tokens **once per
session**, so a 30% cut saves ~1,200 tokens — less than a single `list_projects(detail="full")`
call at 2,646. Trading misuse-prevention for a one-off saving smaller than one call is a bad
deal.

Setting a percentage target before reading the text is what produced this item. Recorded
rather than deleted, because the reasoning is the useful part.

### 4. Keepalive against the idle timeout

The fix belongs in agent behavior, not server code: during long stretches of non-MCP work,
call a **local-only** tool (`get_site_profile` or `list_projects`) roughly every 5 minutes.
Both read local files and generate **zero device traffic**, so the link stays warm at no cost
to the control channel. Encoded in the `autonomous-night` and `run-session` skills alongside
the existing monitoring cadence.

### 5. Persist run state (`data/run_state.json`)

Today `session_id` lives only in memory, so a server restart loses all knowledge that a run
is underway — during the 2026-07-31 session the server died five times and no replacement
could have resumed, warned, or wound down.

A small JSON file, written with the existing `write_json_atomic`, records: active target,
`session_start_utc`, current slot end, remaining planned targets, and the park deadline. It
is written on target change and cleared at wind-down. Any new server can then answer "is a
run live, and what should happen next?" — making recovery structural instead of dependent on
one conversation's memory.

### 6. Two device-load reductions (help both consumers)

- **`get_status` fan-out.** It reads five Alpaca properties (`connected`, `rightascension`,
  `declination`, `tracking`, `slewing`) to assemble one dict. A single native
  `get_device_state` carries the same information. Saves ~3,200 of the dashboard's 8,464
  bridge requests, and the same ratio for any caller.
- **Redundant `pi_get_info`.** `check_night_guardrails` already calls `get_device_state` for
  connected/verified, then makes a second device round-trip for battery — which is present at
  `result.pi_status.battery_capacity` in the response it already has. Removing it saves 610
  calls per dashboard-day and one round-trip per guardrail check, which now runs every ~10
  minutes *inside* each slot.

Both must preserve the never-raise contract and existing return shapes.

### 7. A machine-readable contract instead of prose handbacks

Coordination with the dashboard currently happens by carrying markdown between sessions. That
mechanism is what costs effort — not the separation itself.

- Commit a generated **tool-contract artifact** (names, descriptions, input schemas, and the
  documented response keys) so the dashboard diffs a file rather than reading a work order.
- Add **contract tests** encoding the dashboard's expectations, so a breaking change fails
  *this* repo's build rather than their runtime.
- **Version** the contract so they can pin.

## Non-goals, with reasons

**Moving the dashboard onto the bridge directly.** Considered seriously: 86% of its calls are
device passthrough the bridge already serves. Rejected for now because it would trade a real
safety property for independence they have not asked for — their allowlist *structurally*
cannot issue motion (0 write calls in 13,121), whereas the bridge's `PUT /action` accepts any
native method including `scope_park` and `pi_shutdown` on the same endpoint. They would also
reimplement the normalization the tool layer provides (`get_status` fan-out, `_maybe`
NotImplemented handling, `_native_fail`, focus extraction) and lose provenance on reads. Note
also that this would **not** reduce device contention — the same requests reach the same
bridge either way. Revisit if stdio fragility starts costing them uptime.

**Merging the two projects.** The separation has already paid for itself: an outside consumer
tracing this source found three defects the author would not have (per-sub arrays discarded at
the boundary, a sort that was a no-op, 278 log records collapsed into one string). That value
comes from a different *reviewer*, which survives either arrangement — but merging would mix a
daily-iterating UI with a server whose posture is least-privilege and audited, blend an
astropy/photutils dependency tree with a web stack, and muddy a repo that has just been
published for the community. Fix the handoff mechanism (item 7) instead.

## Verification

- **Context:** re-measure tool-schema bytes and `list_projects` payload before/after; record
  both figures in the plan. Target: list payload under ~400 tokens at current data volume and
  flat as history grows.
- **Behavior:** `uv run pytest` green and `uv run ruff check src tests` clean. Regression tests
  pin that `detail="full"` reproduces today's payload exactly, per the repo's
  backward-compatibility rule.
- **Device load:** count bridge requests generated by one `get_status` and one
  `check_night_guardrails` before/after; expect 5→1 and 2→1.
- **Continuity:** kill the server mid-run and confirm a fresh one reads `run_state.json` and
  reports the live run.

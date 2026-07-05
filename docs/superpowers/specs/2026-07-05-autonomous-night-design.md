# Autonomous Night (Phase 3) — Design Spec

**Date:** 2026-07-05
**Project:** seestar-mcp
**Status:** Approved direction (from brainstorming) — ready for implementation planning
**Builds on:** Phase 1 (planner) + Phase 2 (live operator + projects).

---

## Goal

Let the user hand Claude a whole night: **assess → run the ranked plan target-by-target → react to conditions/QA → wind down and park at dawn**, unattended, behind **hard safety guardrails** and a **mandatory dry-run + confirmation** before any motion. The autonomy is *Claude driving the existing tools in a loop* (the project's "Claude Code as the brain" model over a Remote Control session) — not a hidden background engine — so every decision is visible and auditable.

## Safety model (the crux — conservative by design)

The user builds for high-assurance environments and this drives real hardware in the dark, so:

1. **Opt-in + dry-run first.** An autonomous night is never triggered casually. It starts with `simulate_night` (a no-motion projection of the whole night) presented to the user, and **explicit user go-ahead is required before the first motion command**. That is the one mandatory human checkpoint.
2. **Hard stops (each → wind down + `park`, then notify):** astronomical **dawn** (within a margin), **battery** below a floor, **precip / hard weather no-go**, **loss of connection / unverified scope**, **max session duration** exceeded. Hard stops are non-negotiable and evaluated every loop iteration via `check_night_guardrails`.
3. **Park-on-fault.** Any unrecoverable fault (per the anomaly-playbook) or guardrail stop ends with `stop_view` + `park`. Never leave the mount slewed/tracking on a fault.
4. **Provenance of decisions.** Every guardrail evaluation and target switch is logged (tool calls already log; the skill adds a one-line human-readable note per decision).
5. **Bounded autonomy.** Between targets the loop re-checks guardrails and the plan; it does not free-run. Motion tools remain the same audited tools with honest descriptions.

## Design principles (inherited)

MCP = access/compute, Skills = judgment. Deterministic pure cores (injected timestamps/inputs), never-raise tool paths, provenance, no secrets, no new deps. Reason-tagged verdicts.

---

## Architecture

New module `src/seestar_mcp/planning/autonomous.py` with **pure, testable** cores; 2 new MCP tools on `SeestarController` that gather live inputs and wrap the cores (→ 30 tools); a new **`autonomous-night`** skill.

### Pure cores (`autonomous.py`) — no I/O, fully unit-testable

```python
@dataclass
class ScheduledTarget:
    target_id: str
    target_name: str
    start_utc: str
    end_utc: str
    minutes: float
    recommended_subs: int
    reason: str            # why this target in this slot

@dataclass
class GuardrailVerdict:
    proceed: bool
    action: str            # "continue" | "switch" | "park_and_stop"
    reasons: list[str]     # every stop/continue reason, human-readable
    hard_stops: list[str]  # subset that are HARD stops (dawn/battery/precip/fault/max)
```

- `plan_night(scored_targets, dark_window_utc, *, min_slot_min=20.0, max_targets=None) -> list[ScheduledTarget]` — greedy sequencer over an already-ranked target list (each carries its best window + recommended_subs, e.g. the compact dicts from `plan_targets`): fill the dark window start→dawn, each target taking its sweet-band window (clamped to the remaining night and `min_slot_min`), non-overlapping, skipping targets whose window has passed. Pure function of its inputs — deterministic.
- `evaluate_guardrails(*, now_utc, dark_window_utc, session_start_utc, battery_pct, weather_go, connected, verified, max_session_hours=10.0, battery_floor_pct=20.0, dawn_margin_min=15.0, stop_on_weather_nogo=True) -> GuardrailVerdict` — pure. Emits `park_and_stop` (with the specific hard-stop reason) when: `now` within `dawn_margin_min` of dawn; elapsed since `session_start` ≥ `max_session_hours`; `battery_pct < battery_floor_pct`; `not connected` or `not verified`; `weather_go is False and stop_on_weather_nogo`. Otherwise `continue`. Missing inputs (e.g. `battery_pct=None`) are treated conservatively (noted, not a crash). Never raises.

### New MCP tools (on `SeestarController`)

| Tool | Purpose |
|---|---|
| `simulate_night(date?, types?, limit?)` | **Dry run.** Build the projected night sequence via `plan_targets` (Phase 1/2, project-aware) → `plan_night`. Returns the ordered `ScheduledTarget` list + the conditions verdict + total projected integration. **Issues NO motion.** This is what the user approves before an autonomous run. |
| `check_night_guardrails(session_start_utc, max_session_hours?, battery_floor_pct?, dawn_margin_min?)` | Gather live inputs — dark window (site), scope `connected`/verified + `battery_pct` from `get_device_state`, and `assess_conditions.go` — then call `evaluate_guardrails`. Returns the `GuardrailVerdict`. Provenance-logged. |

Both are async controller methods returning `{"ok": ...}`; catch → `{"ok": False, "error": ...}`; never raise. `battery_pct` parsing from `get_device_state` is `# FIRMWARE-DEPENDENT` (isolated to one helper). Tool count 28 → 30.

### New skill: `autonomous-night`

`skills/autonomous-night/SKILL.md` — the unattended run-book. Flow:
- **Phase A — Propose (no motion):** run `simulate_night`; present the projected plan (targets, windows, integration, and the guardrail defaults that will apply). **Require explicit user confirmation to begin.** If conditions are no-go, say so and do not start.
- **Phase B — Loop (per target):** `check_night_guardrails` → if `park_and_stop`, go to Phase C. Else take the next `ScheduledTarget` and hand it to `run-session` (goto → focus → stack), monitoring via `qa_tier1` + the Phase-2 live-reactivity. When the target's slot ends (or it leaves its sweet band, or QA collapses), `log_session_result`, then re-enter the loop for the next target. Re-check guardrails between every target AND on any anomaly (defer faults to `anomaly-playbook`).
- **Phase C — Wind down + park:** `stop_view` → `log_session_result` for the in-progress target → `park`. Summarize the night (targets imaged, integration each, projects advanced) and notify. `shutdown` only if the user pre-authorized it.
- **Hard rules:** the dry-run + confirmation before motion is MANDATORY; dawn/battery/precip/fault/max-duration are HARD stops that always park; never skip `check_night_guardrails` between targets; log every session; keep the user notified of each target change and any stop (Remote Control surfaces these on the phone).

## Data flow

```
autonomous-night
  Phase A: simulate_night → plan_targets(project-aware) → plan_night → projected sequence → USER CONFIRMS
  Phase B: loop { check_night_guardrails → (proceed) → run-session(target) → log_session_result }
  Phase C: on any hard stop → stop_view + park + summary
```

## Error handling

- Cores never raise (bad inputs → conservative verdict / skip). Tool methods catch → `{"ok": False}`.
- `get_device_state` unavailable → `check_night_guardrails` treats battery as unknown and connection as false → conservative `park_and_stop` (fail safe: if we can't confirm the scope is healthy, we stop).
- No site profile → `simulate_night`/guardrails return `{"ok": False, "error": "no site profile"}`.
- Weather unknown (offline) → not a hard stop by itself (observability-only night), but noted; the skill tells the user weather is unverified.

## Testing

- `plan_night`: a ranked list over a fixed dark window produces a non-overlapping ordered schedule; targets whose window passed are skipped; `min_slot_min` respected; empty input → `[]`.
- `evaluate_guardrails`: each hard stop fires in isolation (dawn margin, max hours, low battery, disconnected, unverified, weather no-go) → `park_and_stop` with the right hard-stop reason; a healthy mid-night state → `continue`; `None` battery → conservative note, no crash.
- tools: 30 registered; `simulate_night` returns an ordered schedule + issues NO alpaca motion calls (assert via a mock alpaca that records calls — only read-only/none); `check_night_guardrails` with a mocked `get_device_state`/weather returns the expected verdict; no-site → `ok:false`; disconnected device → `park_and_stop`.
- `autonomous-night/SKILL.md` frontmatter valid; contains the mandatory-dry-run-and-confirm rule and the hard-stop list.
- No network/hardware in tests (weather + device mocked).

## Security / reproducibility

- No new network host, no new deps, no secrets. Pure cores deterministic (injected `now_utc`/inputs). The two tools gather live state read-only except that the *skill* issues motion via existing audited tools — after explicit user confirmation. Provenance logs guardrail checks and the simulate/schedule. Battery parsing is the only `# FIRMWARE-DEPENDENT` bit, isolated to one helper (validate against real `get_device_state` on hardware).

## Completion

With Phase 3, the three-phase "professional astrophotographer" is complete: **plan (P1) → operate with memory + reactivity (P2) → run a full night unattended within guardrails (P3)**, all through the same auditable MCP service and skills.

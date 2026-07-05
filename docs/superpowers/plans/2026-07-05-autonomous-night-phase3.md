# Autonomous Night (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add unattended full-night operation to seestar-mcp — a dry-run night simulator, hard-guardrail checks, and an `autonomous-night` skill — behind a mandatory confirmation + fail-safe stops.

**Architecture:** New `src/seestar_mcp/planning/autonomous.py` with two PURE cores (`plan_night`, `evaluate_guardrails`); 2 new MCP tools on `SeestarController` (→ 30 total) that gather live device/weather state and wrap the cores; a new `autonomous-night` skill.

**Tech Stack:** Python 3.12, dataclasses, `pytest`. No new deps. `uv` toolchain.

## Global Constraints

- `uv run pytest`, `uv run ruff check src tests`. NEVER bare `python`.
- Pure cores take injected timestamps/inputs — no clock/no I/O — so they are deterministic and unit-testable. Tool methods supply live state.
- Fail-safe: if the scope's health can't be confirmed (no `get_device_state`), guardrails return `park_and_stop`. Never-raise on tool paths → `{"ok": False, "error": ...}`.
- No motion from `simulate_night` (dry run). No new deps, no new network host, no secrets. Provenance-log tool calls.
- `# FIRMWARE-DEPENDENT`: battery parsing from `get_device_state` — isolate to one helper.
- Spec of record: `docs/superpowers/specs/2026-07-05-autonomous-night-design.md` (read it for exact dataclasses + guardrail rules).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`; `git -c core.autocrlf=false commit`.

---

### Task 1: Pure cores (`autonomous.py`)

**Files:** Create `src/seestar_mcp/planning/autonomous.py`; Test `tests/test_planning_autonomous.py`.

**Interfaces — Produces:** `ScheduledTarget`, `GuardrailVerdict` dataclasses (exact fields per spec); `plan_night(scored_targets, dark_window_utc, *, min_slot_min=20.0, max_targets=None) -> list[ScheduledTarget]`; `evaluate_guardrails(*, now_utc, dark_window_utc, session_start_utc, battery_pct, weather_go, connected, verified, max_session_hours=10.0, battery_floor_pct=20.0, dawn_margin_min=15.0, stop_on_weather_nogo=True) -> GuardrailVerdict`.

`scored_targets` is a list of dicts shaped like `plan_targets`' compact output: each has `id`, `name`, `best_window_utc` (`[start_iso, end_iso]` or None), `recommended_subs`. ISO times are UTC; parse with `datetime.fromisoformat` (replace trailing `Z` with `+00:00`).

- [ ] **Step 1: failing tests**
```python
# tests/test_planning_autonomous.py
from seestar_mcp.planning.autonomous import plan_night, evaluate_guardrails

DW = ("2026-07-05T02:00:00Z", "2026-07-05T08:00:00Z")

def _t(id, s, e, subs=300):
    return {"id": id, "name": id, "best_window_utc": [s, e], "recommended_subs": subs}

def test_plan_night_orders_and_no_overlap():
    targets = [
        _t("A", "2026-07-05T02:30:00Z", "2026-07-05T04:00:00Z"),
        _t("B", "2026-07-05T04:30:00Z", "2026-07-05T06:00:00Z"),
        _t("C", "2026-07-05T01:00:00Z", "2026-07-05T01:30:00Z"),  # window before dark start
    ]
    sched = plan_night(targets, DW)
    ids = [s.target_id for s in sched]
    assert ids == ["A", "B"]                # C skipped (window passed before dark)
    assert sched[0].end_utc <= sched[1].start_utc  # non-overlapping, ordered

def test_plan_night_min_slot_and_empty():
    assert plan_night([], DW) == []
    tiny = [_t("A", "2026-07-05T02:00:00Z", "2026-07-05T02:05:00Z")]  # 5 min < min_slot 20
    assert plan_night(tiny, DW, min_slot_min=20.0) == []  # dropped: slot too short

def test_guardrails_healthy_continues():
    v = evaluate_guardrails(now_utc="2026-07-05T04:00:00Z", dark_window_utc=DW,
        session_start_utc="2026-07-05T02:30:00Z", battery_pct=80, weather_go=True,
        connected=True, verified=True)
    assert v.proceed is True and v.action == "continue" and v.hard_stops == []

def test_guardrails_each_hard_stop():
    base = dict(dark_window_utc=DW, session_start_utc="2026-07-05T02:30:00Z",
                battery_pct=80, weather_go=True, connected=True, verified=True)
    # dawn margin (now within 15 min of 08:00)
    assert evaluate_guardrails(now_utc="2026-07-05T07:50:00Z", **base).action == "park_and_stop"
    # low battery
    assert evaluate_guardrails(now_utc="2026-07-05T04:00:00Z", **{**base, "battery_pct":10}).action == "park_and_stop"
    # disconnected
    assert evaluate_guardrails(now_utc="2026-07-05T04:00:00Z", **{**base, "connected":False}).action == "park_and_stop"
    # weather no-go
    assert evaluate_guardrails(now_utc="2026-07-05T04:00:00Z", **{**base, "weather_go":False}).action == "park_and_stop"
    # unknown battery is conservative but not a crash
    v = evaluate_guardrails(now_utc="2026-07-05T04:00:00Z", **{**base, "battery_pct":None})
    assert isinstance(v.proceed, bool)
```
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** per spec. `plan_night`: parse dark window; iterate targets sorted by window start; for each with a window overlapping `[max(prev_end, dark_start), dark_end]`, allocate `start=max(window_start, cursor)`, `end=min(window_end, dark_end)`; keep if `(end-start) >= min_slot_min`; advance cursor; skip targets whose window ends before the cursor/dark start; respect `max_targets`. `evaluate_guardrails`: build reasons; push a hard stop for each triggered condition; `action="park_and_stop"` if any hard stop, else `"continue"`; `proceed = not hard_stops`. Treat `battery_pct is None` as a noted non-fatal unknown (not a hard stop by itself, but add a reason). Never raise (wrap parsing).
- [ ] **Step 4: run → PASS** + ruff.
- [ ] **Step 5: commit** `feat(planning): autonomous night cores (plan_night + guardrails)` (`git add src/seestar_mcp/planning/autonomous.py tests/test_planning_autonomous.py`).

---

### Task 2: MCP tools (`simulate_night`, `check_night_guardrails`)

**Files:** Modify `src/seestar_mcp/server.py`; Test `tests/test_planning_tools.py` (extend) + `tests/test_server.py` (count 28 → 30).

**Interfaces — Produces:** controller methods + tools `simulate_night(date=None, types=None, limit=None)` and `check_night_guardrails(session_start_utc, max_session_hours=10.0, battery_floor_pct=20.0, dawn_margin_min=15.0)`. Tool count 28 → 30.

- [ ] **Step 1: failing tests**
```python
def test_autonomous_tools_registered():   # 30 tools incl the 2 new names
def test_simulate_night_issues_no_motion(tmp_path):
    # controller with a site profile + mocked plan (monkeypatch plan_targets to return
    # 2 compact targets); a mock alpaca that RECORDS calls; simulate_night -> ok:true with
    # an ordered "schedule"; assert the mock alpaca received NO put/method_sync (no motion).
def test_check_guardrails_disconnected_parks(tmp_path):
    # mock get_device_state to raise/empty -> connected False -> action "park_and_stop", ok:true
```
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement.** `simulate_night`: resolve `when`; load site (or ok:false); `plan = await self.plan_targets(date=when, types=types, limit=limit)`; if not plan["ok"] propagate; `dark = dark_window(site, when)`; `sched = plan_night(plan["targets"], dark)`; return `{"ok":True, "conditions": plan["conditions"], "dark_window_utc": dark, "schedule": [asdict(s) for s in sched], "projected_targets": len(sched)}`. NO motion. `check_night_guardrails`: resolve `now = datetime.now(timezone.utc).isoformat()`; load site (or ok:false); `dark = dark_window(site, now)`; gather live state best-effort: `dev = await self.alpaca.method_sync("get_device_state")` in try/except → parse `connected`/`verified`/battery via a `# FIRMWARE-DEPENDENT` helper `_parse_device_health(dev) -> (connected, verified, battery_pct)` (on any failure → `(False, False, None)`); `weather = await assess_conditions_weather(...)` best-effort (its `.go`); `verdict = evaluate_guardrails(now_utc=now, dark_window_utc=dark, session_start_utc=session_start_utc, battery_pct=..., weather_go=..., connected=..., verified=..., max_session_hours=..., battery_floor_pct=..., dawn_margin_min=...)`; return `{"ok":True, **asdict(verdict)}`; provenance-log. Register 2 thin `@mcp.tool()` wrappers with honest docstrings (`simulate_night`: "Dry-run tonight's autonomous plan (ordered target schedule) WITHOUT moving the scope."; `check_night_guardrails`: "Evaluate hard stop conditions for an autonomous run (dawn, battery, weather, connection, max duration)."). Update module docstring 28→30.
- [ ] **Step 4: run → PASS** (whole suite) + ruff. Update `tests/test_server.py` count 28→30 + `EXPECTED_TOOLS`.
- [ ] **Step 5: commit** `feat(planning): simulate_night + check_night_guardrails tools` (`git add src/seestar_mcp/server.py tests/test_planning_tools.py tests/test_server.py`).

---

### Task 3: `autonomous-night` skill + docs

**Files:** Create `skills/autonomous-night/SKILL.md`; Modify `README.md`, `SECURITY.md`.

- [ ] **Step 1** Write `skills/autonomous-night/SKILL.md` with frontmatter (`name: autonomous-night`, description covering "run the whole night", "image unattended", "autonomous session", "run targets all night"). Body implements Phases A/B/C from the spec: **A) propose via `simulate_night` + REQUIRE explicit user confirmation before any motion** (mandatory); **B) loop** — `check_night_guardrails` each iteration → if `park_and_stop` go to C; else next `ScheduledTarget` → `run-session` → `log_session_result`; defer faults to `anomaly-playbook`; **C) wind down + `park` + summary + notify**. Hard rules: dry-run+confirm is MANDATORY before motion; dawn/battery/precip/fault/max-duration are HARD stops that ALWAYS park; never skip `check_night_guardrails` between targets; log every session; `shutdown` only if pre-authorized; keep the user notified (Remote Control). Cross-reference: planning from `observing-planner`, execution from `run-session`, faults from `anomaly-playbook`, QA from `qa-policy`.
- [ ] **Step 2** `README.md`: add `simulate_night` + `check_night_guardrails` to the tools list (→ 30); add a short "Autonomous night" section noting it is opt-in, dry-run-first, guardrailed. Point to the skill.
- [ ] **Step 3** `SECURITY.md`: add a short "Autonomous operation" note — unattended motion is gated behind an explicit user confirmation of a no-motion dry run; hard guardrails (dawn/battery/weather/connection/max-duration) fail safe to `park`; every guardrail decision is provenance-logged; no new network/inbound surface.
- [ ] **Step 4** `uv run pytest -q` (whole suite green) + `uv run ruff check src tests`; verify skill frontmatter `name` matches folder.
- [ ] **Step 5: commit** `feat(planning): autonomous-night skill + docs` (`git add skills/autonomous-night/SKILL.md README.md SECURITY.md`).

---

## Self-Review

**Spec coverage:** pure cores `plan_night` + `evaluate_guardrails` (T1) ✓, hard-stop rules incl. fail-safe unknown state (T1/T2) ✓, `simulate_night` no-motion dry run (T2) ✓, `check_night_guardrails` live gather + FIRMWARE-DEPENDENT battery helper (T2) ✓, 30 tools (T2) ✓, `autonomous-night` skill with mandatory dry-run+confirm + hard stops + park-on-fault (T3) ✓, SECURITY autonomous-operation note (T3) ✓, determinism via injected inputs (Global Constraints) ✓.

**Placeholder scan:** none.

**Type consistency:** `ScheduledTarget`/`GuardrailVerdict` fields consistent T1↔T2; `plan_night` consumes the compact `plan_targets` dict shape (`id`/`best_window_utc`/`recommended_subs`) produced in Phase 1 T7; tool count 28→30 tracked in T2.

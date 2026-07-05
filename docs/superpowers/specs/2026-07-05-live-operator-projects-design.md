# Live Operator + Projects (Phase 2) — Design Spec

**Date:** 2026-07-05
**Project:** seestar-mcp
**Status:** Approved direction (from brainstorming) — ready for implementation planning
**Builds on:** Phase 1 (observing planner). **Precedes:** Phase 3 (autonomous night).

---

## Goal

Give the planner **memory and live reactivity**: (a) a persistent **projects/history** store so targets accumulate integration across nights toward goals ("M31: 2.5 h of 6 h — image more"), repeats are avoided, and the planner can answer "what needs more data?"; and (b) skill-level **live-operator** behavior so `run-session` consults the plan, monitors conditions during a session, reacts (switch target when one leaves its sweet band, pause/abort on incoming clouds), and **logs results back into projects** at wind-down.

## Design principles (inherited)

- MCP = access/compute (the store + queries), Skills = judgment (when to switch/abort).
- Deterministic, offline-first, never-raise on tool paths, provenance-logged, no secrets, no new heavy deps.
- Reason-tagged outputs. Local JSON state (gitignored), like the site profile.

---

## Architecture

New module `src/seestar_mcp/planning/projects.py`; new project tools on `SeestarController`; project-awareness in `ranker.py`; skill expansions to `run-session` and `anomaly-playbook`.

### Data models (`projects.py`)

```python
@dataclass
class SessionRecord:
    date_utc: str                 # ISO, session wind-down time
    integration_minutes: float    # kept integration this session
    subs_total: int
    subs_kept: int
    median_fwhm: float | None = None   # from qa_session_report if available
    notes: str = ""

@dataclass
class Project:
    target_id: str                # catalog id, e.g. "M31"
    target_name: str
    goal_minutes: float           # user integration goal (0 = open-ended)
    collected_minutes: float      # sum of kept integration across sessions
    status: str                   # "active" | "complete" | "paused"
    created_utc: str
    updated_utc: str
    sessions: list[SessionRecord] = field(default_factory=list)
    notes: str = ""
```

### Store functions (`projects.py`) — all take an explicit `now_utc` where a timestamp is recorded (determinism)

- `load_projects(path=None) -> dict[str, Project]` (keyed by target_id; `{}` if missing).
- `save_projects(projects, path=None) -> Path` (default `data/projects.json`, gitignored).
- `get_project(target_id, path=None) -> Project | None`.
- `upsert_project(target_id, target_name, *, goal_minutes=0.0, status=None, notes=None, now_utc, path=None) -> Project` — create or update metadata; never touches `sessions`/`collected_minutes` except to set goal/status/notes.
- `log_session_result(target_id, target_name, *, integration_minutes, subs_total, subs_kept, median_fwhm=None, notes="", now_utc, path=None) -> Project` — append a `SessionRecord`, add to `collected_minutes`; auto-set `status="complete"` when `goal_minutes>0 and collected_minutes>=goal_minutes`; create the project if new.
- `recommend_projects(path=None, *, limit=None) -> list[Project]` — active projects with `collected_minutes < goal_minutes` (or open-ended), ranked by remaining minutes desc (most-needed first).
- `was_recently_imaged(target_id, within_days, now_utc, path=None) -> bool` — True if any session within `within_days`.

### Ranker project-awareness (`ranker.py`)

`rank_targets(...)` gains an optional `projects: dict[str, Project] | None = None` and `now_utc` (already has `when_utc`). When provided:
- **Boost** active projects that still need data (add a bounded bonus to score, with reason "active project — N min of M collected, needs more").
- **Suppress** targets imaged within the last `RECENT_DAYS` (default 2) that are NOT active-needing-data (penalty + reason "imaged <k>d ago"), so fresh targets surface. Completed projects are suppressed with reason "goal met".
- Behavior with `projects=None` is exactly Phase 1 (backward compatible).

### New MCP tools (project group) on `SeestarController`

| Tool | Purpose |
|---|---|
| `list_projects()` | All projects with collected/goal/status. |
| `get_project(target)` | One project incl. session history. |
| `set_project_goal(target, goal_minutes)` | Create/update a project + its integration goal. |
| `log_session_result(target, integration_minutes, subs_total, subs_kept, median_fwhm?, notes?)` | Record a completed session (called at wind-down). |
| `recommend_projects(limit?)` | Active projects that need more data, most-needed first. |

`plan_targets` is extended to load projects and pass them (with `now_utc`) into `rank_targets`, plus accept `avoid_recent_days` (default 2) and `prefer_projects` (default True). Tool count 23 → 28.

### Skill expansions

**`run-session` (expand, do not rewrite):** add
- **Phase 0.5 — consult the plan:** if the user said "image tonight" without a target, defer to `observing-planner` for the shortlist; when a target is chosen, if it has a project, state progress ("M31: 2.5 h of 6 h").
- **Phase 4 monitoring — live reactivity:** poll `assess_conditions` on a slow cadence (e.g. every ~10 min) alongside `qa_tier1`. If `go` flips to False across two polls → route to `anomaly-playbook` (weather branch). When the current target leaves its sweet band (crosses the field-rotation ceiling / sets toward the floor) → tell the user and offer the next target from the plan.
- **Phase 5 wind-down — log the result:** after `qa_session_report`, call `log_session_result` (integration = kept subs × exposure ÷ 60; subs_total/kept and median_fwhm from the report) so the project accumulates. State updated project progress.

**`anomaly-playbook` (expand):** add a **weather-abort branch** — incoming clouds (`assess_conditions.go` false / cloud rising): pause stacking, alert the user with the forecast reason, offer to wait it out or wind down; on a hard no-go (precip) recommend winding down + `park`. Keep the "act automatically vs ask" discipline (pausing/parking always asks first).

## Data flow

```
run-session wind-down → log_session_result → projects.json
plan_targets → load_projects → rank_targets(projects, now) → project-aware shortlist
run-session monitor → assess_conditions (live) → anomaly-playbook (weather branch) on cloud
```

## Error handling

- Missing/corrupt `projects.json` → treated as empty; never raises (log a note).
- `log_session_result` for an unknown target → creates the project (name from catalog `find_target`, or the passed name).
- Ranker with `projects=None` → identical to Phase 1 (no regression).
- All timestamps injected (`now_utc`) for determinism; tool layer supplies `datetime.now(timezone.utc)`.

## Testing

- `projects.py`: round-trip; `log_session_result` accumulates + auto-completes at goal; `recommend_projects` ordering (most-needed first, excludes complete); `was_recently_imaged` boundary; missing file → `{}`.
- `ranker.py`: with a projects dict, an active-needing-data target outranks an equivalent fresh one; a target imaged yesterday is suppressed; `projects=None` reproduces Phase 1 ordering (regression test).
- tools: 28 registered; `set_project_goal` → `log_session_result` → `get_project` shows accumulation; `recommend_projects` returns needed projects; `plan_targets` still returns compact dicts and is project-aware when a store exists.
- Skills: `run-session`/`anomaly-playbook` frontmatter still valid; new sections present (a light assertion that the files contain the new phase headings is enough).
- No network in unit tests (weather mocked where touched); no hardware.

## Security / reproducibility

- New local state `data/projects.json` (gitignored) — no secrets. No new network calls, no new deps. Provenance logs project tool calls. Deterministic via injected `now_utc`.

## Deferred to Phase 3

The *autonomous* unattended loop (assess → run plan → react → park at dawn) and its guardrails/dry-run — Phase 2 only adds the memory + the human-in-the-loop reactive skill behavior that Phase 3 will automate.

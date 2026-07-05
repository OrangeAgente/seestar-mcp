# Live Operator + Projects (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a persistent projects/history store, project-aware ranking, project MCP tools, and live-operator skill behavior to seestar-mcp.

**Architecture:** New `src/seestar_mcp/planning/projects.py` (JSON store, deterministic via injected `now_utc`), project-awareness in `ranker.py`, 5 new MCP tools on `SeestarController` (→ 28 total), and expansions to the `run-session` / `anomaly-playbook` skills.

**Tech Stack:** Python 3.12, dataclasses, `pytest`. No new deps. `uv` toolchain.

## Global Constraints

- `uv run pytest`, `uv run ruff check src tests`. NEVER bare `python`.
- Deterministic: store functions take an explicit `now_utc`; the tool layer supplies `datetime.now(timezone.utc).isoformat()`.
- Never-raise on tool paths → `{"ok": False, "error": ...}`. Corrupt/missing store → treat as empty, never raise.
- Backward compatible: `rank_targets(..., projects=None)` must reproduce Phase 1 behavior exactly.
- Local state `data/projects.json` is gitignored (the repo already ignores `data/`). No secrets. Provenance-log tool calls.
- Spec of record: `docs/superpowers/specs/2026-07-05-live-operator-projects-design.md` (read it for exact dataclasses).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`; use `git -c core.autocrlf=false commit`.

---

### Task 1: Projects store (`projects.py`)

**Files:** Create `src/seestar_mcp/planning/projects.py`; Test `tests/test_planning_projects.py`.

**Interfaces — Produces:** `SessionRecord`, `Project` dataclasses (exact fields per spec); `load_projects(path=None) -> dict[str,Project]`; `save_projects(projects, path=None) -> Path`; `get_project(target_id, path=None) -> Project|None`; `upsert_project(target_id, target_name, *, goal_minutes=0.0, status=None, notes=None, now_utc, path=None) -> Project`; `log_session_result(target_id, target_name, *, integration_minutes, subs_total, subs_kept, median_fwhm=None, notes="", now_utc, path=None) -> Project`; `recommend_projects(path=None, *, limit=None) -> list[Project]`; `was_recently_imaged(target_id, within_days, now_utc, path=None) -> bool`.

- [ ] **Step 1: failing tests**
```python
# tests/test_planning_projects.py
from seestar_mcp.planning.projects import (load_projects, save_projects, get_project,
    upsert_project, log_session_result, recommend_projects, was_recently_imaged, Project)

NOW = "2026-07-05T04:00:00Z"

def test_missing_store_is_empty(tmp_path):
    assert load_projects(tmp_path / "p.json") == {}

def test_log_accumulates_and_completes(tmp_path):
    p = tmp_path / "p.json"
    upsert_project("M31", "Andromeda", goal_minutes=60, now_utc=NOW, path=p)
    log_session_result("M31","Andromeda", integration_minutes=25, subs_total=160, subs_kept=150, now_utc=NOW, path=p)
    proj = get_project("M31", path=p)
    assert proj.collected_minutes == 25 and proj.status == "active" and len(proj.sessions) == 1
    log_session_result("M31","Andromeda", integration_minutes=40, subs_total=250, subs_kept=240, now_utc="2026-07-06T04:00:00Z", path=p)
    assert get_project("M31", path=p).collected_minutes == 65
    assert get_project("M31", path=p).status == "complete"  # >= 60 goal

def test_recommend_orders_by_remaining(tmp_path):
    p = tmp_path / "p.json"
    upsert_project("M31","Andromeda", goal_minutes=360, now_utc=NOW, path=p)
    log_session_result("M31","Andromeda", integration_minutes=60, subs_total=1,subs_kept=1, now_utc=NOW, path=p)  # 300 remaining
    upsert_project("M42","Orion", goal_minutes=120, now_utc=NOW, path=p)
    log_session_result("M42","Orion", integration_minutes=30, subs_total=1,subs_kept=1, now_utc=NOW, path=p)  # 90 remaining
    recs = recommend_projects(path=p)
    assert [r.target_id for r in recs] == ["M31","M42"]  # most-needed first

def test_recently_imaged_boundary(tmp_path):
    p = tmp_path / "p.json"
    log_session_result("M13","Hercules", integration_minutes=10, subs_total=1,subs_kept=1, now_utc="2026-07-04T04:00:00Z", path=p)
    assert was_recently_imaged("M13", 2, now_utc="2026-07-05T04:00:00Z", path=p) is True
    assert was_recently_imaged("M13", 2, now_utc="2026-07-08T04:00:00Z", path=p) is False
```
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** per spec. JSON (de)serialization converts `Project`/`SessionRecord` to/from dicts. `recommend_projects` = active projects with `goal_minutes==0 or collected<goal`, sorted by `(goal_minutes - collected_minutes)` desc (open-ended treated as large remaining). `was_recently_imaged` parses ISO `date_utc`, compares to `now_utc - within_days`. Corrupt file → `{}` (never raise).
- [ ] **Step 4: run → PASS** + ruff.
- [ ] **Step 5: commit** `feat(planning): projects/history store` (`git add src/seestar_mcp/planning/projects.py tests/test_planning_projects.py`).

---

### Task 2: Ranker project-awareness

**Files:** Modify `src/seestar_mcp/planning/ranker.py`; Test `tests/test_planning_ranker.py` (extend).

**Interfaces — Consumes:** `Project` (T1). **Produces:** `rank_targets(..., projects: dict[str,Project]|None=None, now_utc: str|None=None, recent_days: int=2)` — additive optional params; default `None` = Phase 1 behavior.

- [ ] **Step 1: failing tests** (extend the ranker test)
```python
def test_active_project_needing_data_outranks_fresh(...):
    # two identical-observability emission targets A,B; A is an active project 30/360 min.
    # with projects={A: active-needing}, A must outrank B; reason mentions "active project".
def test_recently_imaged_suppressed(...):
    # target C imaged yesterday (not an active-needing project) is penalized vs fresh D.
def test_projects_none_matches_phase1(...):
    # same inputs, projects=None -> identical ordering/scores to the existing Phase 1 test.
```
(Use injected `observability_fn` + hand-built `Project` objects; no astropy.)
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement.** Add a bounded project bonus (e.g. +0.10 to the 0..1 blend, clamped) for active projects with `collected<goal`, reason `"active project — {collected:.0f} of {goal:.0f} min, needs more"`. Apply a penalty (e.g. −0.15) + reason `"imaged {days}d ago"` when `was_recently_imaged`-style check (use the passed `projects` + `now_utc`, don't re-read disk) and the target is NOT an active-needing project. Completed projects: penalty + reason `"goal met"`. `projects=None` → skip all of this (unchanged).
- [ ] **Step 4: run → PASS** (whole suite) + ruff.
- [ ] **Step 5: commit** `feat(planning): project-aware ranking (boost needed, suppress recent)`.

---

### Task 3: Project MCP tools + plan_targets integration

**Files:** Modify `src/seestar_mcp/server.py`; Test `tests/test_planning_tools.py` (extend).

**Interfaces — Produces:** controller methods + tools `list_projects`, `get_project`, `set_project_goal`, `log_session_result`, `recommend_projects`; `plan_targets` gains `avoid_recent_days=2, prefer_projects=True` and passes the loaded projects + `now_utc` to `rank_targets`. Tool count 23 → 28.

- [ ] **Step 1: failing tests**
```python
def test_project_tools_registered():  # 28 tools incl the 5 new names
def test_goal_then_log_then_get(tmp_path):  # set_project_goal -> log_session_result -> get_project shows accumulation, ok:true
def test_recommend_projects_tool(tmp_path):  # returns the needed project
def test_plan_targets_still_compact_and_ok():  # project-aware path returns {ok:true, targets:[compact...]}
```
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement.** Controller methods delegate to `planning.projects`, using `self._projects_path()` = `self.settings.data_dir/"projects.json"` and `now = datetime.now(timezone.utc).isoformat()`. Each returns `{"ok":True, ...}` / catches → `{"ok":False,"error":...}`; provenance-logged. Extend `plan_targets` to `load_projects(self._projects_path())` and pass `projects=` + `now_utc=` to `rank_targets` (guard with `prefer_projects`). Register 5 thin `@mcp.tool()` wrappers with honest docstrings.
- [ ] **Step 4: run → PASS** (whole suite) + ruff. Update the tool-count assertion in `tests/test_server.py` (23 → 28) and `EXPECTED_TOOLS`.
- [ ] **Step 5: commit** `feat(planning): 5 project MCP tools + project-aware plan_targets` (`git add src/seestar_mcp/server.py tests/test_planning_tools.py tests/test_server.py`).

---

### Task 4: Skill expansions (run-session + anomaly-playbook) + docs

**Files:** Modify `skills/run-session/SKILL.md`, `skills/anomaly-playbook/SKILL.md`, `README.md`.

- [ ] **Step 1** `run-session/SKILL.md`: add **Phase 0.5 — consult the plan** (defer to observing-planner for target choice; state project progress if the target is a project), extend **Phase 4** with live reactivity (slow `assess_conditions` poll alongside `qa_tier1`; go→False twice routes to anomaly-playbook weather branch; when the target leaves its sweet band, offer the next planned target), and extend **Phase 5** to call `log_session_result` at wind-down (integration = kept subs × exposure ÷ 60; subs + median_fwhm from `qa_session_report`) and state updated project progress. Keep the existing content; additive edits.
- [ ] **Step 2** `anomaly-playbook/SKILL.md`: add a **Symptom: incoming clouds / weather no-go** branch — confirm via `assess_conditions`; pause stacking and alert with the forecast reason; offer wait-it-out vs wind down; on precip/hard no-go recommend wind down + `park`. Preserve "act automatically vs ask" (pause/park always ask first).
- [ ] **Step 3** `README.md`: add the 5 project tools to the tools list; note projects/history + live-operator behavior in the planner section. Update tool count to 28.
- [ ] **Step 4** `uv run pytest -q` (whole suite green) + `uv run ruff check src tests`. Confirm both skills' YAML frontmatter still valid.
- [ ] **Step 5: commit** `feat(planning): live-operator skill behavior (projects + weather reactivity)`.

---

## Self-Review

**Spec coverage:** projects store (T1) ✓, ranker project-awareness + backward compat (T2) ✓, 5 project tools + plan_targets integration (T3) ✓, run-session/anomaly-playbook expansions + logging at wind-down (T4) ✓, determinism via `now_utc` (Global Constraints + T1/T2) ✓, gitignored store (T1) ✓.

**Placeholder scan:** none — each task has concrete tests + implementation instructions.

**Type consistency:** `Project`/`SessionRecord` fields consistent across T1–T3; `rank_targets` `projects`/`now_utc` params match T2↔T3; tool count 23→28 tracked in T3.

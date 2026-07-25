# Seestar Skills — Community Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the five operational Seestar run-book skills correct, portable, and safe for a stranger with their own S50, and add the packaging (bundle README + rig-profile template) that public distribution requires.

**Architecture:** Sort every claim into Universal (stays as fact) / Conditional (becomes a diagnostic procedure) / Personal (moves to an optional `docs/RIG-PROFILE.md` that `run-session` reads if present). Correct guidance contradicted by the 2026-07-24 run. Prose-only change — no `src/` edits.

**Tech Stack:** Markdown skills (`skills/*/SKILL.md`, YAML frontmatter `name` + `description`), verified with grep-based audits and the repo's existing `uv run pytest` / `uv run ruff` gate.

## Global Constraints

- **Scope:** exactly five skills — `observing-planner`, `run-session`, `autonomous-night`, `anomaly-playbook`, `qa-policy`. Do NOT touch `image-refinement` or `astro-processing`.
- **No `src/` changes.** The repo gate must stay exactly as green as it is on `main`.
- **Privacy (hard):** no real coordinates, SSIDs, hostnames, IPs, local filesystem paths (`C:\`, UNC `\\`), API keys, or the author's name in `skills/**` or `docs/RIG-PROFILE.md`. The reference offset appears only as "one reference S50 … ~20–30′ frame-left".
- **No N=1 assertions.** No "on this unit", no dated war-story framing ("learned the hard way on the 2026-07-12 run").
- **Tool-name truth:** every MCP tool named in a skill must exist among the 33 in `src/seestar_mcp/server.py`. `run_autofocus` **is** a real tool — only the *firmware method* may be missing; never claim the tool is absent.
- **Voice:** keep the existing style — lead with state, one-line phone-friendly output, hard-rules sections, explicit deferral between skills.
- **Branch:** `skills-community-release`. Commit with `git -c core.autocrlf=false`.

---

### Task 1: Rig-profile template (establishes the path skills reference)

**Files:**
- Create: `docs/RIG-PROFILE.md`

**Interfaces:**
- Produces: the path `docs/RIG-PROFILE.md`, consumed by `run-session` Phase 0 in Task 2. Optional at runtime — every skill must work without it.

- [ ] **Step 1: Write the privacy assertion (the test)**

```bash
# Must print nothing once the file exists.
grep -nEi "45\.4|75\.6|<city>|joshu|192\.168|C:\\\\|\\\\\\\\|ssid|[A-Za-z0-9]{16,}" docs/RIG-PROFILE.md
```
Expected before the file exists: `No such file or directory`.

- [ ] **Step 2: Create `docs/RIG-PROFILE.md`**

```markdown
# Rig profile (optional)

Per-unit, per-site observations that are true of **your** Seestar and **your** sky — not of
Seestars in general. The run-book skills read this file at session start if it exists, and
work fine without it. Keep real coordinates out of anything you publish.

Fill in only what you have actually measured. "Unknown" is a valid, honest answer.

## Unit

- **Model / firmware:** e.g. Seestar S50, firmware 7.75
- **Mount mode:** alt-az (default) | EQ (wedge + polar align)
- **Measured pointing offset:** direction and magnitude, or "none observed".
  Only record a value here once you have classified it as *systematic* using the
  procedure in the `run-session` skill (persists across different sky angles AND
  survives a power-cycle + re-level + fresh dark alignment). Note how you measured it,
  e.g. "annotated centre ~170/1080 px vs frame centre 540 — measured on 3 targets at
  different sky angles".
- **Focuser baseline:** typical position after the acquisition autofocus, if you track it.

## Site

- **Site name:** a label only. Set the real coordinates via `set_site_profile`, which
  keeps them in local data — do not write them here.
- **Bortle / sky brightness:** e.g. 8 (city)
- **Dark window:** how much astronomical dark you actually get in each season. At high
  latitude in summer this can be short or absent.
- **Known obstructions:** prefer the learned horizon mask (`suggest_horizon_mask` →
  `add_horizon_mask`) over prose. Note only what the mask cannot express, e.g.
  "neighbour's floodlight on a timer after midnight".

## Network / operations

- **Wi-Fi band used:** 2.4 GHz | 5 GHz, and whether the control link has been stable.
- **Data offload method and timing:** e.g. "share copy, after wind-down only" — never
  during a session; heavy transfers starve the scope's control link.
- **Anything else that has bitten you twice.** Once is an anecdote; twice is a rig quirk.
```

- [ ] **Step 3: Run the privacy assertion**

Run: `grep -nEi "45\.4|75\.6|<city>|joshu|192\.168|C:\\\\|\\\\\\\\|ssid|[A-Za-z0-9]{16,}" docs/RIG-PROFILE.md`
Expected: no output (exit 1). If anything prints, remove it.

- [ ] **Step 4: Commit**

```bash
git -c core.autocrlf=false add docs/RIG-PROFILE.md
git -c core.autocrlf=false commit -m "docs: optional rig-profile template for per-unit quirks"
```

---

### Task 2: `run-session` — focus, acquisition, framing diagnostic

**Files:**
- Modify: `skills/run-session/SKILL.md` (Phase 0 ~28-40, Phase 1 ~52-72, Phase 2 ~74-79, framing check ~119-138, field notes ~172-188)

**Interfaces:**
- Consumes: `docs/RIG-PROFILE.md` (Task 1).
- Produces: the **offset classification procedure** and the **stuck-vs-normal-acquisition discriminator**, both referenced by `autonomous-night` (Task 3) and `anomaly-playbook` (Task 4). Later tasks must not restate them — they link here.

- [ ] **Step 1: Write the assertions (the test)**

```bash
grep -nE "on this unit|learned the hard way|2026-07-1" skills/run-session/SKILL.md
```
Expected NOW: matches at the framing-check and field-notes sections (this is the failing state).

- [ ] **Step 2: Add the rig-profile hook to Phase 0**

Append as a new numbered item at the end of Phase 0 (after the mount-mode item):

```markdown
5. **Read the rig profile if present.** If `docs/RIG-PROFILE.md` exists, read it once now
   and treat its contents as observations about *this specific unit and site* — not as
   general Seestar behavior. If it does not exist, proceed normally and run the relevant
   diagnostic when a symptom actually appears.
```

- [ ] **Step 3: Replace Phase 1 items 3-4 with the acquisition-aware version**

```markdown
3. **Expect a multi-stage acquisition, and budget for it.** A goto normally progresses
   `Initialise` → `3PPA` (a 3-point plate-solve alignment, which runs its own `AutoFocus`)
   → `AutoGoto` → `Stack`. On a healthy run this takes **~2–4 minutes** before the first
   frame stacks. Budget it into every slot: a 45-minute slot yields roughly 42 minutes of
   integration, and a six-target night spends ~20 minutes acquiring.
4. **Verify the slew actually happened — `goto_target` returns ok even when the mount did
   NOT move.** Poll `get_view_state`. Healthy progress = `PlateSolve` reaching `complete`,
   `3PPA percent` climbing, and `ScopeGoto dist_deg` shrinking toward ~0. Two failure
   signatures to catch and act on:
   - **Genuinely stuck:** >~4 min elapsed **and** no solve progress (or repeated solve
     failures) **and** zero frames stacked. That is an unsolvable field — usually
     **obstructed** (roof, tree, wall; typically a low target). Skip it and take the next
     target from the plan; do not wait it out. **Do not diagnose this from elapsed time
     alone** — a normal alignment also sits in `Initialise` for minutes.
   - **Dropped to `ContinuousExposure` with pointing unchanged from before the goto** → the
     mount never slewed (parked, or bad coordinates). Recover via anomaly-playbook; do NOT
     start stacking on a phantom goto.
```

- [ ] **Step 4: Replace all of Phase 2 (Focus)**

```markdown
## Phase 2 — Focus (usually already done for you)
1. **Acquisition normally focuses the scope.** The `Initialise` sequence a goto triggers
   runs its own autofocus (visible as an `AutoFocus` event reaching `complete`, then the
   focuser settling). In the normal case there is nothing to do here: confirm focus was
   established and record the position from `get_focuser_position` as the session baseline
   for drift detection.
2. **`run_autofocus` is optional and firmware-dependent.** The MCP tool exists, but on some
   firmware the underlying device method is unavailable and the call returns an error. Use
   it only for a *deliberate mid-session refocus* (see the focus-drift branch in
   anomaly-playbook). **Never block a session on it** — if it errors, you already have the
   focus established during acquisition.
3. If the focuser position is implausible, or stars look soft in the Phase 4 framing check,
   hand off to the anomaly-playbook skill (focus branch) rather than improvising.
```

- [ ] **Step 5: Replace the "Visual framing check" section**

```markdown
### Visual framing check (do NOT trust telemetry alone)
`stacked N` confirms frames are landing — NOT that the object is framed, focused, or
cloud-free. **Check the actual field at least once per target, EARLY (~5–10 min in), not
only at the end.** The cheapest source is the live plate-solve annotation: `get_view_state`
→ `Stack.Annotate` gives the object's centre `pixelx`/`pixely` and `radius` in the
1080×1920 frame. Alternatively pull the newest sub JPG from the scope's share (`_LP_` in the
filename confirms the dual-band filter engaged, `_IRCUT_` = broadband). Confirm three
things: the object is **in frame**, stars are **tight** (focus good), and the background is
**clean** (no cloud haze).

**If the object is off-centre, classify the offset before reacting.** The frame centre is
(540, 960); compare it against the annotated centre.

| Evidence | Reading | Action |
|---|---|---|
| Offset **varies** between runs, or appeared after a bump, move, or travel | Alignment/level problem — **fixable** | Re-level, re-run a dark-sky alignment, and verify the site/time the scope is using |
| Offset **persists across different sky angles** AND survives a power-cycle + re-level + fresh dark alignment | **Systematic** to that unit | Accept it while the object is fully captured; record it in the rig profile and stop re-testing it |
| Object **cut off at a frame edge** | Framing failure, whatever the cause | Re-acquire; if it recurs at that sky angle, compose around it |

Two captures at **different sky angles** are the minimum evidence for "systematic" — a
single off-centre frame proves nothing, because alt-az rotation smears a fixed angular error
around the frame as the target moves. Once classified as systematic, do not spend session
time or power-cycles chasing a re-centre; note it and keep imaging. (For calibration: one
reference S50 measured a ~20–30′ frame-left offset that persisted through a power-cycle,
re-level, and fresh dark alignment.)

For faint nebulae a single 10 s sub barely shows the object — that is normal; the
accumulated stack reveals it. The check here is framing/focus/clouds, not depth.
```

- [ ] **Step 6: Rewrite the field-notes section header and de-personalize its contents**

Replace the heading `## Field-tested notes (learned the hard way on the 2026-07-15/16 run)` with `## Operating notes`, and within it:
- Replace "the low NE is the worst offender here" with "a low bearing over a roofline or tree line is the usual offender".
- Generalize the transfer warning to: "**Do NOT run a heavy file transfer off the scope's share during a session.** Pulling images off the scope saturates its Wi-Fi and starves the control link, and the bridge then fails to authenticate. Offload before the session or after wind-down. (The scope may also drop its share when it sleeps after `park`, so finish offloads while it is awake.)"
- Delete the `run_autofocus` bullet (now covered correctly by Phase 2).

- [ ] **Step 7: Run the assertions**

```bash
grep -nE "on this unit|learned the hard way|2026-07-1|worst offender here" skills/run-session/SKILL.md
```
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git -c core.autocrlf=false add skills/run-session/SKILL.md
git -c core.autocrlf=false commit -m "skills(run-session): correct focus/acquisition model, make framing offset a diagnostic"
```

---

### Task 3: `autonomous-night` — guardrail semantics, slot budgeting, notes

**Files:**
- Modify: `skills/autonomous-night/SKILL.md` (Phase A ~35-53, Phase B ~55-75, field notes ~111-145)

**Interfaces:**
- Consumes: the offset procedure and stuck-acquisition discriminator from `run-session` (Task 2) — reference them, do not restate.
- Produces: the "Guardrail semantics" section referenced by nothing else (self-contained).

- [ ] **Step 1: Write the assertions (the test)**

```bash
grep -nE "on this unit|learned the hard way|2026-07-1" skills/autonomous-night/SKILL.md
```
Expected NOW: matches in the field-notes section (failing state).

- [ ] **Step 2: Insert a "Guardrail semantics" section immediately before "## Phase B"**

```markdown
## Guardrail semantics (read this before overriding a stop)
Hard stops are **predictive, not reactive.** `check_night_guardrails` reads the forecast and
device health, so a weather stop can fire while the current sky is still stacking cleanly
with zero dropped frames. **That is correct behavior, not a false positive** — the lead time
is exactly what lets the mount stop and fold *before* precipitation or heavy dew arrives.
Two signals commonly drive it:
- **precipitation** forecast inside the session window, and
- **dew risk** — a small temperature/dew-point spread (a couple of °C or less) means
  condensation forming on the optics, which ends the night's usefulness even under a clear
  sky.

When a stop fires: corroborate once with `assess_conditions` to get the human-readable
reason, state it in one line, and **wind down (Phase C).** Do not re-run the check hoping
for a different answer, and never resume a stopped run unless the user explicitly asks.
```

- [ ] **Step 3: Add slot-budgeting to Phase A step 2**

Append to the schedule-presentation bullet:

```markdown
   - When presenting the schedule, note that **each target spends ~2–4 min acquiring**
     (alignment + autofocus) before its first frame stacks, so a 45-min slot yields roughly
     42 min of integration. Do not promise the full slot as integration time.
```

- [ ] **Step 4: Replace the field-notes section**

Change the heading `## Field-tested notes (learned the hard way on the 2026-07-12 run)` to `## Operating notes`, drop the "Ignoring these cost most of a night" preamble, and revise its bullets:
- **goto verification** bullet → replace its body with: "`goto_target` returns ok even when the mount does not slew, and a normal alignment sits in `Initialise` for minutes. Use the acquisition discriminator in **`run-session`** (Phase 1) to tell a healthy alignment from an obstructed field; skip obstructed targets rather than waiting them out."
- **framing** bullet → replace its body with: "Confirm framing from a real image or the live plate-solve annotation once per target, early. If the object is off-centre, classify the offset with the procedure in **`run-session`** (Visual framing check) before reacting — do not assume it is systematic, and do not burn a slot re-centring one that is."
- **PARK** bullet → keep as-is (universal and correct).
- **twilight** bullet → replace "Summer / high-latitude twilight" body with: "At high latitude in summer, astronomical dark can be short or absent. Put **broadband** targets in the real dark and **LP/dual-band** targets into twilight — dual-band tolerates a brightening sky far better. Expect the drop rate to climb toward dawn; that is the natural end of the useful night, not a fault."
- **coordinates** bullet → keep (universal).
- **file-transfer** bullet → keep, generalized identically to Task 2 Step 6.
- **drops-everything** bullet → keep (universal disambiguation logic).

- [ ] **Step 5: Add the slot-pacing note at the end of "## Operating notes"**

```markdown
- **Pacing a long slot.** A 45-minute slot does not need minute-by-minute polling, and tight
  loops cost more than they reveal. Sample the stack counters on a slow cadence (~1 min) and
  watch for three exit conditions: the **slot boundary**, a **drop-spike** (dropped frames
  climbing sharply across consecutive samples — a cloud bank or something drifting into the
  light path), or a **link fault** (repeated solve failures or connection errors). Any of
  those warrants a decision; between them, stay quiet.
```

- [ ] **Step 6: Run the assertions**

```bash
grep -nE "on this unit|learned the hard way|2026-07-1" skills/autonomous-night/SKILL.md
```
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git -c core.autocrlf=false add skills/autonomous-night/SKILL.md
git -c core.autocrlf=false commit -m "skills(autonomous-night): document predictive guardrails, slot budgeting, depersonalize notes"
```

---

### Task 4: `anomaly-playbook` — two new symptoms, soften N=1 claim

**Files:**
- Modify: `skills/anomaly-playbook/SKILL.md` (autofocus symptom ~82-89, connection symptom ~97-106)

**Interfaces:**
- Consumes: the acquisition discriminator from `run-session` Phase 1 (Task 2).

- [ ] **Step 1: Write the assertion (the test)**

```bash
grep -nE "goto seems stuck|authentication fails" skills/anomaly-playbook/SKILL.md
```
Expected NOW: no output (the symptoms are missing — failing state).

- [ ] **Step 2: Insert both new symptom sections immediately before "## Symptom: plate-solve fails"**

```markdown
## Symptom: goto seems stuck (but may be a normal alignment)
Likely cause: the normal `Initialise`/`3PPA` alignment the firmware runs on a goto, which
takes minutes and includes its own autofocus — NOT a fault. This is the most common misread.
- Judge by **progress, not elapsed time**: a healthy alignment shows plate-solves reaching
  `complete`, the alignment percentage climbing, and the goto distance shrinking toward ~0.
  Let it finish (~2–4 min).
- Treat it as a real fault only when **all three** hold: >~4 min elapsed, **no** solve
  progress (or repeated solve failures), and zero frames stacked. Then the field is
  unsolvable — usually an obstruction at that bearing. Skip the target, and log it with
  `log_sky_result(target=..., solved=False)` so the obstruction learner sees the evidence.

## Symptom: bridge connects but authentication fails
Likely causes: another client already holds the scope's single control channel (a phone app
is the usual culprit); a heavy file transfer starving the link; a missing or wrong interop
key on firmware 7.18+.
- A **single** failed handshake at startup is not conclusive — the bridge retries on its
  heartbeat and often authenticates seconds later. Watch for the retry before acting.
- If it keeps failing: close the phone app completely, stop any transfer off the scope's
  share, confirm the scope has finished booting, then restart the bridge.
- If it started right after a firmware update, suspect the auth handshake; do not attempt to
  patch it mid-session.
```

- [ ] **Step 3: Soften the 5 GHz claim in the connection symptom**

Replace `WiFi instability (5 GHz is flaky on the S50 — 2.4 GHz is more stable)` with `WiFi instability (if the link is unstable, try the 2.4 GHz band — it is often more robust at range)`.

- [ ] **Step 4: Reconcile the autofocus symptom with the new focus model**

Prepend to the autofocus symptom body:

```markdown
- **First: focus is normally established during acquisition** (the alignment runs its own
  autofocus), so a failing `run_autofocus` is not automatically a problem — see
  `run-session` Phase 2. On some firmware the underlying device method is unavailable and
  the call simply errors; that is not a focuser fault and must not block the session.
```

- [ ] **Step 5: Run the assertion**

```bash
grep -nE "goto seems stuck|authentication fails" skills/anomaly-playbook/SKILL.md
grep -n "5 GHz is flaky" skills/anomaly-playbook/SKILL.md
```
Expected: first grep prints both new headings; second prints nothing.

- [ ] **Step 6: Commit**

```bash
git -c core.autocrlf=false add skills/anomaly-playbook/SKILL.md
git -c core.autocrlf=false commit -m "skills(anomaly-playbook): add alignment-vs-stuck and auth-failure branches"
```

---

### Task 5: `observing-planner` + `qa-policy` — latitude caveat and Siril drift

**Files:**
- Modify: `skills/observing-planner/SKILL.md` (Phase 1 ~61-84)
- Modify: `skills/qa-policy/SKILL.md:86`

**Interfaces:** none (both self-contained).

- [ ] **Step 1: Write the assertion (the test)**

```bash
grep -n "Siril\|OSC_Preprocessing_WithoutDBF" skills/qa-policy/SKILL.md
```
Expected NOW: line 86 matches (failing state — it names a backend this project does not use).

- [ ] **Step 2: Fix the `qa-policy` keep-list pointer (line 86)**

Replace:
```
4. Point to the keep-list path for re-stacking (the kept subs feed a Siril re-stack with
   OSC_Preprocessing_WithoutDBF.ssf, since Seestar subs are already calibrated).
```
with:
```
4. Point to the keep-list path for re-stacking. The kept subs are what a stacker should
   consume — Seestar subs arrive already calibrated, so no separate darks/flats step is
   needed. Hand off to the `image-refinement` skill, which owns backend choice.
```

- [ ] **Step 3: Add the latitude caveat to `observing-planner` Phase 1**

Insert as a new numbered item after the "Moon / dew" item:

```markdown
7. **Latitude caveat.** At high latitude in summer, astronomical dark can be short or
   absent — the tools return the real dark window, so quote it rather than assuming a full
   night. When dark time is scarce, sequence **broadband** targets (galaxies, clusters) into
   the true dark and **emission/dual-band** targets into twilight, which they tolerate far
   better.
```

- [ ] **Step 4: Run the assertions**

```bash
grep -n "Siril\|OSC_Preprocessing_WithoutDBF" skills/qa-policy/SKILL.md   # expect: no output
grep -n "Latitude caveat" skills/observing-planner/SKILL.md               # expect: one match
```

- [ ] **Step 5: Commit**

```bash
git -c core.autocrlf=false add skills/qa-policy/SKILL.md skills/observing-planner/SKILL.md
git -c core.autocrlf=false commit -m "skills: fix qa-policy stacker drift, generalize planner latitude caveat"
```

---

### Task 6: `skills/README.md` — the bundle document

**Files:**
- Create: `skills/README.md`

**Interfaces:**
- Consumes: the final state of all five skills (Tasks 2-5), so write this last.

- [ ] **Step 1: Write the assertion (the test)**

```bash
test -f skills/README.md && grep -cE "Prerequisites|Safety|Compatibility|License" skills/README.md
```
Expected NOW: `No such file` (failing state). After Step 2: a count of `4` or more.

- [ ] **Step 2: Create `skills/README.md`**

Content must contain, in this order:
1. **What this is** — five run-book skills that drive a ZWO Seestar S50 through the `seestar-mcp` MCP server; the skills carry judgment, the MCP tools carry access.
2. **The skill graph** —
   ```
   observing-planner   plan: go/no-go verdict + ranked targets
          │  hands a chosen target to
          ▼
   run-session         execute: acquire → focus → stack → monitor → wind down
          │                    │
          │ faults             │ sub quality
          ▼                    ▼
   anomaly-playbook      qa-policy
          ▲
          └── autonomous-night orchestrates all four for a whole unattended night
   ```
3. **Prerequisites** — `seestar-mcp` registered in Claude Code (stdio); `seestar_alp` running and reachable; the Alpaca device number matching your `seestar_alp` config (commonly `1`); a **user-supplied** firmware-7.18+ RSA interop key (this project ships none); "save each frame in enhancing" enabled so Tier-2 QA has subs.
4. **Compatibility** — validated against Seestar S50, alt-az, station mode, firmware 7.75, `seestar-mcp` 0.1.0. Firmware changes are the expected breakage vector; device method names are the first thing to check when a tool starts failing.
5. **Safety contract** — verbatim:
   ```markdown
   ## Safety contract

   These skills command real hardware: they slew a motorized mount, change filters and the
   dew heater, and can run unattended for a whole night.

   **Always requires your explicit confirmation:** any slew or motion command, enabling the
   dew heater, editing the horizon mask, parking, shutting down, and starting an autonomous
   night (which begins with a no-motion dry run you must approve).

   **Runs unattended once you approve a night:** target sequencing, stacking, monitoring,
   quality scoring, and the wind-down.

   **Five hard stops, each ending in `park`:** approaching dawn, low battery, weather
   no-go (precipitation or dew risk), lost connection or unverified scope, and maximum
   session duration. Hard stops are predictive — one can fire while the sky still looks
   clear. That is intended.

   **Known consequence:** `park` points the optics at the cradle, from which the firmware
   cannot plate-solve, so a parked scope needs a fresh alignment before it images again.
   Park at wind-down, not to pause mid-session.

   **Your responsibility:** your hardware, your sky, your neighbours, and your local rules.
   Supervise the first few runs before trusting an unattended night, and check the weather
   yourself — a forecast is not a guarantee.
   ```
6. **Install** — clone the repo, install the MCP env with `uv sync`, register with
   `claude mcp add seestar-mcp -- uv --directory <repo> run python -m seestar_mcp.server`
   (register **before** starting a Remote Control session — MCP servers cannot be added
   mid-session), and copy `skills/` into your Claude Code skills location (or use the repo
   as your working directory).
7. **Optional rig profile** — point at `docs/RIG-PROFILE.md`.
8. **License / attribution** — this repo is MIT. `seestar_alp` is GPL-3.0, external, and is
   never bundled or redistributed here. No ZWO key or firmware material is distributed; the
   firmware-7.18+ interop key is user-supplied.
9. **Contributing a rig quirk** — report firmware changes and unit-specific behavior as
   issues; keep unit-specific values in your own rig profile rather than in the skills.

- [ ] **Step 3: Run the assertion + privacy grep**

```bash
grep -cE "Prerequisites|Safety|Compatibility|License" skills/README.md
grep -nEi "45\.4|75\.6|<city>|joshu|192\.168|C:\\\\|ssid" skills/README.md
```
Expected: first ≥ 4; second no output.

- [ ] **Step 4: Commit**

```bash
git -c core.autocrlf=false add skills/README.md
git -c core.autocrlf=false commit -m "docs(skills): bundle README with prerequisites, safety contract, licensing"
```

---

### Task 7: Verification — three audits + repo gate

**Files:** none modified (audit only; fix any failure in the owning file and re-commit).

- [ ] **Step 1: Portability audit — no N=1 assertions or personal data**

```bash
grep -rnE "on this unit|learned the hard way|2026-07-[0-9]|worst offender here|5 GHz is flaky" skills/
grep -rnEi "<city>|joshu|192\.168|45\.40|75\.66|C:\\\\|\\\\\\\\[a-z]|ssid|EMMC Images" skills/ docs/RIG-PROFILE.md
```
Expected: both print nothing.

- [ ] **Step 2: Tool-name consistency audit — every named tool must exist**

```bash
# Extract tool-like identifiers from the five skills and diff against the registered tools.
grep -rhoE "\b(add_horizon_mask|assess_conditions|check_night_guardrails|connect_telescope|download_subs|get_focuser_position|get_project|get_site_profile|get_status|get_target_observability|get_view_state|goto_target|list_projects|list_subs|log_session_result|log_sky_result|park|plan_targets|plate_solve|qa_session_report|qa_tier1|qa_tier2|recommend_projects|run_autofocus|set_dew_heater|set_filter|set_project_goal|set_site_profile|shutdown|simulate_night|start_stack|stop_view|suggest_horizon_mask)\b" \
  skills/observing-planner/SKILL.md skills/run-session/SKILL.md skills/autonomous-night/SKILL.md skills/anomaly-playbook/SKILL.md skills/qa-policy/SKILL.md | sort -u > /tmp/skill_tools.txt
grep -oE "^async def [a-z_0-9]+" src/seestar_mcp/server.py | sed 's/async def //' | sort -u > /tmp/real_tools.txt
comm -23 /tmp/skill_tools.txt /tmp/real_tools.txt
```
Expected: `comm` prints nothing (no skill names a tool that does not exist).

Then catch *unregistered* names written in tool style:
```bash
grep -rhoE "\`[a-z_]{4,}\(" skills/*/SKILL.md | tr -d '`(' | sort -u | while read -r t; do
  grep -qx "$t" /tmp/real_tools.txt || echo "NOT A TOOL: $t"
done
```
Expected: only non-tool prose helpers (e.g. `set_toml`-style names should not appear). Investigate anything surprising.

- [ ] **Step 3: Fresh-reader audit**

Read `skills/README.md` → `observing-planner` → `run-session` → `autonomous-night` start-to-finish as if you own a new S50 and have never seen this repo. Confirm: prerequisites are complete, no step depends on knowledge that only the author has, every cross-skill reference resolves to a skill in the bundle, and no instruction tells you to ignore a symptom without first classifying it. Fix anything that fails.

- [ ] **Step 4: Repo gate (must be unchanged from `main`)**

```bash
uv run ruff check src tests
uv run pytest -q
```
Expected: ruff clean; pytest `1 failed, 327 passed` — the single pre-existing environmental failure `test_check_backends_controller` (this machine has `pixinsight-mcp` configured while the test hard-codes its absence; it fails identically on `main`). No new failures.

- [ ] **Step 5: Commit any audit fixes**

```bash
git -c core.autocrlf=false add -A skills docs
git -c core.autocrlf=false commit -m "skills: audit fixes for portability and tool-name consistency"
```

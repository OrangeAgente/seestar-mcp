# Seestar Skills — Community Release (5 Operational Skills) — Design Spec

**Date:** 2026-07-25
**Project:** seestar-mcp
**Status:** Approved direction (from discussion) — ready for implementation planning
**Scope:** `skills/observing-planner`, `skills/run-session`, `skills/autonomous-night`,
`skills/anomaly-playbook`, `skills/qa-policy` + two new docs. Distribution: **in-repo**.

---

## Goal

Make the five operational Seestar run-book skills safe and useful for **someone who is not
this repo's author** — a stranger with their own S50, their own sky, and no memory of the
sessions that produced these notes — and document them well enough to share publicly.

Explicitly **not** a rewrite: the skills' architecture is sound. This is a correctness,
portability, and honesty pass, plus the packaging that public distribution requires.

## Why — findings from the critical evaluation

Ranked by severity for a third-party reader.

1. **(Critical) One rig's defect is stated as universal truth.** `run-session` (framing
   check) and `autonomous-night` (field notes) both assert that a ~20–30′ frame-left offset
   is a "KNOWN systematic pointing offset **on this unit**" and instruct the reader to
   "**accept it and keep imaging; do NOT burn a slot or a power-cycle chasing a re-centre**."
   This generalizes N=1 hardware evidence into an instruction to *ignore* off-centre framing.
   On another user's scope the same symptom may be a genuinely fixable bad level, stale
   alignment, or wrong site/time — and this text suppresses that diagnosis. Same class:
   "the low NE is the worst offender here" (the author's roofline).
2. **(High) Field notes are a personal lab notebook.** Sections titled "Field-tested notes
   (learned the hard way on the 2026-07-12 run)" / "(2026-07-15/16 run)" reference sessions
   the reader never had. The lessons are valuable; the framing is not durable guidance.
3. **(High) Shipped guidance is contradicted by current evidence.** From the 2026-07-24 run:
   - Both skills say `run_autofocus` fails ("method not found") and to "proceed on boot
     focus". In fact **every `goto_target` triggers a full `Initialise` → `3PPA` sequence
     that includes its own `AutoFocus`** (device `setting.auto_3ppa_calib: true`; observed
     `AutoFocus state: complete`, focuser → 1536). Focus **is** re-established per target.
     `run-session` Phase 2 ("run autofocus, record the baseline") is therefore wrong as the
     normal path.
   - That acquisition sequence costs **~2–4 min per goto** (observed 04:35:28 goto →
     04:39:36 stack start). No skill mentions it, so **slot budgeting is systematically
     optimistic** — a "45-min slot" yields ~42 min of integration.
   - The weather guardrail correctly returned `park_and_stop` **while the live sky was still
     dropping zero frames** (forecast precip + 1.6 °C temp–dewpoint spread). Nothing explains
     that hard stops act on **forecast lead time**, so a new user will read a correct safety
     stop as a false positive and override it. This is a safety-relevant documentation gap.
4. **(Medium) Undeclared dependencies.** The skills name ~33 MCP tools. Without
   `seestar-mcp` registered, `seestar_alp` running, `SEESTAR_ALPACA_DEVICE_NUM=1`, and the
   user's own firmware-7.18+ RSA interop key, the skills confidently call tools that do not
   exist. There is no prerequisites or version-compatibility statement anywhere.
5. **(Medium) Safety framing too thin for public release.** These skills command real motion
   and can run unattended all night on a stranger's hardware. The confirmation gate is good,
   but there is no top-level contract stating what runs autonomously, what never happens
   without confirmation, the known failure modes (park strands pointing; dew), or a plain
   responsibility disclaimer + licensing note.
6. **(Medium) Single-rig assumptions:** alt-az assumed throughout (EQ users get contradictory
   advice), high-latitude summer twilight, Windows/SMB `EMMC Images` specifics, and a
   "5 GHz is flaky on the S50" claim presented as fact from one unit.
7. **(Low, concrete) `qa-policy` doc drift.** Its "Reading a `qa_session_report`" section
   points the keep-list at "a **Siril** re-stack with `OSC_Preprocessing_WithoutDBF.ssf`",
   but this project's documented backends are DeepSkyStacker (default), pystack, and
   PixInsight. It sends readers to a tool the project does not use.

**Nuance to preserve:** `run_autofocus` **is** a registered MCP tool. It is the *firmware
method* that may return "method not found". No skill may claim the tool is absent.

## Design principle — three tiers of knowledge

Every claim in a skill is sorted into one of three tiers, and each tier has one home:

| Tier | Definition | Example | Home |
|---|---|---|---|
| **Universal** | True of every S50 on current firmware | alt-az field rotation; `park` strands pointing; never stack an unsolved field; goto runs 3PPA+autofocus | stated as fact in `SKILL.md` |
| **Conditional** | Varies by rig / site / latitude | pointing offset; local obstructions; twilight length; Wi-Fi band | rewritten as a **diagnostic procedure**, never an assumed fact |
| **Personal** | This unit's measured values | *this* ~20–30′ offset; *this* NE roofline; <city>'s dark window | moved **out** to a rig profile the skills optionally consult |

This preserves the hard-won knowledge and upgrades it: a conclusion becomes the procedure
that produced it, so any reader derives their own correct answer. Site obstructions already
have a first-class home — `set_site_profile` + the learned horizon mask — so that knowledge
becomes **mask data**, not prose.

---

## Architecture

### Per-skill changes

**`run-session`** (largest change)
- **Phase 2 (Focus) — rewritten.** New normal path: focus is established by the
  `Initialise`/`3PPA` sequence that `goto_target` triggers; confirm it (focuser position via
  `get_focuser_position` / device state) rather than calling `run_autofocus` as a matter of
  course. `run_autofocus` is documented as *optional, for a mid-session refocus, and
  firmware-version-dependent* (the tool exists; the firmware method may not) — and never a
  blocker.
- **Phase 1 (Acquire)** — document the `Initialise` → `3PPA` → `AutoGoto` → `Stack`
  progression and its **~2–4 min** cost; give the reader the test that distinguishes a normal
  Initialise from a genuinely stuck AutoGoto (elapsed time **and** no solve progress **and**
  zero frames), so they don't abort a healthy acquisition.
- **Framing check — rewritten as a measurement procedure.** How to measure your own offset
  (`Stack.Annotate` `pixelx/pixely` against the 1080×1920 frame centre 540/960); how to
  classify it (**systematic** iff it persists across different sky angles *and* survives a
  re-level + fresh dark 3PPA; **fixable** otherwise); what to do in each case; and where to
  record the result (rig profile). No asserted magnitude.
- **Transfer warning generalized** — any heavy transfer off the scope's share starves the
  control link; Windows/SMB specifics demoted to a parenthetical.
- **Slot budgeting** note referencing the acquisition overhead.

**`autonomous-night`**
- Stops duplicating the offset claim; defers to `run-session`'s diagnostic.
- **New "Guardrail semantics" section:** hard stops act on **forecast lead time**, so a
  weather stop can fire while the current sky is still clean — that is correct behavior and
  must not be overridden; the lead time is what lets the mount fold *before* precip/dew
  arrives. Cite the observable signals (precip forecast, temp–dewpoint spread) without
  site-specific numbers.
- Slot packing accounts for per-target acquisition overhead.
- Dated war-stories → **topic-organized operating notes** (undated, impersonal).
- Adds the **long-slot pacing pattern** (how to run a 45-min slot without polling every
  minute: watch stacked/dropped counters, exit at the slot boundary or on a drop-spike /
  link fault), described generically — no scratchpad script paths.

**`anomaly-playbook`**
- **New symptom: "goto looks stuck but is a normal Initialise/3PPA"** — the most likely
  new-user misread, with the discriminator from `run-session` Phase 1.
- **New symptom: bridge authentication fails on first connect, then self-recovers** on the
  heartbeat retry (observed); when it does *not* recover, suspect the phone app holding the
  scope's single control channel, or a transfer starving the link.
- 5 GHz claim softened from asserted fact to conditional advice ("if the link is unstable,
  try 2.4 GHz").
- Autofocus branch reconciled with the new focus reality.

**`observing-planner`**
- Generalize the latitude caveat: at high latitude in summer, astronomical dark is short or
  absent; put dual-band/emission targets in twilight and broadband in true dark. No
  site-specific times.

**`qa-policy`**
- Fix the **Siril doc-drift** (finding 7): point the keep-list at this project's actual
  backends (DeepSkyStacker default / pystack / PixInsight) via the `image-refinement` skill,
  rather than naming an unused tool and script.
- Otherwise audited against the three tiers; thresholds are session-relative and already
  universal. Any further change is reported, not assumed.

### New files

**`skills/README.md`** — the bundle document:
- What this is; the **skill graph** (`observing-planner` plans → `run-session` executes →
  `anomaly-playbook` on faults → `qa-policy` scores; `autonomous-night` orchestrates all four).
- **Prerequisites:** `seestar-mcp` registered in Claude Code; `seestar_alp` running and
  reachable; `SEESTAR_ALPACA_DEVICE_NUM=1`; user-supplied firmware-7.18+ RSA interop key
  (this project ships none — §1201(f) note); "save each frame in enhancing" ON for Tier-2 QA.
- **Compatibility statement:** validated against Seestar S50, alt-az, station mode, firmware
  7.75, `seestar-mcp` 0.1.0 — with a note that firmware changes are the expected breakage
  vector.
- **Safety contract:** what runs unattended; what *always* requires explicit confirmation
  (all motion, dew heater, mask edits, park/shutdown); the five hard stops and that each ends
  in `park`; the park-strands-pointing consequence; and a plain "your hardware, your sky,
  your responsibility — supervise the first runs" disclaimer.
- **Licensing/attribution:** repo MIT; `seestar_alp` GPL-3.0, external, never bundled; no ZWO
  key or firmware material is distributed.
- How to report a rig quirk or firmware change back.

**`docs/RIG-PROFILE.md`** — a fill-in template for per-unit, per-site facts: measured
pointing offset (value + how it was classified), obstructed bearings (and a pointer to the
learned mask as the better home), mount mode, network band, dark-window notes, and firmware
version. Placeholders only.

**Consultation mechanism (explicit).** `run-session` Phase 0 (pre-flight) instructs: *if
`docs/RIG-PROFILE.md` exists, read it once at session start and treat its contents as
observations about **this** unit; if it does not exist, proceed and run the relevant
diagnostic when the symptom appears.* The file is optional — every skill must work fully
without it. No other skill reads it directly; `autonomous-night` inherits it through
`run-session`.

### Privacy constraint (hard requirement)

The author's site profile contains **home coordinates** (lat/lon) and the device dump
exposes a Wi-Fi SSID. **No real coordinates, SSIDs, hostnames, IPs, local filesystem paths,
or API keys may appear** in `skills/`, `skills/README.md`, or `docs/RIG-PROFILE.md`. The
measured offset appears only anonymized, e.g. "one reference S50 measured ~20–30′
frame-left". Real values stay in gitignored local data.

## Verification

No `src/` changes, so the repo gate is unchanged and must stay green: `uv run pytest`
(1 pre-existing environmental failure in `test_check_backends_controller` — `pixinsight-mcp`
is configured on this machine while the test hard-codes its absence; it fails identically on
`main`) and `uv run ruff check src tests`.

Three review passes specific to this work:
1. **Portability audit** — every remaining declarative claim is Universal tier; no N=1
   assertion, no dated war-story framing, no personal data (grep for coordinates, SSIDs,
   `C:\`, UNC paths, IPs, the author's name).
2. **Tool-name consistency audit** — mechanically extract every MCP tool named across the
   five skills and assert each exists among the 33 registered in `src/seestar_mcp/server.py`.
   This catches run-book/tool drift, the primary way run-books rot.
3. **Fresh-reader audit** — a reader with a new S50 and no repo context can follow
   README → prerequisites → plan → run → wind down without hidden knowledge.

## Out of scope

`image-refinement` and `astro-processing` (heavier external deps — DeepSkyStacker/PixInsight;
separate release), Claude Code plugin packaging, an EQ-mode run-book, non-S50 variants, and
any change to `src/`.

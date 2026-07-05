---
name: image-refinement
description: >
  Turn a session's QA keep-list into a finished image: stack the good subs into a
  master and refine them. Use whenever the user wants to process/refine a session —
  e.g. "stack my subs", "process the session", "refine the images", "make a final
  image", "create the final photo", "give me a finished picture of M27". Refines ONLY
  the QA keep-list (never rejected subs), stacks with DeepSkyStacker (default,
  always-available → master + auto-stretched preview) or, if the user has PixInsight,
  via WBPP + a hand-off to their external pixinsight-mcp for a quality-gated finish.
  Runs on the seestar-refine MCP service; every external run is provenance-logged.
---

# Image Refinement

This skill turns "make the final image" into an auditable run: keep-list → stacked
master → finished picture. It runs on the **`seestar-refine`** MCP service (separate
from `seestar-mcp`, on the processing/4090 host). Two backends, chosen by
availability and the user's wishes:

- **DeepSkyStacker (DSS)** — the default, always-available path. Registers +
  integrates the keep-list into a master and an auto-stretched PNG preview. Complete
  for DSS-only users.
- **PixInsight** — the optional full finish (only if installed): stack via WBPP, then
  hand the master to the user's **external `pixinsight-mcp`** server for its
  quality-gated creative processing → a publication-ready image.

Keep output tight and phone-friendly (Remote Control): lead with state, one-line
status for the long stacking run, then the result.

## Phase 0 — Inputs

1. **Confirm the target and its QA keep-list.** The keep-list comes from
   `qa_session_report` (the `keep_list` in its output) — see the **`qa-policy`** skill
   for how it's decided. Refine **only the keep-list.** **Never** stack rejected subs.
   If there is no keep-list for the target, say so and point back to
   `qa_session_report` (`run-session` captures the subs it scores).
2. **Call `check_backends`.** State plainly what's available on this host, e.g.:
   `Backends: DSS ready · PixInsight found · pixinsight-mcp bridge reachable.`
   `Backends: DSS ready · PixInsight not configured — DSS finish only.`
   The report has `dss`, `pixinsight`, and `pixinsight_mcp` (the external bridge) plus
   `notes` — quote it; do not assume a backend is present.

## Phase 1 — Stack

1. Call **`stack_keep_list(target, engine=...)`**.
2. **DSS is the default and always-available.** Use `engine="dss"` (or `"auto"`) unless
   **both** PixInsight is available **and** the user has asked for the full PixInsight
   finish — only then use `engine="wbpp"`.
3. **State the engine and stacking params** in one line before/at kick-off, e.g.:
   `Stacking M27 · DSS · register + integrate, kappa-sigma rejection · 137 subs.`
   Seestar OSC subs are **pre-calibrated** (the scope builds/applies its own darks), so
   the default is **register + integrate only** — no darks/flats.
4. **Stacking is a long external process** (minutes for N×12 MB subs). Give **one**
   status line and wait for the result — do not spam progress. On a structured error
   (e.g. DSS not configured, empty keep-list), surface the error and stop; don't retry
   blindly.

## Phase 2 — Finish

### DSS path (default)

- A successful `stack_keep_list(dss)` already produced a **`preview_path`** (an
  auto-stretched PNG) next to the master. **Present the PNG preview and say where the
  master is** (`master_path`).
- If the user wants a different look, offer **`stretch_master(master_path, params?)`**
  with different `black_point_sigma` / `midtone` — regenerate the preview and show it.
- That completes the DSS finish. Done.

### PixInsight path (only if available)

Only when PixInsight is present **and** the user chose the full finish:

1. **`prepare_pixinsight_handoff(master_path, target)`** — writes the
   `<target>_pixinsight.json` config (target + absolute channel paths + output dir) and,
   if the optional `xisf` package is installed, an `.xisf` copy of the master (else it
   degrades to the FITS master — documented fallback). This does **not** run PixInsight.
2. **Drive the EXTERNAL `pixinsight-mcp`** tools (or its `giga-run` orchestrator) with
   that config to produce the quality-gated finished XISF + JPG, then **present the
   result** (final image + where it landed).
3. **If the external `pixinsight-mcp` bridge is unreachable, say so plainly and fall
   back to the DSS master + preview** — run `stretch_master` on the master and present
   that. Never leave the user with nothing.

## Hard rules

- **Keep-list only.** Stack the QA keep-list and nothing else — never a rejected sub.
- **Always state the backend + params used** (engine, rejection, register/integrate,
  sub count). No silent choices.
- **The DSS-vs-PixInsight choice is the USER's.** Offer the best available path, but do
  **NOT** silently launch a long PixInsight run — confirm before kicking off a heavy
  WBPP / pixinsight-mcp finish. DSS is the safe default.
- **Fall back to DSS** whenever PixInsight or its bridge is unavailable or fails —
  present the DSS master + preview rather than failing the request.
- **Every external invocation is provenance-logged** by the service
  (`refine_provenance.jsonl`); don't work around that.
- **A PixInsight finish needs the user's own install.** It requires PixInsight 1.8.9+
  **and** the external `pixinsight-mcp` server running — that server is
  **macOS-tested; Windows is unverified.** Set that expectation before promising a
  PixInsight finish; the DSS master + preview is guaranteed, the PixInsight finish is not.

## Cross-references

- **`qa-policy`** — how the keep-list (PASS/MARGINAL/REJECT) is decided; this skill
  consumes that keep-list.
- **`run-session`** — captures and QA-scores the session whose subs this skill refines.

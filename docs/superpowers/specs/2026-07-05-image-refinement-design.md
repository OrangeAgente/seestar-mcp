# Image Refinement (DSS + PixInsight) — Design Spec

**Date:** 2026-07-05
**Project:** seestar-mcp (this repo; new sibling MCP service `seestar-refine`)
**Status:** Approved direction (from brainstorming) — ready for implementation planning
**Builds on:** the QA keep-list produced by `qa_session_report` (Phase-1 Tier-2 QA).

---

## Goal

Take the **QA keep-list** (the good subs from a session) and turn it into finished outputs:
**stack** the kept subs and **refine** them. Two backends, chosen by availability:

- **DeepSkyStacker (DSS)** — the always-available path (Windows). Registers + integrates the
  keep-list into a master + an auto-stretched preview. Complete for DSS-only users.
- **PixInsight** — the optional full-finish path, used **if available**: stack the keep-list
  via PixInsight WBPP, then hand the master to the user's external
  [`pixinsight-mcp`](https://github.com/aescaffre/pixinsight-mcp) server for its quality-gated
  creative processing → a publication-ready image.

An `image-refinement` skill orchestrates: it refines **only the keep-list**, picks the best
available backend (or the user's choice), and never touches rejected subs.

## Host & scope

**Everything runs on one machine — the Windows / RTX-4090 box.** There is no Jetson role for
this subsystem; the earlier Jetson/Remote-Control framing (for telescope control) does not
apply here. DSS is Windows-native; PixInsight and `pixinsight-mcp` (if used) run on the same
box.

## What this project builds vs. what the user provides

- **We build & test:** the `seestar-refine` MCP service (DSS stacking + preview + handoff prep
  + a WBPP-runner invocation) and the `image-refinement` skill.
- **The user provides at runtime:** DeepSkyStacker installed (its CLI on `PATH`/configured);
  and — for the PixInsight path — PixInsight 1.8.9+ (with BlurX/NoiseX/StarX) plus the external
  `pixinsight-mcp` server installed and its watcher running. **`pixinsight-mcp` is macOS-tested;
  Windows support is unverified — that integration risk is the user's** (they chose that server).
  The skill orchestrates `pixinsight-mcp`'s tools *if they are reachable*; it does not vendor it.

## Design principles (inherited)

MCP = access/compute, Skills = judgment. Never-raise on tool paths → `{"ok": false, "error"}`;
provenance-log every external invocation (command + inputs + output paths; no secrets);
reason/­status-tagged results; deterministic-where-possible pure cores (file-list building,
keep-list parsing, stretch) unit-tested with mocked subprocesses / synthetic FITS. Real DSS /
PixInsight are desktop apps — never invoked in tests.

---

## Architecture

New package `src/seestar_refine/` — a **separate FastMCP server** in this repo, run as
`python -m seestar_refine.server` and registered separately in Claude Code (only on the
processing host). Separate from `seestar-mcp` because refinement is a distinct concern with
external desktop-app dependencies; it shares the repo, `uv` env, tests, and reads the QA
keep-list + subs from the shared data dir.

| File | Responsibility |
|---|---|
| `config.py` | `SEESTAR_REFINE_`-prefixed settings: DSS CLI path, PixInsight exe path, data/keep-list/output dirs, stacking params (rejection algo, alignment), stretch params. |
| `keeplist.py` | Parse a `qa_session_report` output (or a keep-list JSON) → `(target, [absolute FITS sub paths])`. Pure. |
| `dss.py` | Build a DSS file list from the keep-list; invoke `DeepSkyStackerCL`; locate the autosave master. Subprocess isolated behind one function. |
| `wbpp.py` | Build a PixInsight WBPP invocation (a bundled PJSR runner script + params) to stack the keep-list into a master. Best-effort; requires PixInsight. Subprocess isolated. |
| `preview.py` | Auto-stretch a master FITS/TIFF (asinh / midtone-transfer-function) → 8-bit PNG. Pure (numpy/astropy + Pillow). |
| `handoff.py` | Convert a master → XISF (+ write the JSON config `pixinsight-mcp` expects: target, absolute channel paths, output dir). XISF via the optional `xisf` package; falls back to a documented FITS + config if unavailable. |
| `backends.py` | Detect availability: is DSS CLI present? is PixInsight present? is `pixinsight-mcp`'s bridge reachable? → a capability report. Pure-ish (filesystem/env checks). |
| `server.py` | FastMCP tools + provenance. |

### Data models
```python
@dataclass
class StackResult:
    ok: bool
    engine: str                 # "dss" | "wbpp"
    target: str
    n_subs: int                 # kept subs stacked
    master_path: str | None
    preview_path: str | None
    stats: dict                 # min/median/max, dimensions
    log: str                    # tail of the tool's own output
    error: str | None = None
```

### MCP tools (`seestar-refine`)
| Tool | Purpose |
|---|---|
| `check_backends()` | Report what's available: DSS CLI, PixInsight, `pixinsight-mcp` bridge — so the skill picks a path. Read-only. |
| `stack_keep_list(target, engine="auto")` | Stack the target's keep-list. `engine`: `dss` (default/always), `wbpp` (PixInsight, if available), or `auto` (wbpp when a PixInsight finish is intended, else dss). Returns a `StackResult` (master + preview for dss). SIDE EFFECT: runs a long external process + writes files. |
| `stretch_master(master_path, params?)` | Auto-stretch a master → PNG preview; return the path + stats. |
| `prepare_pixinsight_handoff(master_path, target)` | Convert the master → XISF and write the `pixinsight-mcp` JSON config; return the config path + channel paths for the skill to pass to the external server. |
| `list_masters()` | List masters/previews produced under the output dir. Read-only. |

The **creative PixInsight finish itself is not a tool here** — the skill calls the *external*
`pixinsight-mcp` server's tools (or its `giga-run` orchestrator) with the handoff config.

### The `image-refinement` skill
`skills/image-refinement/SKILL.md` — the refinement run-book. Flow:
- **Phase 0 — inputs:** confirm the target + its QA keep-list (`qa_session_report` output).
  Refine **only the keep-list**; never rejected subs. `check_backends` to see what's available.
- **Phase 1 — stack:** `stack_keep_list`. DSS is the default and always-available; if PixInsight
  is available AND the user wants the full finish, stack via WBPP instead. State the engine +
  stacking params used.
- **Phase 2 — finish:**
  - **DSS path:** `stretch_master` → present the PNG preview + where the master is. Done.
  - **PixInsight path (if available):** `prepare_pixinsight_handoff` → drive the external
    `pixinsight-mcp` tools/runner with the config → present the finished XISF + JPG. If
    `pixinsight-mcp` is unreachable, say so and fall back to the DSS master + preview.
- **Hard rules:** keep-list only; state the backend + params; the DSS/PixInsight choice is the
  user's (offer the best available, don't silently pick a heavy PixInsight run); every external
  invocation is provenance-logged; long runs get a compact status line.

## Data flow
```
qa_session_report keep-list
  → stack_keep_list (DSS register+integrate  |  WBPP for PixInsight owners)
  → master
      ├─ DSS path:        stretch_master → PNG preview  (done)
      └─ PixInsight path: prepare_pixinsight_handoff → external pixinsight-mcp → finished XISF+JPG
```

## Error handling
- **DSS not installed / not configured** → `check_backends` reports it; `stack_keep_list(dss)`
  returns `{"ok": false, "error": "DeepSkyStackerCL not found — set SEESTAR_REFINE_DSS_CLI"}`.
- **PixInsight / `pixinsight-mcp` unavailable** → the PixInsight path is simply not offered; the
  skill falls back to DSS. `stack_keep_list(wbpp)` without PixInsight → structured error.
- **Stacking fails / times out** → structured error with the tail of DSS/PixInsight's own output
  (stacking N×12 MB subs takes minutes — generous timeout, surfaced as a status line).
- **Empty / missing keep-list** → clear error (run `qa_session_report` first).
- Calibration: Seestar OSC subs are already calibrated (the scope builds/applies darks), so the
  default is **register + integrate only** (no dark/flat). Optional dark/flat lists are supported
  but off by default; documented.
- Never raises out of a tool; the pure cores (file-list build, keep-list parse, stretch) never
  raise on bad input.

## Testing
- `keeplist.py`: parse a sample `qa_session_report` keep-list → correct absolute paths; missing
  file → error object.
- `dss.py`: **mock the subprocess** — assert the DSS file list content + the exact command line
  are built correctly; assert output-master location logic; a non-zero exit → structured error.
- `wbpp.py`: mock the subprocess — assert the WBPP runner command/params; unavailable PixInsight
  → structured error. (Not run against real PixInsight in CI.)
- `preview.py`: stretch a synthetic FITS (gradient + a few Gaussians) → a valid PNG of expected
  size; deterministic.
- `handoff.py`: write a config from a sample master → valid JSON with absolute paths; XISF
  conversion tested only if the `xisf` dep is present (round-trip a small array), else the
  FITS-fallback path is asserted.
- `backends.py`: availability logic against a temp dir (present/absent CLI) → correct report.
- tools: the `seestar-refine` server registers the expected tools; `stack_keep_list` with a
  mocked `dss.run` returns a `StackResult`.
- `image-refinement/SKILL.md` frontmatter valid; contains the keep-list-only + backend-choice
  rules. No real DSS/PixInsight, no network in tests.

## Dependencies
- Reuse `astropy`/`numpy`. Add **`Pillow`** (PNG preview writing) — small, pure-ish, widely used.
- **`xisf`** (PyPI, pure-Python XISF read/write) is **optional** — added only for the handoff
  conversion; `handoff.py` degrades to a FITS master + config if it's absent. If adding it
  complicates the lock, ship FITS-only handoff in v1 and note XISF as a follow-up.
- No new *heavy* runtime deps; the real engines (DSS, PixInsight) are external desktop apps.

## Security / reproducibility
- Refinement shells out to desktop apps — tools validate the configured CLI paths, never accept
  an arbitrary executable from tool args, and provenance-log each invocation (command, input
  keep-list, output paths; no secrets). Outputs go under a configured `output/` dir.
- No new network host (the external `pixinsight-mcp` bridge is file-based/local). No secrets.
- Pure cores deterministic + unit-tested; external runs are isolated behind one function each so
  the logic around them is fully testable without the apps.

## Completion

With this, the Seestar workflow is end-to-end: **plan → capture → QA → refine.** The QA
keep-list flows straight into a DSS master + preview for everyone, and into a PixInsight
quality-gated finish for owners — all orchestrated by Claude on the one machine.

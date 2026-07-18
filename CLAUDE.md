# CLAUDE.md — working in seestar-mcp

Guidance for Claude Code (and humans) working on this repo. Read this before making changes.

## What this is

An **auditable MCP server + Claude Code Skills** to control a ZWO Seestar S50 smart telescope
(via `seestar_alp`'s ASCOM Alpaca API) and run two-tier data-quality QA on its FITS output,
plus a full **observing planner** (conditions verdict, ranked targets, projects/history,
learned horizon mask, autonomous night). Built for high-assurance / air-gapped-adjacent use:
provenance-logged, hash-pinned deps, least-privilege, human-in-the-loop for anything
destructive. Runs on a Jetson, driven from the Claude phone app via Remote Control.

## Toolchain — `uv` only

There is **no bare `python`** on the dev machine (Windows Store shim). Always:

```bash
uv run pytest            # full suite (currently ~214 tests, all green)
uv run ruff check src tests
uv run python -m seestar_mcp.server   # launch the MCP server (stdio)
uv run python -c "..."   # one-off checks
```

No new dependencies without strong justification — the astronomy is `astropy` (already
pinned), weather is `httpx`, FITS QA is `photutils`/`numpy`. Deps are exact-pinned and
hash-locked in `uv.lock`; re-run `uv lock` if you touch `pyproject.toml`.

## Architecture — MCP = access, Skills = procedure

- **MCP tools** (`src/seestar_mcp/server.py`) = access + reproducible computation. 33
  single-purpose tools on one `SeestarController`. Each returns a JSON dict with `"ok"`,
  catches errors → `{"ok": false, "error": ...}`, and is provenance-logged. Motion/destructive
  tools have honest `SIDE EFFECT` descriptions.
- **Skills** (`skills/*/SKILL.md`) = judgment. `run-session`, `qa-policy`, `anomaly-playbook`,
  `observing-planner`, `autonomous-night`. Skills decide *whether/what*; tools *do*.
- **Modules:**
  - `alpaca_client.py` — async ASCOM Alpaca client + the `method_sync` action tunnel.
  - `data_client.py` — list/download FITS subs (HTTP + SMB fallback), path-traversal-hardened.
  - `qa_tier1.py` — firmware telemetry health signals (never a quality verdict).
  - `qa_tier2.py` — photutils per-sub grading (FWHM/HFR/ecc/SNR/background/star-count/wFWHM/
    `scattered_light`) → PASS/MARGINAL/REJECT, session-relative, reason-tagged.
  - `planning/` — `astro.py` (deterministic ephemeris + field-rotation sweet-band), `catalog.py`
    (120 DSOs), `site.py`, `weather.py` (Open-Meteo), `lightpollution.py`, `ranker.py`,
    `projects.py`, `obstructions.py` (learned mask), `autonomous.py` (guardrails + scheduler).
  - `config.py` (pydantic-settings, `SEESTAR_` env prefix), `provenance.py`, `secrets.py`.

## Non-negotiable conventions

- **Determinism:** nothing in `planning/` reads the clock. Astronomy/scoring take an injected
  `now_utc`/`when_utc`; only the *tool layer* resolves "tonight" via `datetime.now(timezone.utc)`.
  This keeps tests stable — preserve it.
- **Never-raise on tool paths.** `analyze_sub`, guardrail/observability cores, and all
  controller methods degrade to error-tagged results, never exceptions.
- **No secrets in config/source/logs.** `provenance.py` redacts; `secrets.py` loads on demand.
- **Reason-tagged verdicts.** Every QA verdict, conditions call, target score, guardrail stop,
  and obstruction suggestion carries human-readable `reasons` — "no unexplained rejects."
- **Backward compatibility:** additive optional params (e.g. `rank_targets(projects=None)`,
  `SubMetrics.scattered_light=None`) must reproduce prior behavior exactly — keep the regression
  tests that pin this.
- **Human-in-the-loop for irreversible things:** motion/destructive tools and mask edits
  (`add_horizon_mask`) are only invoked after explicit user confirmation, per the skills.

## Gotchas

- **Alpaca device number is `1`, not 0** (`SEESTAR_ALPACA_DEVICE_NUM=1`). seestar_alp registers
  the scope at the number in its `config.toml` (shipped example = 1). Verified on hardware.
- **Firmware 7.18+ needs the RSA interop key.** The scope silently ignores unauthenticated
  commands. `seestar_alp` handles the handshake if its `interop_pem` points at a
  `seestar_client_key.pem` the user supplies (extracted from the ZWO APK under §1201(f)). This
  server talks *through* seestar_alp, so it never handles the key itself.
- **`# FIRMWARE-DEPENDENT` (unvalidated against hardware — isolated to single helpers):** the
  GPS key in `_parse_gps`, the battery key in `_parse_device_health` (both in `server.py`), and
  the sub-listing method `get_img_file_list` in `data_client.py`. Validate + correct these when
  next on real hardware. The real `get_view_state` structure IS confirmed (fw 7.75):
  `result.View.Stack.stacked_frame` / `dropped_frame`.
- **Line endings:** commit with `git -c core.autocrlf=false commit`. Commit diff stats can look
  inflated (CRLF↔LF renormalization) — the content diff is what matters; tests are the gate.
- **Field rotation (alt-az):** rank on *sweet-band* time `[min_alt, ~60° ceiling]`, NOT raw
  altitude — rotation is worst near the zenith. See `astro.py` / `ranker.py`.

## Testing

TDD, subagent-driven. All tests run offline: hardware (alpaca/device state), weather
(Open-Meteo via `respx`), and GPS are mocked; astronomy uses fixed timestamps; FITS QA uses
seeded fixtures (`tests/fixtures/*.fits`, incl. `good`/`bad_ecc`/`bad_snr`/`hazy`). No test
touches the network or real hardware.

## Branching & done

Branch for any non-trivial change (never commit to `main`); keep each branch one
coherent concern; merge promptly and delete. "Done" = `uv run pytest` green **and**
`uv run ruff check src tests` clean — never claim done without them.

## Deploy / run

- MCP transport is **stdio** — no inbound socket. Register BEFORE starting a Remote Control
  session (can't add MCP mid-session): `claude mcp add seestar-mcp -- uv --directory <repo> run
  python -m seestar_mcp.server`.
- Startup order and the hardened Jetson systemd unit (`deploy/seestar-mcp.service`) are in
  `README.md` / `SECURITY.md`.

## Where the design lives

Every feature has a spec + plan under `docs/superpowers/specs/` and `docs/superpowers/plans/`
(observing planner P1-P3, learned horizon mask, scattered-light metric). Read the relevant
spec before changing a subsystem.

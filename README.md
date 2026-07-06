# seestar-mcp

An auditable [Model Context Protocol](https://modelcontextprotocol.io) server plus a set of
Claude Code Skills that let you control a **ZWO Seestar S50** smart telescope and run
data-quality assurance on its astrophotography output. The server wraps
[`seestar_alp`](https://github.com/smart-underworld/seestar_alp)'s ASCOM Alpaca HTTP API,
pulls RAW FITS subs off the device, and scores them with a two-tier QA pipeline. It is
built for high-assurance / air-gapped-adjacent operation: every tool call is written to an
append-only provenance log, dependencies are hash-pinned, and the daemon runs sandboxed and
least-privilege. It is designed to run 24/7 on an **NVIDIA Jetson** host and is driven from
the **Claude phone app via Claude Code Remote Control** — the Jetson is the brain; the phone
is just a window into the local session.

## Status & limitations

**Alpha (0.1.0).** The core has been exercised against real hardware — a full live M27
session end-to-end (goto → autofocus → plate-solve → stack → park) and a 766-sub M31 deep
stack through DeepSkyStacker — but several code paths are **not yet hardware-validated** and
are flagged `# FIRMWARE-DEPENDENT` in the source (single, well-marked update points):

- the GPS key in `_parse_gps` and the battery key in `_parse_device_health`;
- the on-device sub-listing method (`get_img_file_list`) used by `list_subs`;
- the PixInsight/WBPP refinement path on Windows (the external `pixinsight-mcp` is
  macOS-tested; the DSS path is the validated default).

Firmware changes are the expected breakage vector; these constants are isolated so a bump is
a one-line swap. Treat unvalidated paths as needing a first-light check against your own
scope. The field-rotation **autocrop** is a brightness heuristic and cannot pixel-perfectly
excise the sheared border on a smooth sky (a coverage-map crop is a future enhancement).

## Prerequisites

- **[`seestar_alp`](https://github.com/smart-underworld/seestar_alp)** running and reachable
  at `http://127.0.0.1:5555` (it owns the device handshake; GPL-3.0, installed separately).
- A **user-supplied firmware interop key** for Seestar firmware 7.18+ (you extract it from
  your own licensed ZWO app under 17 U.S.C. §1201(f); this project ships none — see
  [Legal & trademarks](#legal--trademarks)).
- **[DeepSkyStacker](https://deepskystacker.free.fr)** (`DeepSkyStackerCL`) on the processing
  host for the `seestar-refine` service.
- *Optional:* **PixInsight** + the external
  **[`pixinsight-mcp`](https://github.com/aescaffre/pixinsight-mcp)** for the full-finish path.
- *Optional (hands-free data pull):* the SMB/filesystem backend requires a **machine-wide
  Windows SMB relaxation** — see the caveat under [Configuration](#configuration) and
  [`SECURITY.md`](SECURITY.md#data-access-the-smb--filesystem-backend-host-wide-caveat).

## Operating model — Remote Control

The phone never touches your LAN. It reaches the local Claude Code session only through
Anthropic's API; the Jetson reaches the Seestar over the LAN. This changes the security
posture and imposes hard constraints:

- **The Jetson makes outbound HTTPS only.** It polls Anthropic's API over TLS and opens
  **no inbound ports**. There is **no VPN, no SSH, no tmux, no port forwarding, and no
  inbound network exposure** to design for or harden. Do not add any of that plumbing.
- **MCP servers must be registered BEFORE the Remote Control session starts.** Remote
  Control preserves the full local context (files, env, and MCP servers), but you
  **cannot add a new MCP server mid-session from the phone**. If you add or change tools,
  re-register and restart Claude Code locally on the Jetson.
- All local ports (this server over stdio, `seestar_alp` on :5555) are bound to
  localhost/LAN only and must **never** be exposed publicly.

See [`SECURITY.md`](SECURITY.md) for the full threat model and OWASP MCP Top 10 controls.

## Startup order (order matters)

1. **On the Jetson: ensure `seestar_alp` is running** — its ASCOM Alpaca server must be up
   on `http://127.0.0.1:5555` (bound to localhost/LAN, never public).
2. **Register `seestar-mcp` in Claude Code** (stdio; no network port — see the exact line
   below).
3. **Start the Claude Code session and enable Remote Control** — run `claude`, then
   `/remote-control "Seestar"`, and pair the app via the QR code / URL once.
4. **Drive the session from the Claude app** — kick off a session, monitor stacking/QA, and
   approve motion / destructive actions as they are surfaced.

Because MCP cannot be added mid-session, treat "register before start" (steps 2 → 3) as a
hard sequence.

### Exact register line

The server speaks MCP over **stdio** — no network port is opened. Register it with:

```bash
# Linux / Jetson (the production host)
claude mcp add seestar-mcp -- uv --directory /path/to/SeeStar-AI run python -m seestar_mcp.server
```

On the Windows development machine, use the repo path here instead:

```bash
claude mcp add seestar-mcp -- uv --directory C:/path/to/SeeStar-AI run python -m seestar_mcp.server
```

## Install / dev

```bash
uv sync                                   # install from the hash-locked deps (uv.lock)
make test   # or: uv run pytest           # run the test suite
make run    # or: uv run python -m seestar_mcp.server   # launch the MCP server (stdio)
make lint   # or: uv run ruff check src tests
```

Never invoke a bare `python` — always go through `uv run` so the locked environment is used.

## Configuration

All settings are overridable via environment variables with the `SEESTAR_` prefix (e.g.
`SEESTAR_ALPACA_BASE_URL`). Key variables:

| Env var | Default | Purpose |
|---|---|---|
| `SEESTAR_ALPACA_BASE_URL` | `http://127.0.0.1:5555` | seestar_alp ASCOM Alpaca base URL (localhost only). |
| `SEESTAR_ALPACA_DEVICE_NUM` | `1` | Alpaca device number. seestar_alp registers the scope at the Alpaca number equal to its `config.toml` `device_num`; its shipped example uses `1`, so a standard single-scope install is device `1` (verified on real hardware). Override if your seestar_alp config numbers the scope differently. |
| `SEESTAR_SEESTAR_HOST` | `127.0.0.1` | Seestar LAN IP (station mode, DHCP reservation) for data pulls. |
| `SEESTAR_BIND_HOST` | `127.0.0.1` | Bind address. Defaults to localhost; a validator warns if set to a public/routable address. Never make it public. |
| `SEESTAR_DATA_DIR` | `./data` | Local data directory (downloaded subs, provenance log, manifests). |
| QA thresholds | see `config.py` | `SEESTAR_QA_FWHM_SIGMA`, `SEESTAR_QA_ECCENTRICITY_REJECT`, `SEESTAR_QA_SNR_FLOOR_FACTOR`, `SEESTAR_QA_STARCOUNT_FLOOR_FACTOR`, absolute overrides, etc. are all overridable. |

All binds default to **localhost** and must never be public. **Secrets never go in config.**
The firmware-7.18+ RSA key and any tokens live in `./secrets/` (gitignored) or in
`SEESTAR_SECRET_*` environment variables, loaded on demand by `secrets.py` and never written
to config, source, or the provenance log.

### Local state (all under `SEESTAR_DATA_DIR`, gitignored)

| File | Written by | Purpose |
|---|---|---|
| `provenance.jsonl` | every tool call | append-only audit log (redacted; no secrets). |
| `manifests/`, `reports/` | `qa_session_report` | per-session QA manifest + JSON/Markdown reports. |
| `site_profile.json` | `set_site_profile` / `add_horizon_mask` | observing site (lat/lon, Bortle, horizon mask, `location_tolerance_km`). |
| `projects.json` | `log_session_result` / `set_project_goal` | multi-night integration goals + session history. |
| `sky_failures.json` | `log_sky_result` | weather-gated (az,alt) plate-solve failure histogram for the learned horizon mask. |

QA thresholds (all `SEESTAR_QA_*`, see `config.py`) include the scattered-light metric
(`SEESTAR_QA_SCATTER_REJECT_SIGMA`, `SEESTAR_QA_SCATTER_MARGINAL_SIGMA`,
`SEESTAR_QA_SCATTER_ABSOLUTE`) alongside FWHM / eccentricity / SNR / star-count.

> **⚠️ Hands-free data pull (SMB) — host-wide caveat.** Setting `SEESTAR_SEESTAR_IMAGE_ROOT`
> to the Seestar's UNC share (e.g. `\\<seestar-ip>\EMMC Images\MyWorks`) lets `list_subs` /
> `download_subs` read subs directly off the device with no mapped drive. But the Seestar
> serves that share as an **unauthenticated guest**, which modern Windows blocks by default;
> enabling it needs an **elevated, machine-wide** `Set-SmbClientConfiguration
> -EnableInsecureGuestLogons $true` (revert with `$false`). This re-enables unsigned guest
> SMB for the **whole machine** — a real security downgrade. Prefer the default HTTP transport
> (no such change), keep the scope on an isolated VLAN if you do enable it, and revert when
> done. Full detail in [`SECURITY.md`](SECURITY.md#data-access-the-smb--filesystem-backend-host-wide-caveat).

## Tools (33)

The server exposes exactly 33 single-purpose, least-privilege tools with honest,
non-obfuscated descriptions. Destructive/motion tools are clearly labelled `SIDE EFFECT` in
their descriptions; Skills gate them behind explicit user confirmation.

### Control / state

| Tool | Description |
|---|---|
| `connect_telescope` | Connect to the Seestar via seestar_alp. No motion; safe anytime. |
| `get_status` | Read connection, RA/Dec pointing, and tracking/slewing state. Read-only. |
| `get_view_state` | Read the device's live view/stacking telemetry. Read-only. |
| `goto_target` | Slew to a target and open a new session (commands MOTION). |
| `start_stack` | Start live-stacking (begins capturing/integrating exposures). |
| `stop_view` | Stop the current view/stack (`Stack` or `ContinuousExposure`). |
| `run_autofocus` | Run the autofocus routine (moves the focuser). |
| `get_focuser_position` | Read the current focuser position. Read-only. |
| `plate_solve` | Plate-solve the current field and return the solution. |
| `set_filter` | Set the filter wheel position (LP / IR-Cut / Dark). |
| `set_dew_heater` | Turn the dew heater on/off (enabling INVALIDATES existing darks). |
| `park` | Park the mount (stops tracking, moves to park). |
| `shutdown` | Power down the Seestar (TERMINATES the seestar_alp control link). |

### Data

| Tool | Description |
|---|---|
| `list_subs` | List RAW FITS subs saved on the device (optionally one target). Read-only. |
| `download_subs` | Download RAW subs to local storage (HTTP, SMB fallback); files hashed into provenance. |

### QA

| Tool | Description |
|---|---|
| `qa_tier1` | Poll firmware telemetry once; return a snapshot + neutral health flags. Read-only. |
| `qa_tier2` | Score RAW subs PASS/MARGINAL/REJECT with per-sub reasons + keep-list. Read-only. |
| `qa_session_report` | Score subs, then WRITE a JSON+Markdown report and session manifest. |

### Planning

Local-computation tools for the pre-session observing planner (deterministic astropy
ephemeris + a bundled DSO catalog; only `assess_conditions` reaches the network, for
weather). Read-only except `set_site_profile`, which writes the site profile.

| Tool | Description |
|---|---|
| `get_site_profile` | Return the stored observing site profile (or note that none is set). Read-only. |
| `set_site_profile` | Create/update the site profile (lat/lon, elevation, Bortle, horizon mask, min altitude). |
| `assess_conditions` | Weather + moon + astronomical twilight → go/no-go verdict + reasons + dark window. |
| `get_target_observability` | Deep-dive one target → full observability (sweet-band time, transit, moon, framing) + recommended subs. Read-only. |
| `plan_targets` | Ranked, reasoned target shortlist with best windows and recommended integration. Read-only. |
| `simulate_night` | Dry-run tonight's autonomous plan (ordered target schedule) WITHOUT moving the scope. Read-only. |
| `check_night_guardrails` | Evaluate hard-stop conditions for an autonomous run (dawn, battery, weather, connection, max duration). Read-only. |
| `log_sky_result` | Record one plate-solve outcome, binned by (az, alt); weather-gated so cloudy failures never count as obstructions. |
| `suggest_horizon_mask` | Return learned obstruction arcs with evidence (nights, failure rate) for review. **Read-only — suggests, never applies.** |
| `add_horizon_mask` | Append one user-confirmed obstruction arc to the site profile's horizon mask (explicit user action). |

### Projects / history

Persistent projects/history store (`data/projects.json`, gitignored) so targets accumulate
integration across nights toward goals, repeats are avoided, and the planner can answer
"what needs more data?". Read-only except `set_project_goal` and `log_session_result`.

| Tool | Description |
|---|---|
| `list_projects` | List all projects with collected/goal integration and status. Read-only. |
| `get_project` | Return one project including its session history. Read-only. |
| `set_project_goal` | Create/update a project and its integration goal (minutes). |
| `log_session_result` | Record a completed session (integration, sub counts, median FWHM); accumulates toward the goal. |
| `recommend_projects` | Active projects that still need data, most-needed first. Read-only. |

## Observing planner

Set a site profile once (`set_site_profile` with your lat/lon and, if you know it,
Bortle), then ask to plan a night. `assess_conditions` gives a one-line go/no-go from
weather + moon + twilight, and `plan_targets` returns a ranked shortlist optimized for
*clean* alt-az data (field-rotation sweet-band time, light-pollution fit, moon
separation, FOV framing) — every score is reason-tagged. With no profile, the tools
fall back to the scope's GPS and a default Bortle. Weather is the only external call
(`api.open-meteo.com`, keyless) and a failure is non-fatal. The **`observing-planner`**
skill drives this flow and hands the chosen target to `run-session`.

The planner now also has **projects/history** (it boosts targets that still need data,
suppresses recently-imaged ones, and can recommend what to shoot next) and **live-operator
reactivity**: `run-session` consults the plan, watches conditions during a session, switches
target when one leaves its sweet band, and logs each session's result back into its project
at wind-down.

### Learned horizon mask

The horizon mask is **learned-and-confirmed**, not just hand-declared. `log_sky_result`
accumulates plate-solve outcomes binned by (azimuth, altitude) across nights, and
`suggest_horizon_mask` proposes obstruction arcs — but only when the evidence is
unambiguous: **weather-gated** (cloudy-sky failures are excluded), **cross-night** (a
bearing must fail on several distinct clear nights), **low-altitude** (obstructions are
near the horizon; a high blank is never a tree), and **GPS-checked** (records and the mask
are scoped to the site where they were recorded). It is **suggest-and-confirm**: the mask
is **never auto-applied** — the user reviews the evidence and confirms each arc via
`add_horizon_mask`. Every plan (`plan_targets` / `assess_conditions` / `simulate_night`)
returns a `location` block reconciling the scope's live GPS with the saved site; if the
scope has moved beyond tolerance the stale mask is **not applied** and the mismatch is
disclosed.

## Autonomous night

An **opt-in** unattended mode: hand over the whole night and let Claude run the ranked plan
target-by-target, react to conditions/QA, and park at dawn. It is **dry-run-first** —
`simulate_night` projects the ordered schedule with **no motion**, and an **explicit user
confirmation is required before the first motion command**. It is **guardrailed** —
`check_night_guardrails` is evaluated every loop iteration and any hard stop (dawn, low
battery, weather no-go, lost connection, max duration) **always ends in `park`**; if the
scope's health can't be confirmed, the run stops (fail safe). Autonomy adds no new network
host or inbound surface — it is the same audited tools driven in a visible loop. The
**`autonomous-night`** skill drives this flow (planning from `observing-planner`, execution
from `run-session`, faults from `anomaly-playbook`).

## Image refinement (`seestar-refine`)

Refinement runs as a **separate MCP service** in this repo — `seestar-refine` — on the
processing machine (the Windows / RTX-4090 box, where DeepSkyStacker and, optionally,
PixInsight are installed). It takes the QA **keep-list** (from `qa_session_report`) and
turns it into a finished image. It is a distinct concern with external desktop-app
dependencies, so it is its own FastMCP server, registered separately from `seestar-mcp`.

Three stacking backends, chosen by availability and the user's wish:

- **DeepSkyStacker (DSS)** — registers + integrates the keep-list into a master and an
  auto-stretched PNG preview. Complete on its own (when DSS is installed).
- **pystack** — a **pure-Python, DSS-free** backend (`astroalign` + numpy): debayer →
  star-triangle registration → memmap-bounded sigma-clipped integration → `(3,H,W)`
  master. **Cross-platform, no external app, and visually equivalent to DSS** on Seestar
  data (validated on 286 real M27 subs). Use `engine="pystack"`.
- **PixInsight** — the **optional full finish** (only if installed): stack via WBPP,
  then hand the master to the user's **external
  [`pixinsight-mcp`](https://github.com/aescaffre/pixinsight-mcp)** server for its
  quality-gated creative processing. That server is the user's own install
  (macOS-tested; Windows unverified) — this repo drives it if reachable, it does not
  vendor it.

### Register line

Like `seestar-mcp`, it speaks MCP over **stdio** — no network port. Register it on the
processing host **before** starting the Claude Code session:

```bash
claude mcp add seestar-refine -- uv --directory C:/path/to/SeeStar-AI run python -m seestar_refine.server
```

### Configuration

All settings are overridable via environment variables with the `SEESTAR_REFINE_`
prefix. It holds **no secrets** — only non-sensitive desktop-app paths and directories.

| Env var | Default | Purpose |
|---|---|---|
| `SEESTAR_REFINE_DSS_CLI` | `""` | Path to `DeepSkyStackerCL(.exe)`. Empty ⇒ DSS not available. |
| `SEESTAR_REFINE_PIXINSIGHT_EXE` | `""` | Path to `PixInsight(.exe)`. Empty ⇒ the PixInsight/WBPP path is not offered. |
| `SEESTAR_REFINE_DATA_DIR` | `./data` | Shared dir holding the QA reports + subs to refine. |
| `SEESTAR_REFINE_OUTPUT_DIR` | `./data/refine` | Where masters, previews, and `refine_provenance.jsonl` are written. |
| `SEESTAR_REFINE_REJECTION` | `kappa-sigma` | Pixel-rejection algorithm for stacking. |
| `SEESTAR_REFINE_ALIGNMENT` | `auto` | Star-alignment mode. |

Only configured executables are ever run; a configured-but-missing exe is refused. A
missing/empty path simply means that backend is unavailable (`check_backends` reports it).

### Tools (5)

| Tool | Description |
|---|---|
| `check_backends` | Report which backends are available: DSS CLI, **pystack** (astroalign), PixInsight, and the external `pixinsight-mcp` bridge. Read-only. |
| `stack_keep_list` | Stack a target's QA keep-list into a master. `engine`: `dss`/`auto`, **`pystack`** (pure-Python, no external app), or `wbpp` (PixInsight, if available). SIDE EFFECT: long process + writes files. |
| `stretch_master` | Auto-stretch a master into an 8-bit PNG preview via the AstroPipe pipeline (opt-in `params`: `gradient`, `white_balance`, `deconv`, `saturation`, `upscale`, plus stretch controls); return the path + stats. |
| `prepare_pixinsight_handoff` | Write the `pixinsight-mcp` JSON config (+ an XISF copy if `xisf` is installed) for the external server's creative finish. Does NOT run PixInsight. |
| `list_masters` | List masters/previews produced under the output dir. Read-only. |

DSS gives a **master + preview**; PixInsight is an **optional full finish** done by the
user's external `pixinsight-mcp`. The **`image-refinement`** skill orchestrates the flow
(keep-list only, backend + params stated, fallback if a backend is unreachable).

### AstroPipe — the pure-Python pipeline (no DSS/PixInsight required)

A DSS/PixInsight-free path built on the scientific-Python stack, exposed through
`stack_keep_list(engine="pystack")` + `stretch_master(params=…)`:

1. **Stack** (`pystack`) — debayer (GRBG) → `astroalign` registration → memmap-bounded
   sigma-clipped integration → `(3,H,W)` master. Validated visually equivalent to DSS.
2. **Gradient removal** — star-masked `photutils.Background2D` sky subtraction.
3. **Color calibration** — star-based white balance (neutral star color).
4. **Stretch** — percentile-white-point MTF (raise the black point on low-SNR data).
5. **Deconvolution** — gentle Richardson-Lucy (honest detail recovery; keep it light —
   aggressive settings ring around bright stars).
6. **Saturation** — chroma boost to match references.
7. **Upscale** *(opt-in)* — Lanczos by default ("no new detail"); an optional AI path is
   **provenance-labeled "AI-generated detail, not captured signal."**

Every stage is opt-in via `stretch_master` params and provenance-logged. The whole thing
runs cross-platform with no external desktop app.

## Skills

**MCP is the access layer; Skills are the procedure layer.** The MCP server gives Claude the
ability to reach the telescope, the FITS files, and the QA computations; the Skills encode
*how* to use them:

- **`run-session`** — the end-to-end session run-book (pre-flight, acquire, focus, stack,
  monitor, wind down).
- **`qa-policy`** — the scoring policy: what the QA metrics mean (FWHM, HFR, eccentricity,
  SNR, background, star count, wFWHM, and `scattered_light` — a bright-star-halo /
  background-non-uniformity metric that catches thin-cirrus veils slipping the SNR/star-count
  floors) and how to decide PASS / MARGINAL / REJECT.
- **`anomaly-playbook`** — the fault decision tree (clouds, dew, focus drift, tracking loss,
  connection drops).
- **`observing-planner`** — the pre-session planner: a go/no-go conditions verdict and a
  ranked, reasoned target shortlist (best window + recommended integration), then hands the
  chosen target to `run-session`.
- **`autonomous-night`** — the unattended full-night run-book: propose a no-motion plan
  (`simulate_night`), get one explicit go-ahead, then loop target-by-target under hard
  guardrails (`check_night_guardrails`) and park at dawn or on any hard stop.
- **`image-refinement`** — the post-session refinement run-book (on `seestar-refine`):
  stack the QA keep-list into a master + preview with DSS (default), or, for PixInsight
  owners, WBPP + a hand-off to the external `pixinsight-mcp` for a quality-gated finish.

Skills stay at roughly ~100 tokens of description until they are invoked, so they add almost
no standing context cost, while the MCP server is deliberately kept lean and single-purpose.
This keeps token usage low and the tool surface auditable.

## Security

See [`SECURITY.md`](SECURITY.md) for the threat model, the OWASP MCP Top 10 mitigation
table, the supply-chain runbook (`uv lock` / `make sbom` / `make scan`), and secrets
handling. The hardened systemd unit for the Jetson is in
[`deploy/seestar-mcp.service`](deploy/seestar-mcp.service).

## Legal & trademarks

**Not affiliated with ZWO.** "Seestar" and "ZWO" are trademarks of Suzhou ZWO Co., Ltd. This
project is unofficial and is **not** affiliated with, authorized, sponsored, or endorsed by
ZWO; those names are used only to identify the hardware this software interoperates with.

**Firmware interoperability key.** Seestar firmware 7.18+ requires an RSA handshake. This
project **ships no ZWO firmware, no ZWO application code, and no key**, and contains **no
tool to extract or circumvent any key** — it drives the external `seestar_alp`, which owns
the handshake. To use real 7.18+ hardware you supply **your own** key file, extracted by you
from **your own lawfully licensed** copy of the ZWO app for interoperability, as permitted
under the reverse-engineering provision of **17 U.S.C. §1201(f)** (and analogous provisions
elsewhere). That key is a user secret: it lives only in the gitignored `secrets/` dir or a
`SEESTAR_SECRET_*` variable and is never committed. You are responsible for compliance with
the laws of your own jurisdiction. See [`NOTICE`](NOTICE) for full third-party attribution.

Licensed under the [MIT License](LICENSE). It drives — but never bundles or redistributes —
`seestar_alp` (GPL-3.0, separate process), the external `pixinsight-mcp` (MIT), DeepSkyStacker
(freeware), and PixInsight (commercial); each remains under its own license.

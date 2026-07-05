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
claude mcp add seestar-mcp -- uv --directory C:/Users/<user>/SeeStar-AI run python -m seestar_mcp.server
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
| `SEESTAR_ALPACA_DEVICE_NUM` | `0` | Alpaca device index in URLs. Note: seestar_alp's `config.toml` numbers the first scope `1`, but the HTTP endpoints use `0` — do not "correct" this. |
| `SEESTAR_SEESTAR_HOST` | `127.0.0.1` | Seestar LAN IP (station mode, DHCP reservation) for data pulls. |
| `SEESTAR_BIND_HOST` | `127.0.0.1` | Bind address. Defaults to localhost; a validator warns if set to a public/routable address. Never make it public. |
| `SEESTAR_DATA_DIR` | `./data` | Local data directory (downloaded subs, provenance log, manifests). |
| QA thresholds | see `config.py` | `SEESTAR_QA_FWHM_SIGMA`, `SEESTAR_QA_ECCENTRICITY_REJECT`, `SEESTAR_QA_SNR_FLOOR_FACTOR`, `SEESTAR_QA_STARCOUNT_FLOOR_FACTOR`, absolute overrides, etc. are all overridable. |

All binds default to **localhost** and must never be public. **Secrets never go in config.**
The firmware-7.18+ RSA key and any tokens live in `./secrets/` (gitignored) or in
`SEESTAR_SECRET_*` environment variables, loaded on demand by `secrets.py` and never written
to config, source, or the provenance log.

## Tools (18)

The server exposes exactly 18 single-purpose, least-privilege tools with honest,
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

## Skills

**MCP is the access layer; Skills are the procedure layer.** The MCP server gives Claude the
ability to reach the telescope, the FITS files, and the QA computations; the Skills encode
*how* to use them:

- **`run-session`** — the end-to-end session run-book (pre-flight, acquire, focus, stack,
  monitor, wind down).
- **`qa-policy`** — the scoring policy: what the QA metrics mean and how to decide
  PASS / MARGINAL / REJECT.
- **`anomaly-playbook`** — the fault decision tree (clouds, dew, focus drift, tracking loss,
  connection drops).

Skills stay at roughly ~100 tokens of description until they are invoked, so they add almost
no standing context cost, while the MCP server is deliberately kept lean and single-purpose.
This keeps token usage low and the tool surface auditable.

## Security

See [`SECURITY.md`](SECURITY.md) for the threat model, the OWASP MCP Top 10 mitigation
table, the supply-chain runbook (`uv lock` / `make sbom` / `make scan`), and secrets
handling. The hardened systemd unit for the Jetson is in
[`deploy/seestar-mcp.service`](deploy/seestar-mcp.service).

# Containerize seestar-mcp + the seestar_alp Bridge — Design Spec

**Date:** 2026-07-18
**Project:** seestar-mcp
**Status:** Approved direction (from discussion) — ready for implementation planning
**Builds on:** current stdio-MCP + external-`seestar_alp` deployment (`deploy/seestar-mcp.service`, `README.md`, `SECURITY.md`).

---

## Goal

Package **both** the MCP server and the `seestar_alp` bridge as containers, driven by one `docker compose`
stack, and **move the bridge's SSC web-UI port off `5432`** so it stops colliding with the Sentinel
postgres backend on the same host. Do it without weakening the repo's security posture (stdio MCP with no
listening socket; `seestar_alp` external, pinned, **not vendored**; human-in-the-loop for destructive ops)
and without changing any `src/` behavior (the 214-test suite must stay green).

## What actually collides (and the one-line fix)

`seestar_alp`'s `device/config.toml` exposes two independent ports:

- `[network] port = 5555` → **ASCOM Alpaca API** — the only bridge port the MCP server talks to. **Unchanged.**
- `[webui_settings] uiport = 5432` → **SSC (Simple Seestar Client) web UI** — defaults to `5432`, the literal
  postgres collision. **This is the port we move.**

**Fix:** our config override sets `uiport = 5433`. The MCP server never references `uiport`, so the collision
fix requires **no** MCP config change. The MCP↔bridge address change below is a *separate*, containerization-
driven rewire (loopback host → Docker service name), not part of the collision fix.

Confirmed against upstream `smart-underworld/seestar_alp` (`device/config.toml.example`, `docker/`): the
Alpaca server (`root_app.py`) serves `[network] port` (5555) and `[webui_settings] uiport` (5432); upstream's
`docker/run.sh` bind-mounts `docker/config.toml` → `/home/seestar/seestar_alp/device/config.toml`.

## Design principles (inherited)

- **stdio MCP, no inbound socket.** The MCP container is launched per session by Claude Code over stdio
  (`docker run -i --rm …`); it publishes no ports. Preserves the SECURITY.md boundary.
- **`seestar_alp` is not vendored.** No upstream source is copied into this repo. The bridge image is built
  from an operator-supplied, pinned `seestar_alp` checkout using upstream's own `docker/Dockerfile`; we
  contribute only a `config.toml` override and the compose wiring. Respects `NOTICE` / GPL-3.0 separation.
- **No secrets in image/source/logs.** The RSA interop key and the meteoblue key are mounted/injected at
  runtime, never baked into an image or committed.
- **Determinism & backward compatibility.** No `src/` change; the non-containerized host path (`uv … run
  python -m seestar_mcp.server` against `127.0.0.1:5555`) keeps working unchanged.

---

## Architecture

One `docker compose` stack on a **user-defined bridge network** `seestar_net`:

```
┌─ network: seestar_net ───────────────────────────────────────────────┐
│                                                                       │
│  seestar-mcp (stdio, on-demand)        seestar-alp (long-lived svc)   │
│  docker run -i --rm --network seestar_net                             │
│    SEESTAR_ALPACA_BASE_URL ───────────▶ :5555  Alpaca  (network-only) │
│      = http://seestar-alp:5555          :5433  SSC UI  (127.0.0.1:5433)│
│    -v seestar-data:/app/data                                          │
│    --env-file .env.docker                                            │
└──────────────────────────────────┬────────────────────────────────────┘
        publish 127.0.0.1:5433:5433 ┘        └─▶ LAN: Seestar <seestar-ip>
                                                  Alpaca→JSON-RPC :4700 / HTTP :80 / SMB :445 (NAT)
```

### Networking decision — bridge net, not host net

Upstream's `run.sh` uses `--network host` + Avahi so mDNS (`seestar.local`) resolves. We deliberately do
**not**: host networking would (a) break Docker service-name DNS so the on-demand MCP container couldn't
reach `seestar-alp:5555`, and (b) bind the SSC UI straight onto host `:5432`, re-creating the collision.
Because the Seestar already has a **fixed DHCP-reserved IP (`<seestar-ip>`)**, mDNS is unnecessary: set
`[[seestars]] ip_address = "<seestar-ip>"` in our `config.toml`, put both containers on `seestar_net`, and
the bridge reaches the scope over the LAN via NAT. This is what makes the on-demand MCP model work cleanly.

### Components (files added — all under `deploy/docker/`)

| File | Purpose |
|------|---------|
| `Dockerfile.mcp` | Build the MCP image: `python:3.12-slim` + `uv`, `uv sync --locked` (hash-locked env from `uv.lock`), entrypoint `python -m seestar_mcp.server`. Builds natively on Jetson arm64 or Windows/Linux x86_64 (base + numpy/astropy/photutils wheels are multi-arch; include build-essential in a builder stage only if a transitive dep lacks an aarch64 wheel). |
| `docker-compose.yml` | Defines `seestar-alp` (long-lived) + `seestar_net` + volumes. `seestar-alp` uses `build.context: ${SEESTAR_ALP_DIR}`, `build.dockerfile: docker/Dockerfile`, `build.args: {ASTRO_PLATFORM: ALPACA}`, and bind-mounts our `config.toml` and the RSA key. Publishes only `127.0.0.1:5433:5433` (SSC) and optionally `127.0.0.1:5555:5555` (host-side debugging). The MCP is **not** a compose `service` (it's stdio/on-demand); the compose file documents its `docker run` registration in a comment. |
| `config.toml` | Our bridge override: `[webui_settings] uiport = 5433`; `[network] port = 5555`; `[[seestars]] ip_address = "<seestar-ip>"`, `device_num = 1`; `interop_pem = "/etc/seestar/seestar_client_key.pem"`; `[network] ip_address = '0.0.0.0'` so the Alpaca server is reachable from the MCP container on `seestar_net` (not just loopback-inside-container). |
| `.env.docker.example` | Compose/`docker run` env: `SEESTAR_ALP_DIR` (path to the pinned checkout), `SEESTAR_IP`, `SSC_PORT=5433`, `SEESTAR_CLIENT_KEY` (host path to the PEM), `SEESTAR_METEOBLUE_API_KEY`, and the MCP override `SEESTAR_ALPACA_BASE_URL=http://seestar-alp:5555`. Real `.env.docker` is gitignored. |
| `README-docker.md` | Build/run/registration runbook (below), plus the two behavioral notes. |

### The MCP↔bridge rewire ("make sure the MCP server knows")

The host `.env` stays `SEESTAR_ALPACA_BASE_URL=http://127.0.0.1:5555` for the non-containerized path. The
**MCP container** instead receives `SEESTAR_ALPACA_BASE_URL=http://seestar-alp:5555` (via `--env-file
.env.docker`). That loopback→service-name swap is the address change the MCP server must know once
containerized. `SEESTAR_ALPACA_DEVICE_NUM=1` is unchanged.

### Secrets & volumes

- **RSA interop key** (`seestar_client_key.pem`): bind-mounted **read-only** into the *bridge* at
  `/etc/seestar/seestar_client_key.pem`, referenced by `interop_pem`. The MCP server never sees it
  (SECURITY.md invariant preserved).
- **meteoblue key**: injected into the MCP container via env-file (`SEESTAR_METEOBLUE_API_KEY`); never
  committed.
- **MCP data**: named volume `seestar-data` → `/app/data` for the append-only provenance log + manifests.

### Registration (Claude Code)

```
# 1. bring up the bridge (long-lived)
cd deploy/docker && docker compose up -d seestar-alp

# 2. register the on-demand MCP container with Claude Code
claude mcp add seestar-mcp -- \
  docker run -i --rm \
    --network seestar_net \
    -v seestar-data:/app/data \
    --env-file deploy/docker/.env.docker \
    seestar-mcp:local
```
Registration MUST happen before starting a Remote Control session (MCP servers can't be added mid-session).

## Data flow (unchanged semantics)

```
Claude Code ──stdio──▶ seestar-mcp (container) ──HTTP──▶ seestar-alp:5555 ──▶ Seestar <seestar-ip>
                                     │                     (Alpaca → native JSON-RPC :4700)
                                     └──HTTP :80 / SMB :445 (LAN, NAT)──▶ Seestar  (sub downloads)
```

## Two behavioral notes (called out, not hidden)

1. **`SEESTAR_SEESTAR_IMAGE_ROOT`** is a Windows UNC path (`\\<seestar-ip>\EMMC Images\MyWorks`) and is
   meaningless inside a Linux MCP container. **Default: leave it unset in `.env.docker`**, so
   `list_subs`/`download_subs` fall back to the device HTTP/JSON-RPC path (device `:80`/`:4700` reachable
   over the LAN via NAT). Documented alternative: CIFS-mount the share into the MCP container and set
   `SEESTAR_SEESTAR_IMAGE_ROOT=/mnt/seestar-works`.
2. **Hardened-systemd egress allowlist** (`IPAddressDeny/Allow`, syscall filter, `ProtectSystem`) from
   `deploy/seestar-mcp.service` has only a **weaker Docker analog**. We keep localhost-only port publishing
   and document the gap; we do **not** attempt to reproduce the full systemd sandbox in Docker. The systemd
   unit remains the hardened option for host-run deployments.

## Error handling / failure modes

- Bridge not up when the MCP container starts → MCP tools return `{"ok": false, "error": …}` on the first
  Alpaca call (existing never-raise behavior); the runbook says `compose up -d seestar-alp` first.
- Missing/empty RSA key with firmware 7.18+ → bridge silently ignores commands (existing firmware behavior);
  runbook documents mounting the PEM and setting `interop_pem`.
- Wrong `SEESTAR_ALP_DIR` / unpinned ref → `docker compose build` fails fast with a clear path/ref error;
  runbook shows the expected checkout + `git checkout <reviewed-ref>` step.

## Testing / verification

No `src/` change ⇒ `uv run pytest` (≈214) stays green and `uv run ruff check src tests` clean — confirmed as
the gate. Container-level verification (documented in `README-docker.md`, run on a Docker host):

- `docker compose config` lints the compose file (env interpolation resolves).
- `docker compose build seestar-alp` from a pinned checkout succeeds; `docker build -f Dockerfile.mcp`
  succeeds and `uv sync --locked` matches the committed lock.
- `docker compose up -d seestar-alp` → SSC UI answers on `127.0.0.1:5433` (and **not** `5432`); Alpaca answers
  on `seestar-alp:5555` from within `seestar_net`.
- Smoke: `docker run -i --rm --network seestar_net --env-file .env.docker seestar-mcp:local` then a
  `connect_telescope` / `get_status` round-trip resolves `seestar-alp:5555` and returns `ok`.

## Security / reproducibility

- MCP publishes no port (stdio); bridge publishes SSC only to `127.0.0.1`. No new inbound exposure.
- No secrets in images or source; RSA key + meteoblue key mounted/injected at runtime; `.env.docker`
  gitignored.
- Bridge pinned to an operator-reviewed `seestar_alp` ref and built from upstream's Dockerfile — not
  vendored, not redistributed. MCP env hash-locked via `uv.lock`.
- Deterministic host path preserved (`127.0.0.1:5555` unchanged); the containerized path only overrides the
  Alpaca base URL to the service name.

## Out of scope

Registry publishing / signed images, multi-scope (`[[seestars]]` > 1) arrays, the INDI bridge variant, CIFS
image-root automation, and reproducing the full systemd sandbox inside Docker.

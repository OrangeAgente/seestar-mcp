# Containerized deployment — bridge + MCP server

Run the external `seestar_alp` **bridge** and the **seestar-mcp** server as containers, driven by
one `docker compose` stack. Use this instead of the host `uv` + systemd path when you'd rather
isolate both processes in Docker (e.g. sharing a host with other services such as a Sentinel
postgres backend).

## Why the SSC port moved to 5544

`seestar_alp` serves two ports: the **ASCOM Alpaca API on `5555`** (the only port the MCP server
talks to) and the **SSC web UI on `5432`** by default. `5432` is postgres's default port, so on a
shared host it collides with a postgres backend — and a postgres often gets remapped to the
next-door `5433`, so that neighbour is frequently taken too. This stack therefore defaults the
**SSC web UI to `5544`** — clear of the whole `5432`/`5433` postgres neighbourhood — via `uiport`
in `config.toml` and the published host port in compose. Pick any free port with `SSC_PORT` in
`.env.docker` (keep it equal to `uiport`). The Alpaca port stays `5555`, and the MCP server reaches
the bridge over the compose network at `http://seestar-alp:5555` — **no MCP config change is needed
for the collision fix.**

## Topology

```
┌─ network: seestar_net ───────────────────────────────────────────────┐
│  seestar-mcp (stdio, on-demand)        seestar-alp (long-lived svc)   │
│  docker run -i --rm --network seestar_net                             │
│    SEESTAR_ALPACA_BASE_URL ───────────▶ :5555  Alpaca  (network only)  │
│      = http://seestar-alp:5555          :5544  SSC UI  (127.0.0.1 only)│
│    -v seestar-data:/app/data                                          │
└──────────────────────────────────┬────────────────────────────────────┘
                                    └─▶ LAN: Seestar (fixed IP)
                                        Alpaca→JSON-RPC :4700 / HTTP :80 / SMB :445 (NAT)
```

The MCP server is **not** a compose service — it speaks MCP over stdio and is launched per session
by Claude Code. Compose runs the long-lived bridge; Claude Code spawns a fresh MCP container each
session, joined to `seestar_net`.

## Prerequisites

- Docker Engine 24+ with Compose v2 (tested on 29.x / Compose v5) and buildx.
- A pinned **source** checkout of `seestar_alp` (`git clone https://github.com/smart-underworld/seestar_alp`,
  then `git checkout <reviewed-ref>`). **A compiled/PyInstaller distribution has no `docker/Dockerfile`
  and cannot be built** — you need the source tree. `seestar_alp` is external and GPL-3.0; it is **not**
  vendored into this repo — the compose file builds it from *your* checkout.
  - **Windows hosts:** clone with LF preserved — `git clone --config core.autocrlf=false ...` — or the
    container crash-loops with `/usr/bin/env: 'bash\r': No such file or directory` (exit 127): Git's
    default `autocrlf=true` rewrites upstream's shell entrypoints to CRLF, which breaks the shebang
    inside the Linux image. Linux/Jetson build hosts are unaffected.
- Your scope's **fixed LAN IP** (DHCP reservation). The bridge runs on a user-defined network without
  mDNS, so `seestar.local` will not resolve — use the IP.
- (Firmware 7.18+) your RSA interop key `seestar_client_key.pem`.

## One-time setup

```bash
cd deploy/docker

# 1. Bridge config: copy the template, set your scope's LAN IP.
cp config.toml.example config.toml
#    edit config.toml:  [[seestars]] ip_address = "<your-scope-IP>"
#    firmware < 7.18? set  interop_pem = ""  and skip the key below.

# 2. Compose/run env: copy the template, set paths + ports.
cp .env.docker.example .env.docker
#    edit .env.docker:
#      SEESTAR_ALP_DIR=/absolute/path/to/seestar_alp   # your pinned source checkout
#      SSC_PORT=5544                                    # or any free port != 5432
#      SEESTAR_CLIENT_KEY=/abs/path/to/seestar_client_key.pem   # firmware 7.18+ only
#      SEESTAR_METEOBLUE_API_KEY=...                    # optional (planner weather)
```

`config.toml` and `.env.docker` are gitignored — only the `*.example` templates are committed.

## Run

```bash
# Build + start the long-lived bridge
docker compose --env-file .env.docker up -d --build seestar-alp

# Build the MCP image (from the repo root)
cd ../..
docker build -f deploy/docker/Dockerfile.mcp -t seestar-mcp:local .
```

Verify the bridge:

```bash
# SSC web UI is published to the host on 5544 (not 5432):
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5544/   # -> 200

# Alpaca (5555) is NOT published to the host — it's reachable in-network as
# seestar-alp:5555, so it can't collide with a native seestar_alp on the host.
# Check it from within seestar_net:
docker run --rm --network seestar_net curlimages/curl:latest \
  -fsS http://seestar-alp:5555/management/apiversions          # -> {"Value":[1],...}
```

## Register the MCP server with Claude Code

MCP servers **must** be registered before a Remote Control session starts (they can't be added
mid-session). Register the on-demand container form:

```bash
claude mcp add seestar-mcp -- \
  docker run -i --rm --network seestar_net \
    -v seestar-data:/app/data \
    --env-file deploy/docker/.env.docker \
    seestar-mcp:local
```

The `.env.docker` file supplies `SEESTAR_ALPACA_BASE_URL=http://seestar-alp:5555` — that service-name
address (not `127.0.0.1`) is what lets the containerized MCP server reach the bridge across
`seestar_net`.

## Two behavioral notes

1. **Image root / sub downloads.** The host `SEESTAR_SEESTAR_IMAGE_ROOT` is often a Windows UNC path
   (`\\<ip>\EMMC Images\MyWorks`), which is meaningless inside a Linux container — leave it **unset**
   in `.env.docker`. `list_subs`/`download_subs` then fall back to the device HTTP/JSON-RPC path
   (reachable over the LAN via NAT). To use SMB instead, CIFS-mount the share into the MCP container
   and set `SEESTAR_SEESTAR_IMAGE_ROOT=/mnt/seestar-works`.
2. **Egress hardening.** The hardened systemd unit (`deploy/seestar-mcp.service`) enforces a strict
   egress allowlist + syscall filter + read-only FS that Docker only weakly approximates. This stack
   keeps localhost-only port publishing and the stdio-no-socket boundary, but if you need the
   strongest egress controls, prefer the systemd host-run path. The two are alternatives, not layers.

## Teardown

```bash
docker compose --env-file deploy/docker/.env.docker down   # removes containers + network
# the seestar-data volume (provenance log + manifests) persists; add -v to delete it.
```

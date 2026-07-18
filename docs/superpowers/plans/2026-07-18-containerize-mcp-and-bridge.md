# Containerize seestar-mcp + seestar_alp Bridge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `docker compose` stack that runs the external `seestar_alp` bridge as a long-lived service and the stdio MCP server as an on-demand container, with the bridge's SSC web UI moved off `5432` (Sentinel/postgres collision) to `5433`, and the MCP container wired to reach the bridge at `http://seestar-alp:5555`.

**Architecture:** Two containers on a user-defined bridge network `seestar_net`. The bridge is built from an operator-supplied, pinned `seestar_alp` **source** checkout (`SEESTAR_ALP_DIR`) using upstream's own `docker/Dockerfile` — nothing vendored. The MCP image is a slim, non-root, `uv`-hash-locked Python 3.12 image launched via `docker run -i --rm` (no ports). No `src/` code changes.

**Tech Stack:** Docker 29.x + Compose v5, `python:3.12-slim`, `ghcr.io/astral-sh/uv:0.10.12`, upstream `seestar_alp` (ubuntu:24.04, Flask/waitress).

## Global Constraints

- **stdio MCP, no inbound socket.** MCP container publishes no ports; launched per session by Claude Code.
- **`seestar_alp` not vendored.** Build from `${SEESTAR_ALP_DIR}` (pinned source checkout) + upstream Dockerfile; contribute only a `config.toml` override + compose wiring. Respects `NOTICE`/GPL-3.0.
- **No secrets committed.** RSA `*.pem` is a runtime file-mount into the bridge only; meteoblue key via env-file. `.env.docker` and the real `config.toml` are gitignored; only `*.example` templates are committed.
- **No `src/` behavior change.** `uv run pytest` (≈214) stays green and `uv run ruff check src tests` clean — the gate.
- **Bridge Alpaca bind MUST be `0.0.0.0`** (`[network] ip_address`) or the MCP container can't reach it across `seestar_net` (confirmed: `device/app.py` binds `make_server(Config.ip_address, Config.port)`).
- **New SSC port = 5433**; Alpaca stays `5555`; `device_num = 1`.
- **Files live under `deploy/docker/`.**

---

### Task 1: MCP server image

**Files:**
- Create: `deploy/docker/Dockerfile.mcp`
- Create: `deploy/docker/.dockerignore`

**Interfaces:**
- Produces: image `seestar-mcp:local`, entrypoint `python -m seestar_mcp.server` (stdio), reads config from env (`SEESTAR_*`), writes to `/app/data` (volume).

- [ ] **Step 1: Write `deploy/docker/.dockerignore`** (keep the MCP build context tiny; the context is the repo root)

```
.git
.venv
data
docs
deploy
tests
skills
*.fits
*.pem
*.key
.env
.env.docker
__pycache__
.pytest_cache
.ruff_cache
sbom*.json
sbom*.xml
```

- [ ] **Step 2: Write `deploy/docker/Dockerfile.mcp`**

```dockerfile
# syntax=docker/dockerfile:1
# ---- builder: resolve the hash-locked environment (uv, matching repo toolchain) ----
FROM python:3.12-slim AS builder

# Pinned uv (matches the dev machine's uv 0.10.12) for reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /usr/local/bin/uv

# Build tooling only needed if a transitive dep lacks a wheel for the target arch
# (aarch64 on the Jetson). Confined to the builder stage — not in the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
# Deps only (cached unless lock/pyproject change); project runs from /app/src via PYTHONPATH.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# ---- runtime: slim, non-root, venv + source only ----
FROM python:3.12-slim AS runtime

RUN groupadd --gid 1001 seestar \
    && useradd --uid 1001 --gid 1001 --create-home --shell /usr/sbin/nologin seestar

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SEESTAR_DATA_DIR=/app/data \
    SEESTAR_PROVENANCE_LOG=/app/data/provenance.jsonl \
    SEESTAR_MANIFEST_DIR=/app/data/manifests

COPY --from=builder /opt/venv /opt/venv
COPY src /app/src

WORKDIR /app
RUN mkdir -p /app/data && chown -R seestar:seestar /app
USER seestar

# MCP over stdio: no EXPOSE, no socket. Claude Code attaches stdin/stdout.
ENTRYPOINT ["python", "-m", "seestar_mcp.server"]
```

- [ ] **Step 3: Build the image**

Run: `docker build -f deploy/docker/Dockerfile.mcp -t seestar-mcp:local .`
Expected: build succeeds; final line `naming to ... seestar-mcp:local`.

- [ ] **Step 4: Verify the package imports inside the image**

Run: `docker run --rm --entrypoint python seestar-mcp:local -c "import seestar_mcp.server; from seestar_mcp.config import get_settings; print('alpaca=', get_settings().alpaca_base_url)"`
Expected: prints `alpaca= http://127.0.0.1:5555` (the default; overridden at runtime by env). No import error.

- [ ] **Step 5: Commit**

```bash
git -c core.autocrlf=false add deploy/docker/Dockerfile.mcp deploy/docker/.dockerignore
git -c core.autocrlf=false commit -m "build: MCP server container image (uv hash-locked, non-root, stdio)"
```

---

### Task 2: Bridge config override (the port move)

**Files:**
- Create: `deploy/docker/config.toml.example` (committed template)
- Create (local, gitignored, for testing): `deploy/docker/config.toml`

**Interfaces:**
- Produces: a complete `seestar_alp` `config.toml` bind-mounted to `/home/seestar/seestar_alp/device/config.toml`. Overrides vs upstream: `[network] ip_address='0.0.0.0'`, `[webui_settings] uiport=5433`, `[[seestars]] ip_address=<SEESTAR_IP>`, `device_num=1`, `interop_pem` path.

- [ ] **Step 1: Write `deploy/docker/config.toml.example`** (full file — seestar_alp reads the whole file; a partial mount would drop sections)

```toml
# seestar_alp bridge config for the containerized stack.
# Copy to config.toml (gitignored) and set <SEESTAR_IP> to your scope's fixed LAN IP.
# Overrides vs upstream defaults are flagged with  # <-- OVERRIDE
title = "seestar_alp (containerized bridge)"

[network]
ip_address = '0.0.0.0'   # <-- OVERRIDE: bind all ifaces so the MCP container can reach Alpaca on seestar_net
port = 5555              # ASCOM Alpaca API (the MCP server talks to this)
imgport = 7556           # imaging API
stport = 8090            # stellarium
sthost = 'localhost'
rtsp_udp = true

[webui_settings]
uiport = 5433            # <-- OVERRIDE: SSC web UI moved off 5432 (Sentinel/postgres collision)
uitheme = "dark"
confirm = true

[server]
location = 'Anywhere on Earth'
verbose_driver_exceptions = true

[device]
can_reverse = true
step_size = 1.0
steps_per_sec = 6
verify_injection = true

[seestar_initialization]
save_good_frames = true
save_all_frames = false
lat = 0                  # 0 = guess from IP; set to your latitude for correct init
long = 0                 # 0 = guess from IP; set to your longitude for correct init
gain = 80
exposure_length_preview_ms = 500
exposure_length_stack_ms = 10000
dither_enabled = true
dither_length_pixel = 50
dither_frequency = 10
activate_LP_filter = false
dew_heater_power = 0
guest_mode_init = true
battery_low_limit = 3
dec_pos_index = 3
is_frame_calibrated = true

# RSA interop key (firmware 7.18+). Mounted read-only into the bridge by compose.
# Firmware < 7.18: set this to "" and the key mount is ignored.
interop_pem = "/etc/seestar/seestar_client_key.pem"   # <-- OVERRIDE: container path

[logging]
log_level = 'INFO'
log_prefix = ''
log_to_stdout = true     # <-- OVERRIDE: logs to container stdout (docker logs / journald)
max_size_mb = 5
num_keep_logs = 10
log_events_in_info = true

[[seestars]]
name = "Seestar Alpha"
ip_address = "<SEESTAR_IP>"   # <-- OVERRIDE: your scope's fixed DHCP-reserved LAN IP (no mDNS in bridge net)
device_num = 1
```

- [ ] **Step 2: Create the local test `config.toml`** (copy the example, set the real IP; this file is gitignored)

Run: `cp deploy/docker/config.toml.example deploy/docker/config.toml` then set `<SEESTAR_IP>` → `<seestar-ip>` and `interop_pem = ""` (no live handshake needed for the endpoint smoke test).
Expected: `deploy/docker/config.toml` exists with `uiport = 5433`, `ip_address = '0.0.0.0'`, `ip_address = "<seestar-ip>"`.

- [ ] **Step 3: Verify the override keys**

Run: `grep -nE "uiport|ip_address|device_num" deploy/docker/config.toml`
Expected: shows `uiport = 5433`, `[network] ip_address = '0.0.0.0'`, seestar `ip_address = "<seestar-ip>"`, `device_num = 1`.

- [ ] **Step 4: Commit the template only**

```bash
git -c core.autocrlf=false add deploy/docker/config.toml.example
git -c core.autocrlf=false commit -m "config: containerized seestar_alp bridge config (SSC 5432->5433, bind 0.0.0.0)"
```

---

### Task 3: Compose stack + env template + gitignore

**Files:**
- Create: `deploy/docker/docker-compose.yml`
- Create: `deploy/docker/.env.docker.example` (committed template)
- Create (local, gitignored): `deploy/docker/.env.docker`
- Modify: `.gitignore` (add `deploy/docker/.env.docker`, `deploy/docker/config.toml`)

**Interfaces:**
- Consumes: image `seestar-mcp:local` (Task 1), `config.toml` (Task 2), `${SEESTAR_ALP_DIR}` source checkout.
- Produces: network `seestar_net`, volume `seestar-data`, service `seestar-alp` publishing `127.0.0.1:5433` + `127.0.0.1:5555`.

- [ ] **Step 1: Write `deploy/docker/docker-compose.yml`**

```yaml
name: seestar

networks:
  seestar_net:
    name: seestar_net
    driver: bridge

volumes:
  seestar-data:
    name: seestar-data

services:
  # The external seestar_alp bridge — built from YOUR pinned source checkout
  # (SEESTAR_ALP_DIR), not vendored here. Long-lived; owns the device handshake.
  seestar-alp:
    container_name: seestar-alp
    image: seestar-alp:local
    build:
      context: ${SEESTAR_ALP_DIR:?set SEESTAR_ALP_DIR to a pinned seestar_alp SOURCE checkout}
      dockerfile: docker/Dockerfile
      args:
        ASTRO_PLATFORM: ALPACA
    networks: [seestar_net]
    ports:
      # SSC web UI — moved OFF 5432 to dodge the Sentinel/postgres collision.
      - "127.0.0.1:${SSC_PORT:-5433}:5433"
      # Alpaca — reachable network-internally as seestar-alp:5555; also published
      # to loopback for host-side debugging (safe: localhost only).
      - "127.0.0.1:5555:5555"
    volumes:
      - type: bind
        source: ./config.toml
        target: /home/seestar/seestar_alp/device/config.toml
        read_only: true
      # RSA interop key (firmware 7.18+). Defaults to an empty mount when unset;
      # set SEESTAR_CLIENT_KEY to your PEM path for firmware >= 7.18.
      - type: bind
        source: ${SEESTAR_CLIENT_KEY:-/dev/null}
        target: /etc/seestar/seestar_client_key.pem
        read_only: true
    environment:
      - TZ=${TZ:-Etc/UTC}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python3", "-c",
             "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5555/management/apiversions', timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s

# ─────────────────────────────────────────────────────────────────────────────
# The MCP server is NOT a service here — it speaks MCP over stdio and is launched
# on-demand by Claude Code. After `docker compose up -d seestar-alp`, register it:
#
#   docker build -f deploy/docker/Dockerfile.mcp -t seestar-mcp:local .
#   claude mcp add seestar-mcp -- \
#     docker run -i --rm --network seestar_net \
#       -v seestar-data:/app/data \
#       --env-file deploy/docker/.env.docker \
#       seestar-mcp:local
# ─────────────────────────────────────────────────────────────────────────────
```

- [ ] **Step 2: Write `deploy/docker/.env.docker.example`**

```bash
# Copy to .env.docker (gitignored) and fill in. Used by `docker compose` AND the
# on-demand MCP `docker run --env-file`. No DEVICE secrets here — the RSA key is a
# FILE mount; only the (non-device) meteoblue key is a var.

# --- bridge build ---
# Path to YOUR pinned seestar_alp SOURCE checkout (git clone, reviewed ref).
# NOTE: a compiled/PyInstaller dist has no docker/Dockerfile and cannot be built.
SEESTAR_ALP_DIR=/absolute/path/to/seestar_alp
# SSC web UI host port (moved off 5432 for the Sentinel/postgres collision).
SSC_PORT=5433
# Host path to your RSA interop key (firmware 7.18+). Leave unset if firmware < 7.18
# (also set interop_pem="" in config.toml).
# SEESTAR_CLIENT_KEY=/absolute/path/to/seestar_client_key.pem
TZ=Etc/UTC

# --- MCP container -> bridge wiring (the address the MCP server must know) ---
SEESTAR_ALPACA_BASE_URL=http://seestar-alp:5555
SEESTAR_ALPACA_DEVICE_NUM=1

# --- MCP planner (non-device secret; keep out of git) ---
SEESTAR_METEOBLUE_API_KEY=

# --- image root: leave UNSET in-container -> subs download via device HTTP/JSON-RPC.
# To use SMB instead, CIFS-mount the share and set an absolute container path:
# SEESTAR_SEESTAR_IMAGE_ROOT=/mnt/seestar-works
```

- [ ] **Step 3: Add gitignore entries** (append under the secrets block)

```
# Docker local config / secrets (commit only the .example templates)
deploy/docker/.env.docker
deploy/docker/config.toml
```

- [ ] **Step 4: Create the local test `.env.docker`** (gitignored) with `SEESTAR_ALP_DIR` → the scratch source clone (from Task 4 Step 1), `SSC_PORT=5433`, meteoblue key from the host `.env`.

- [ ] **Step 5: Lint the compose file**

Run: `docker compose -f deploy/docker/docker-compose.yml --env-file deploy/docker/.env.docker config`
Expected: prints the fully-resolved config; `seestar-alp` shows ports `127.0.0.1:5433->5433` and `127.0.0.1:5555->5555`, the `config.toml` bind, and network `seestar_net`. No error.

- [ ] **Step 6: Commit templates + gitignore**

```bash
git -c core.autocrlf=false add deploy/docker/docker-compose.yml deploy/docker/.env.docker.example .gitignore
git -c core.autocrlf=false commit -m "build: docker compose stack for bridge + on-demand MCP container"
```

---

### Task 4: Bring-up + end-to-end wiring test

**Files:** none (verification task; uses a scratch source clone outside the repo).

**Interfaces:**
- Consumes: everything from Tasks 1-3.
- Produces: evidence that SSC is on 5433 (not 5432), Alpaca answers on `seestar-alp:5555`, and the MCP container resolves + reaches the bridge via `SEESTAR_ALPACA_BASE_URL`.

- [ ] **Step 1: Clone the bridge source to scratch** (NOT into the repo — keeps "not vendored")

Run: `git clone --depth 1 https://github.com/smart-underworld/seestar_alp "$SCRATCH/seestar_alp"` and point `SEESTAR_ALP_DIR` in `.env.docker` at it.
Expected: clone succeeds; `$SCRATCH/seestar_alp/docker/Dockerfile` and `requirements.txt` exist.

- [ ] **Step 2: Build the bridge image**

Run: `docker compose -f deploy/docker/docker-compose.yml --env-file deploy/docker/.env.docker build seestar-alp`
Expected: build succeeds; `seestar-alp:local` created.

- [ ] **Step 3: Bring up the bridge**

Run: `docker compose -f deploy/docker/docker-compose.yml --env-file deploy/docker/.env.docker up -d seestar-alp`
Then: `docker compose -f deploy/docker/docker-compose.yml logs seestar-alp | grep -i "Serving on"`
Expected: log shows `==STARTUP== Serving on 0.0.0.0:5555`. Container is Up.

- [ ] **Step 4: Verify SSC moved to 5433 (and Alpaca answers) from the host**

Run: `curl -fsS http://127.0.0.1:5555/management/apiversions` and `curl -fsS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5433/`
Expected: Alpaca returns JSON `{"Value":[1],...}`; SSC returns `200` on 5433.

- [ ] **Step 5: Confirm nothing on 5432 from this stack**

Run: `curl -fsS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5432/ ; echo "exit=$?"`
Expected: connection refused / non-200 (the stack no longer publishes 5432 — free for Sentinel's postgres).

- [ ] **Step 6: MCP container → bridge reachability (the "MCP knows the new address" proof)**

Run:
```bash
docker run --rm --network seestar_net --env-file deploy/docker/.env.docker \
  --entrypoint python seestar-mcp:local -c \
  "import os,httpx; u=os.environ['SEESTAR_ALPACA_BASE_URL']; r=httpx.get(u+'/management/apiversions',timeout=5); print('URL',u,'->',r.status_code,r.text[:120])"
```
Expected: `URL http://seestar-alp:5555 -> 200 {"Value":[1],...}` — DNS resolves `seestar-alp`, Alpaca reachable across `seestar_net`, env wiring correct.

- [ ] **Step 7: (best-effort) real MCP tool round-trip**

Run a `connect_telescope` then `get_status` via the controller inside the MCP image against the bridge.
Expected: JSON with `"ok"` present (may be `ok:false` with an error if the scope is powered off — that still proves the MCP→bridge→Alpaca path; a live scope yields `ok:true`). Record whichever occurs.

- [ ] **Step 8: Tear down**

Run: `docker compose -f deploy/docker/docker-compose.yml --env-file deploy/docker/.env.docker down`
Expected: containers + network removed; `seestar-data` volume persists.

---

### Task 5: Docs

**Files:**
- Create: `deploy/docker/README-docker.md`
- Modify: `README.md` (add a "Run in containers" pointer near the existing deploy section)
- Modify: `SECURITY.md` (note the containerized topology + the systemd-egress-vs-Docker gap)

**Interfaces:** none (documentation).

- [ ] **Step 1: Write `deploy/docker/README-docker.md`** covering: prerequisites (Docker, a pinned `seestar_alp` **source** checkout, optional RSA key), the SSC 5432→5433 rationale, `cp *.example` steps, `docker compose up -d seestar-alp`, `docker build` + `claude mcp add` registration (before starting Remote Control), the two behavioral notes (UNC image-root → unset/HTTP fallback; systemd-egress-allowlist has only a weaker Docker analog), and teardown.

- [ ] **Step 2: Add a pointer in `README.md`** under the deploy section: containerized alternative → `deploy/docker/README-docker.md`, one line that the SSC UI is on 5433 in the container stack.

- [ ] **Step 3: Add a `SECURITY.md` note:** containerized topology preserves stdio-no-socket for MCP + localhost-only publishing for the bridge; the hardened systemd unit remains the option with the strongest egress controls.

- [ ] **Step 4: Commit**

```bash
git -c core.autocrlf=false add deploy/docker/README-docker.md README.md SECURITY.md
git -c core.autocrlf=false commit -m "docs: containerized deployment runbook (bridge + MCP)"
```

---

### Final gate

- [ ] **Step 1: Tests + lint (no src change ⇒ must be unchanged-green)**

Run: `uv run pytest -q` then `uv run ruff check src tests`
Expected: pytest all pass (≈214); ruff clean.

- [ ] **Step 2: Confirm no secrets / local files staged**

Run: `git status --porcelain` and `git ls-files deploy/docker`
Expected: only `*.example`, `Dockerfile.mcp`, `.dockerignore`, `docker-compose.yml`, `README-docker.md` tracked; `config.toml`, `.env.docker` untracked/ignored.

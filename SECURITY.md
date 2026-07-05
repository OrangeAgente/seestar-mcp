# Security

`seestar-mcp` is built for high-assurance operation. This document states the threat model,
maps the [OWASP MCP Top 10](https://owasp.org/) risks to the concrete controls in this repo,
gives the supply-chain runbook, and describes secrets handling. All controls below match
code that actually exists in `src/seestar_mcp/`.

## Threat model

### Assets

- **Telescope control** — the ability to command motion (goto, autofocus, park), change the
  filter/dew heater, and power the device down.
- **FITS data** — the RAW subs pulled off the device and the derived QA verdicts.
- **The provenance log** — the append-only audit trail of every tool call; its integrity is
  itself an asset (it is the chain of custody from photons to processed stack).
- **The RSA auth material** — the firmware-7.18+ challenge-response private key. Today
  `seestar_alp` owns the :4700 handshake, so this server does not need the key; if that ever
  changes, the key is the highest-value secret in the system.

### Trust boundaries

```
Claude app (phone)  ──TLS──▶  Anthropic API  ──TLS──▶  Jetson (outbound HTTPS ONLY)
                                                          │  Claude Code process
                                                          ▼
                                                     seestar-mcp  ──HTTP──▶  seestar_alp
                                                     (stdio child)          (127.0.0.1:5555)
                                                                                 │  LAN
                                                                                 ▼
                                                                            Seestar S50
```

1. **Claude app ↔ Anthropic API ↔ Jetson** — the phone reaches the session only via
   Anthropic's API. The Jetson makes **outbound HTTPS only** and opens no inbound ports.
2. **seestar-mcp ↔ seestar_alp** — a local, loopback HTTP call to `127.0.0.1:5555`. The MCP
   server is a stdio child of Claude Code; it opens no listening socket of its own.
3. **seestar_alp ↔ Seestar** — over the LAN (Alpaca :5555 fronting native JSON-RPC :4700 /
   data :80 / SMB :445), ideally on an isolated IoT VLAN with a DHCP reservation.

### Remote Control note

Because remote phone access is handled by Claude Code Remote Control (the Jetson makes
outbound HTTPS only), **there is NO inbound network surface to harden beyond the local
ports.** The residual remote-access concern is account security (protect the Claude
account/MFA — pairing the app grants session control), not a listening service. Every port
this project touches is loopback or LAN and is never exposed publicly.

## OWASP MCP Top 10 — mitigations

| Risk | Our concrete control |
|---|---|
| **Tool poisoning** (hidden instructions in tool descriptions/schemas) | Tool descriptions are honest, human-readable, and non-obfuscated (see `server.py`); side effects are labelled `SIDE EFFECT` in plain English. Tools are statically defined in pinned code — **no dynamic/remote tool definitions**, no description mutation at runtime. |
| **Prompt injection via tool output** | Device telemetry and FITS data are treated as **data, not instructions**: tools return structured `dict`s, never executable text. The Skills explicitly instruct not to act on instructions embedded in device/FITS output. Every tool call and its result is recorded to the provenance log for after-the-fact review. |
| **Over-permissioned tools** | 18 single-purpose, least-privilege tools — each does exactly one thing. Motion/destructive tools (`goto_target`, `run_autofocus`, `set_filter`, `set_dew_heater`, `park`, `shutdown`, `download_subs`, `qa_session_report`) are clearly labelled, and the `run-session` / `anomaly-playbook` Skills gate motion and destructive operations behind **explicit user confirmation**. |
| **Supply chain** (rug-pulls / typosquats / drift) | Exact version pins in `pyproject.toml` and a **hash-locked `uv.lock`** (committed). An SBOM is generated via CycloneDX (`make sbom`), and a non-blocking `mcp-scan` check (`make scan`) watches for tool-poisoning / supply-chain findings. `seestar_alp` is vendored/pinned at a reviewed commit rather than tracking `main`. |
| **Prompt injection / insufficient sandboxing** | The daemon runs under a hardened systemd unit (`deploy/seestar-mcp.service`): a dedicated **unprivileged** user, `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `PrivateDevices`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, `RestrictNamespaces`, `LockPersonality`, `MemoryDenyWriteExecute`, `SystemCallFilter=@system-service`, and `ReadWritePaths` limited to the data/manifests dir. |
| **SSRF** (server tricked into fetching arbitrary URLs) | `data_client` only fetches from the **configured `seestar_host` on fixed ports** (:80 HTTP / :445 SMB) — there are **no user-controlled URLs**. The systemd unit adds an IP egress allowlist (`IPAddressDeny=any` + `IPAddressAllow=localhost <SEESTAR_IP>`) so even a bug cannot reach anything but the Seestar and localhost. |
| **Insecure updates** (firmware-fragile auth) | The firmware-7.18+ RSA handshake is isolated to `secrets.py` and single `# FIRMWARE-DEPENDENT` constants, so a firmware bump is a one-line key/constant swap in one place, not a code change. Dependencies are pinned; the auth/command map is the single updatable point. |
| **Insufficient logging** | Every tool call appends a structured record to an **append-only provenance JSONL** log (timestamp, tool, args, literal request, transaction IDs, response code, FITS hash), with recursive redaction of any sensitive field. Per-session manifests capture every command and QA verdict. |
| **Secrets exposure** | No secret ever enters config, source, or the audit log. `SecretStore` reads values on demand, never caches or logs them, and its `__repr__`/`status()` report only **presence** (`present`/`absent`), never values. The provenance layer redacts recursively. |
| **Network exposure** | All local ports are bound to **localhost/LAN, never public** (`bind_host` defaults to `127.0.0.1`). A `bind_host` field validator **warns** if it is ever set to a public/routable (non-loopback, non-RFC1918) address. The MCP server itself is stdio — it opens no listening socket at all. |

## Supply-chain runbook

The lockfile is **committed** — installs are reproducible and hash-pinned.

```bash
uv lock         # regenerate the hash-pinned lockfile (reproducible installs); commit it
make sbom       # write a CycloneDX SBOM (sbom.json) from the locked environment
make scan       # NON-BLOCKING mcp-scan supply-chain / tool-poisoning check
```

- `uv lock` produces `uv.lock` with per-dependency hashes; commit any change and review the
  diff (a surprising change is a signal to block the deploy).
- `make sbom` runs `cyclonedx-py environment` and emits a CycloneDX JSON SBOM artifact for
  diffing across builds.
- `make scan` runs `mcp-scan` and is **non-blocking** (leading `-` / `|| true`) so a missing
  tool or findings never fail the build; treat findings as review input, not a gate — until
  you choose to make it blocking in CI.

## Secrets handling

- The firmware-7.18+ RSA key and any tokens live in the gitignored `./secrets/` directory
  or in `SEESTAR_SECRET_*` environment variables. `.gitignore` already excludes `secrets/`,
  `*.pem`, `*.key`, and `.env`. **Secrets are never committed.**
- `secrets.py` (`SecretStore`) is the single load point. Values are read on demand and never
  cached, logged, or rendered by `__repr__`/`__str__`/`status()`.
- **Firmware updates (7.18+ RSA handshake) are a known, recurring breakage vector.** They
  are handled in exactly one place — `secrets.py` plus the `# FIRMWARE-DEPENDENT` constants
  — so recovering from a firmware bump is a one-line key/constant swap, never a code change.
  Today `seestar_alp` owns the :4700 handshake, so this server does not hold the key at all.

## Reporting

This is a private, high-assurance build. Security concerns should be raised directly to the
project owner. Any change that touches the auth path, the tool descriptions, the dependency
set, or the provenance/redaction logic must be reviewed against this document.

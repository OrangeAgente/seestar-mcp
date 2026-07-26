# Seestar S50 run-book skills

Five Claude Code skills that run a ZWO Seestar S50 observing night end to end: decide
whether it's worth imaging and what to shoot, acquire and monitor each target, react to
faults, score the data, and — if you want — run the whole night unattended.

The split is deliberate: **the MCP server provides access, these skills provide judgment.**
The [`seestar-mcp`](../README.md) server exposes single-purpose, provenance-logged tools
(slew, stack, assess conditions, score subs). The skills decide *whether* and *what*, and
never re-implement a tool.

## The skills

```
observing-planner    plan: go/no-go verdict + ranked target shortlist
        │  hands a chosen target to
        ▼
run-session          execute: acquire → focus → stack → monitor → wind down
        │                      │
        │ faults               │ sub quality
        ▼                      ▼
anomaly-playbook          qa-policy

autonomous-night     orchestrates all four for a whole unattended night
```

| Skill | Owns | Ask it for |
|---|---|---|
| `observing-planner` | *whether and what* | "plan tonight", "is it clear enough", "what should I image" |
| `run-session` | *how* — all motion | "image M31", "start a session", "wind down" |
| `anomaly-playbook` | faults | routed to automatically when something misbehaves |
| `qa-policy` | the numbers | "is this data good", "why was that sub rejected" |
| `autonomous-night` | sequencing + safety | "run the whole night", "image unattended" |

Each skill defers rather than duplicates, so install them together — used alone they will
reference guidance that isn't there.

**Not part of this bundle:** the repo also ships `image-refinement` and `astro-processing`
skills for stacking and post-processing finished images. They need extra software
(DeepSkyStacker, optionally PixInsight) and are not required to run an observing night.

## Glossary

Terms used throughout, roughly in the order you'll meet them:

| Term | Meaning |
|---|---|
| **Alpaca** | ASCOM Alpaca, a standard HTTP API for astronomy gear. `seestar_alp` exposes the Seestar over it; this project talks to that API, never to the scope directly. |
| **the bridge** | Shorthand for `seestar_alp` — the separate service that owns the connection to the telescope (default port `:5555`). |
| **plate-solve** | Working out exactly where the scope is pointing by matching the star field to a catalog. The scope does this itself; a failed solve means it cannot confirm its aim. |
| **sub** | One individual exposure (10 s by default). Many subs stack into the final image. |
| **keep-list** | The subs that passed quality scoring — the input to stacking. |
| **Tier-1 / Tier-2** | Tier-1 is live firmware telemetry (a *health* signal). Tier-2 is real measurement of the saved FITS files (the *quality* verdict). Only Tier-2 justifies calling data good. |
| **sweet band** | The altitude window worth imaging in: above your horizon floor, but below the field-rotation ceiling (~60°) where alt-az rotation trails stars fastest. |
| **LP / dual-band filter** | The Seestar's light-pollution filter. It passes the narrow emission lines (Hα/OIII) nebulae emit, so it helps emission targets under city skies and hurts broadband ones (galaxies, clusters). |
| **Bortle** | A 1–9 light-pollution scale for your site (1 = pristine, 9 = inner city). Used to rank which targets are realistic. |
| **dark window** | Tonight's span of true astronomical darkness — often short, and at high latitude in summer sometimes absent. |

## Setup

Four things must be in place before the skills are useful: `uv`, the bridge, the MCP server,
and the skills themselves.

### 1. Prerequisites

- **[`uv`](https://docs.astral.sh/uv/)** — the Python package manager every command below
  uses. Install it first.
- **Claude Code**, on a machine that stays awake near the scope. A laptop that sleeps will
  kill a session mid-run; unattended nights want a small always-on host.
- **The Seestar on your LAN in station mode**, ideally with a DHCP reservation so its address
  doesn't move.
- **"Save each frame in enhancing" enabled in the Seestar mobile app** — without it the scope
  keeps only the stacked result, and Tier-2 QA has no subs to score.

### 2. The bridge (`seestar_alp`)

[`seestar_alp`](https://github.com/smart-underworld/seestar_alp) owns the actual connection to
the telescope. Install and run it per its own documentation — it is a separate GPL-3.0
project, never bundled here — and pin it to a release you have reviewed. By default it serves
the Alpaca API on **`:5555`**, which is what this project talks to.

In its `config.toml`, note the **device number** for your scope (the shipped example uses
`1`); you need it in step 4.

**Firmware 7.18 and newer — effectively every scope sold today — requires an interop key.**
Without it the Seestar *silently ignores* commands: everything connects, nothing moves. The
bridge performs the challenge-response handshake if its `interop_pem` points at a key you
supply. **This project ships no key, no firmware, and no extraction tooling.** Obtaining a key
from your own licensed ZWO app is your responsibility, and the legality varies by jurisdiction
(in the US it is generally treated as interoperability under 17 U.S.C. §1201(f)). The
`seestar_alp` project and its community are where that procedure is discussed. If your scope
connects but ignores every command, this is almost always why.

Start the bridge **before** the MCP server, and give it time to finish connecting — it can
take a minute, and it may log one failed handshake before a retry succeeds.

### 3. This repo

```bash
git clone https://github.com/OrangeAgente/seestar-mcp
cd SeeStar-AI
uv sync
```

### 4. Register the MCP server

The server reads `SEESTAR_`-prefixed environment variables, which you can pass at
registration:

```bash
claude mcp add seestar-mcp \
  -e SEESTAR_ALPACA_BASE_URL=http://127.0.0.1:5555 \
  -e SEESTAR_ALPACA_DEVICE_NUM=1 \
  -e SEESTAR_SEESTAR_HOST=<your-scope-LAN-IP> \
  -- uv --directory "$PWD" run python -m seestar_mcp.server
```

Set `SEESTAR_ALPACA_DEVICE_NUM` to the device number from step 2 — if it is wrong, every
device call fails. Alternatively put the same `KEY=value` lines in a `.env` file in the repo
root (the server reads it on startup) and drop the `-e` flags.

Transport is stdio, so no port is opened. **Register before starting a Remote Control
session** — MCP servers cannot be added mid-session.

### 5. Make the skills visible

Claude Code discovers skills in `~/.claude/skills/` (everywhere) or `.claude/skills/` (per
project). Copy the bundle in:

```bash
mkdir -p ~/.claude/skills
cp -r skills/observing-planner skills/run-session skills/autonomous-night \
      skills/anomaly-playbook skills/qa-policy ~/.claude/skills/
```

### 6. Verify before dark

Ask Claude to check the link: `connect_telescope` then `get_status` should report connected.
If they error, the bridge is not up or not authenticated (step 2). Then try *"plan tonight"*
for a conditions verdict and a ranked target list. Both are read-only — nothing moves.

## Compatibility

Validated against a **Seestar S50** in **alt-az**, station mode, firmware **7.75**, with
`seestar-mcp` 0.1.0. EQ-mode (wedge) users should expect the field-rotation guidance to be
conservative — rotation is the alt-az constraint these skills are built around.

**Firmware changes are the expected breakage vector.** Device method names are the first
thing to check when a tool that used to work starts failing.

## Safety contract

These skills command real hardware: they slew a motorized mount, change filters and the
dew heater, and can run unattended for a whole night.

Confirmation works differently in the two modes, so be clear which one you are in:

**Attended (a single session).** Every motion command is confirmed individually: each slew,
enabling the dew heater, editing the horizon mask, parking, shutting down. Nothing moves
without you saying so that time.

**Unattended (an autonomous night).** You approve **once**, up front, against a no-motion dry
run that lists the planned targets. That one approval covers **every slew in the plan** — the
run then sequences targets, stacks, monitors, scores, and winds down on its own, without
asking again per target. It is a standing authorization, not a per-slew one, so read the dry
run before approving. Anything outside the approved plan (a new target, the dew heater, a
mask edit) still comes back to you, and the run always ends parked.

**Five hard stops, each ending in `park`:** approaching dawn, low battery, weather no-go
(precipitation or dew risk), lost connection or unverified scope, and maximum session
duration. Hard stops are **predictive** — one can fire while the sky still looks clear.
That is intended: the lead time is what lets the mount fold before weather arrives.

**Known consequence:** `park` points the optics at the cradle, from which the firmware
cannot plate-solve, so a parked scope needs a fresh alignment before it images again. Park
at wind-down, not to pause mid-session.

**Your responsibility:** your hardware, your sky, your neighbours, and your local rules.
Supervise the first few runs before trusting an unattended night, and check the weather
yourself — a forecast is not a guarantee.

## Optional: a rig profile

Your unit and your sky have quirks that are not true of Seestars in general — a measured
pointing offset, a blocked bearing, how much astronomical dark you actually get. Record them
in [`docs/RIG-PROFILE.md`](../docs/RIG-PROFILE.md) and `run-session` will read it at session
start. It is entirely optional; every skill works without it.

Keep real coordinates out of it — set those with `set_site_profile`, which stores them in
local, gitignored data.

## License and attribution

This repository is **MIT** licensed. `seestar_alp` is **GPL-3.0**, external, and is never
bundled, linked, or redistributed here — you install and pin it yourself. No ZWO key,
firmware, or app material is distributed with this project; the firmware-7.18+ interop key is
user-supplied.

## Contributing

Firmware moves, and one unit's behavior is not every unit's. Useful contributions:

- **Firmware changes** that break a device method — open an issue with the firmware version
  and the failing call.
- **Behavior that differs on your unit** — please report it as *your* observation rather than
  changing a skill to assert it universally. Unit-specific values belong in your own rig
  profile; skills should carry the *diagnostic*, not one rig's answer.
- **A clearer diagnostic** for a symptom the playbook handles badly.

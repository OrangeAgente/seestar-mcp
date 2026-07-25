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

## Prerequisites

1. **`seestar-mcp` registered in Claude Code** (stdio transport, no network port):
   ```bash
   claude mcp add seestar-mcp -- uv --directory /path/to/SeeStar-AI run python -m seestar_mcp.server
   ```
   Register **before** starting a Remote Control session — MCP servers can't be added
   mid-session.
2. **[`seestar_alp`](https://github.com/smart-underworld/seestar_alp) running and reachable.**
   It owns the device handshake; this project drives it over its local Alpaca HTTP API. It is
   installed separately and is never bundled here.
3. **The right Alpaca device number.** `seestar_alp` registers the scope at the number in its
   own config (the shipped example uses `1`). Set `SEESTAR_ALPACA_DEVICE_NUM` to match.
4. **A user-supplied interop key for firmware 7.18+.** Newer firmware silently ignores
   unauthenticated commands. `seestar_alp` performs the handshake if you point its
   `interop_pem` at a key **you** extract from your own licensed ZWO app. **This project
   ships no key and no firmware material.**
5. **"Save each frame in enhancing" enabled** on the scope, or Tier-2 QA has no subs to score.

## Compatibility

Validated against a **Seestar S50** in **alt-az**, station mode, firmware **7.75**, with
`seestar-mcp` 0.1.0. EQ-mode (wedge) users should expect the field-rotation guidance to be
conservative — rotation is the alt-az constraint these skills are built around.

**Firmware changes are the expected breakage vector.** Device method names are the first
thing to check when a tool that used to work starts failing.

## Safety contract

These skills command real hardware: they slew a motorized mount, change filters and the
dew heater, and can run unattended for a whole night.

**Always requires your explicit confirmation:** any slew or motion command, enabling the
dew heater, editing the horizon mask, parking, shutting down, and starting an autonomous
night (which begins with a no-motion dry run you must approve).

**Runs unattended once you approve a night:** target sequencing, stacking, monitoring,
quality scoring, and the wind-down.

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

## Install

Clone the repo, install the hash-locked environment, register the MCP server, and make the
skills visible to Claude Code (work in the repo, or copy `skills/` into your skills
location):

```bash
git clone https://github.com/OrangeAgente/SeeStar-AI
cd SeeStar-AI
uv sync
claude mcp add seestar-mcp -- uv --directory "$PWD" run python -m seestar_mcp.server
```

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

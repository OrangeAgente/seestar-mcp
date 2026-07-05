# Observing Planner (Phase 1) — Design Spec

**Date:** 2026-07-05
**Project:** seestar-mcp (auditable MCP server for a ZWO Seestar S50)
**Status:** Approved design — ready for implementation planning

---

## Goal

Turn `seestar-mcp` into a "professional astrophotographer" **planner**: given the observing site and tonight's date, produce (a) a **go/no-go conditions verdict** from weather + moon + twilight, and (b) a **ranked, reasoned target list** optimized for *clean* data — factoring altitude/airmass, the alt-az **field-rotation usable window**, moon separation/illumination, light-pollution fit, FOV framing, and astronomical-dark overlap. Delivered as new **local-computation MCP tools** plus a new **`observing-planner` skill**.

This is **Phase 1 of 3**. Phases 2 (Live Operator + Projects) and 3 (Autonomous Night) are roadmapped at the end and get their own specs.

## Design principles (inherited from the project)

- **MCP = access/compute, Skills = judgment.** Reproducible astronomy math lives in tools (auditable, testable); professional judgment lives in a skill.
- **Offline-first.** Only weather makes a network call. All ephemeris/observability/catalog computation is local and **deterministic** (timestamps are injected, never read from a hidden clock) so tests are stable and results are reproducible.
- **No unexplained verdicts.** Every conditions call and every target score carries human-readable **reasons** (same ethos as the Tier-2 QA layer).
- **Minimal footprint.** Reuse existing pinned deps — `astropy` for ephemeris (sun/moon/AltAz), `httpx` for weather, `numpy`. **No new heavy dependencies** in Phase 1 (no skyfield, no ephemeris binary, no large LP atlas).
- **Least privilege / no new inbound surface.** Weather is outbound HTTPS only; keyed-provider secrets (if ever used) come from `SecretStore`, never config. Planning tool calls are provenance-logged.

---

## Architecture

New package `src/seestar_mcp/planning/`:

| File | Responsibility | Key public interface |
|---|---|---|
| `site.py` | Observing-site profile + persistence + GPS fallback | `SiteProfile` dataclass; `load_site()`, `save_site(profile)`, `site_from_gps(lat, lon, ...)` |
| `catalog.py` | Bundled DSO catalog | `DsoTarget` dataclass; `load_catalog()`, `find_target(name_or_id)` |
| `astro.py` | Deterministic observability engine (astropy) | `dark_window(site, when)`, `observability(site, target, when)` → `Observability` |
| `weather.py` | Weather-source abstraction + Open-Meteo default | `WeatherSource` protocol; `OpenMeteoSource`; `assess(site, window)` → `ConditionsAssessment` |
| `lightpollution.py` | Light-pollution / Bortle → target-type suitability | `bortle_for(site)`, `lp_suitability(target_type, bortle)` |
| `ranker.py` | Fuse observability + conditions + LP + moon + framing → scored plan | `rank_targets(site, when, catalog, conditions, filters)` → `list[TargetPlan]` |

Data files (committed): `src/seestar_mcp/planning/data/dso_catalog.json`. Local state (gitignored): `data/site_profile.json`.

### Data models (dataclasses, JSON-serializable)

```python
@dataclass
class SiteProfile:
    name: str
    lat_deg: float
    lon_deg: float
    elevation_m: float = 0.0
    bortle: int | None = None          # 1 (pristine) .. 9 (inner city)
    sqm: float | None = None           # optional sky-quality mag/arcsec^2
    horizon_mask: list[tuple[float, float, float]] = field(default_factory=list)
    # each entry: (az_min_deg, az_max_deg, alt_min_deg) — below alt_min in that
    # azimuth arc is blocked (trees/buildings). Empty = flat open horizon.
    min_altitude_deg: float = 20.0     # global usable-altitude floor (airmass)

@dataclass
class DsoTarget:
    id: str                # e.g. "M27", "NGC7000", "C33"
    name: str              # common name, e.g. "Dumbbell Nebula"
    ra_deg: float
    dec_deg: float
    type: str              # emission_nebula|planetary_nebula|galaxy|open_cluster|globular_cluster|supernova_remnant|reflection_nebula
    size_arcmin: float     # largest angular extent
    magnitude: float | None = None

@dataclass
class Observability:
    target_id: str
    max_alt_deg: float
    transit_utc: str | None            # ISO, or None if never above floor
    rise_utc: str | None
    set_utc: str | None
    dark_minutes_above_floor: float    # minutes above min_altitude AND horizon mask AND in astro dark
    field_rotation_deg_per_hr_at_transit: float
    usable_sub_minutes: float          # minutes before field rotation smears a sub past threshold
    moon_sep_deg: float
    moon_alt_deg: float
    moon_illum_frac: float
    best_window_utc: tuple[str, str] | None   # (start, end) of best contiguous dark/high window

@dataclass
class ConditionsAssessment:
    go: bool | None                    # True/False, or None when weather unknown (offline)
    suitability: int                   # 0..100 imaging suitability
    cloud_cover_pct: float | None      # representative worst cloud over the dark window
    dew_risk: str                      # low|moderate|high (temp - dewpoint spread)
    wind_kph: float | None
    transparency: str | None           # good|average|poor proxy
    seeing: str | None                 # good|average|poor proxy
    moon_illum_frac: float
    dark_window_utc: tuple[str, str]
    source: str                        # "open-meteo" | "<keyed>" | "unknown"
    reasons: list[str]

@dataclass
class TargetPlan:
    target: DsoTarget
    score: int                         # 0..100
    reasons: list[str]                 # every score is explained
    best_window_utc: tuple[str, str] | None
    recommended_subs: int              # given usable field-rotation window / dark minutes
    recommended_exposure_s: int        # default 10 (Seestar), noted if LP suggests otherwise
    framing_note: str                  # fits FOV / too large (mosaic) / small
    observability: Observability
```

### The observability engine (`astro.py`) — the core

All via `astropy` (`EarthLocation`, `AltAz`, `get_sun`, `get_body("moon")`, `SkyCoord`, `Time`). A `when` (UTC `Time`/ISO) is always passed in — never `Time.now()` inside — so results are deterministic and testable.

- **Dark window:** sample the sun's altitude across the night; astronomical dark = sun < −18°. Return `(astro_dusk, astro_dawn)`.
- **Target track:** altitude/azimuth of the target across the dark window (sampled, e.g. 2-min grid).
- **Field rotation (alt-az specific):** instantaneous sky rotation rate
  `rate_deg_per_hr = 15.041 * cos(lat) * cos(az) / cos(alt)` (magnitude), evaluated along the track. `usable_sub_minutes` = time for accumulated rotation at the frame edge to reach a smear threshold (default: star trails < ~1.5 px across the ~1.3° FOV for a 10 s sub → parameterized). This is *the* "will I actually bank clean subs" number for the alt-az Seestar.
- **Horizon mask + floor:** minutes the target is simultaneously above `min_altitude_deg`, above the site `horizon_mask`, and within the dark window → `dark_minutes_above_floor`.
- **Moon:** phase/illumination, altitude, and angular separation from the target.
- **Best window:** the longest contiguous span that is dark + above floor + above mask.

### Ranking (`ranker.py`)

Score 0–100 = weighted, documented, configurable blend:
- **Observability** (dark minutes above floor, max altitude) — more good time ranks higher.
- **Field-rotation usable time** — a target with only a few clean minutes is penalized.
- **LP fit** (`lightpollution.lp_suitability`): under high Bortle, favor emission/planetary/SNR (narrowband-friendly) targets; on dark nights, allow broadband galaxies/reflection nebulae.
- **Moon penalty:** scaled by illumination × proximity (small separation to a bright moon hurts).
- **Framing fit:** compare `size_arcmin` to the Seestar FOV (~1.3°×0.7° ≈ 78'×42'); flag too-large (suggest mosaic) or very small.
- **Conditions gate:** if `assess_conditions` says no-go, planning still runs but every plan is annotated with the conditions caveat.

Each `TargetPlan.reasons` names the contributing factors, e.g.:
`"M27 — 82 | transits 68° at 01:10 UTC | 2.1h dark above 20° | field rotation ok (~34 min/sub-safe) | 41° from 12%-lit moon | emission target suits Bortle 6 | fits FOV (8')"`.

### Weather (`weather.py`)

- `WeatherSource` protocol: `assess(site, window) -> ConditionsAssessment`.
- `OpenMeteoSource` (default): one outbound HTTPS GET to `api.open-meteo.com` for hourly `cloudcover_low/mid/high`, `relativehumidity_2m`, `dewpoint_2m`, `temperature_2m`, `windspeed_10m`, `precipitation_probability` over the dark window's hours at the site lat/lon. Derive: representative cloud %, `dew_risk` from (temp − dewpoint) spread, wind, a coarse transparency proxy (humidity/cloud) and seeing proxy (wind/gradient), and a 0–100 `suitability`. `go` = suitability ≥ threshold and no precip.
- **Pluggable keyed provider:** the protocol allows a future `KeyedSource` that reads its API key from `SecretStore` (never config). Not implemented in Phase 1, but the seam exists.
- **Network failure is non-fatal:** on timeout/error, return `ConditionsAssessment(go=None, source="unknown", suitability=0, reasons=["weather unavailable — assess sky manually"])`. Planning proceeds on observability alone.

### Light pollution (`lightpollution.py`)

Phase-1 lightweight: `bortle_for(site)` returns `site.bortle` if set; else a coarse estimate (small bundled lat/lon→Bortle approximation table, or a documented default) — a *full* LP atlas is out of scope for Phase 1. `lp_suitability(target_type, bortle)` returns a 0–1 multiplier used by the ranker (emission/narrowband high at Bortle 6–9; broadband targets high at Bortle 1–4).

---

## New MCP tools (a "planning" group)

Thin wrappers over the `planning` module on the existing `SeestarController`/FastMCP, honoring the `{ "ok": ... }` convention and provenance logging. Clear, honest descriptions.

| Tool | Purpose |
|---|---|
| `get_site_profile()` | Return the stored site profile (or a note that none is set). |
| `set_site_profile(name, lat, lon, elevation_m?, bortle?, sqm?, horizon_mask?, min_altitude_deg?)` | Create/update the profile. |
| `assess_conditions(date?)` | Weather + moon + twilight → go/no-go verdict + reasons + dark window. |
| `get_target_observability(target, date?)` | Deep-dive one object → full `Observability` + framing + recommended subs. |
| `plan_targets(date?, types?, min_alt?, limit?)` | Ranked `TargetPlan` list with reasons, best windows, recommended integration. |

`date` defaults to "tonight" — resolved by the tool layer (which may read the clock) and passed as an explicit timestamp into the deterministic engine. Site latitude/longitude come from the stored profile, or `site_from_gps()` using the scope's `get_device_state` GPS when no profile is set (the "site changes during planning" fallback).

## New skill: `observing-planner`

`skills/observing-planner/SKILL.md` — how a pro builds and presents a nightly plan. Outline:
- **Phase 0 — conditions:** load site; `assess_conditions`; state the **go/no-go** verdict in one line with the driving reason (clouds/moon/dark window). If no-go, say why and stop (offer to plan anyway for a partial/clearing window).
- **Phase 1 — target shortlist:** `plan_targets`; present a compact ranked shortlist with each target's best window, recommended integration (from the field-rotation usable window), and the one-line "why". Sequence suggestions to catch each target near transit and minimize slews. Respect the horizon mask (never suggest a blocked target). Apply field-rotation-aware integration limits (alt-az) and LP-appropriate selection; note moon avoidance and dew risk.
- **Phase 2 — hand off:** the chosen target(s) go to the existing `run-session` skill for execution; QA interpretation stays with `qa-policy`.
- **Output discipline:** compact, phone-friendly (Remote Control). Lead with the verdict and top 3 targets.

The skill *consults* the tools for numbers (never invents ephemeris/weather), interprets them, and defers execution/QA to the existing skills.

## Data flow

```
"plan tonight"
  → observing-planner skill
    → assess_conditions   (weather + moon + twilight → go/no-go)
    → plan_targets        (observability engine over catalog, site/horizon/LP filtered, scored)
  → ranked plan (verdict + top targets + windows + integration)
  → run-session executes the chosen target
```

## Error handling

- **Weather down/timeout** → `assess_conditions` returns `go=None` ("unknown"); planning continues on observability alone (offline-capable).
- **No site profile** → tools fall back to scope GPS (`site_from_gps`) + a default Bortle, and the skill prompts the user to set a profile for better ranking.
- **Unknown target name** → `get_target_observability`/`find_target` returns a clear `{ok: false, error}` with near-matches.
- **Target never above floor tonight** → `Observability` with `transit_utc=None`, `dark_minutes_above_floor=0`; ranker drops it with a reason.
- All astronomy is deterministic (timestamp injected) → no flaky tests; never raises on bad input (returns structured results).

## Testing

- **`astro.py`:** fixed site + fixed UTC (e.g. lat 40.0, lon −74.0, 2026-07-05T04:00Z) → assert transit time, max altitude, moon illumination/separation within tolerance (astropy is deterministic). Field-rotation formula unit-tested at known (lat, alt, az) points (e.g. rate → 0 near the pole/az=90°·alt handling). Dark-window sun-altitude logic tested.
- **`weather.py`:** `respx`-mock an Open-Meteo JSON response → known `ConditionsAssessment` (cloud/dew/suitability/go); simulated network error → `go=None, source="unknown"`, non-fatal.
- **`ranker.py`:** synthetic small catalog + fixed conditions → deterministic ordering; assert reasons present and that a horizon-blocked / never-up target is excluded with a reason; LP fit flips ordering between Bortle 3 and Bortle 8.
- **`catalog.py`/`site.py`:** catalog loads and validates; profile round-trips to JSON; `horizon_mask` blocking logic.
- **tools/skill:** the planning tools register (exact names); a couple driven with a mocked engine returning canned data; assert `{ok:...}` + provenance record. `observing-planner/SKILL.md` frontmatter validates.
- **No network in tests** (weather mocked; ephemeris local). No real hardware.

## Security / reproducibility

- Only weather calls the network (Open-Meteo, or a future keyed provider whose key comes from `SecretStore`). Outbound HTTPS only; **no new inbound surface**. The systemd egress allowlist (SECURITY.md) must add the weather host when weather is enabled.
- Deps unchanged or minimally extended; `uv.lock` re-locked + hash-pinned. Planning tool calls are provenance-logged (no secrets).
- Deterministic engine → reproducible plans; every verdict/score is reason-tagged and auditable.

---

## Roadmap (separate specs)

- **Phase 2 — Live Operator + Projects:** persistent projects/history store (`log_session_result`, integration goals, avoid repeats, "what needs more data?"); expand `run-session` to consult the planner + monitor weather live + react (switch target when one sets, pause/abort on incoming clouds); expand `anomaly-playbook` with weather-driven aborts.
- **Phase 3 — Autonomous Night:** an `autonomous-night` skill running an unattended loop (assess → execute ranked plan → react to conditions/QA → wind down + park at dawn) behind **hard guardrails** (max session, cloud/dew/battery abort thresholds, park-on-fault), a **dry-run/simulate** mode, provenance of every decision, and phone notifications via Remote Control.

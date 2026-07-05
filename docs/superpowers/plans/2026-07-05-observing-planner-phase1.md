# Observing Planner (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pre-session "observing planner" to seestar-mcp — a go/no-go conditions verdict and a ranked, reasoned DSO target list optimized for clean data on the alt-az Seestar S50.

**Architecture:** New local-computation `src/seestar_mcp/planning/` package (deterministic astropy ephemeris + Open-Meteo weather + a bundled DSO catalog + a scoring ranker), exposed as 5 new MCP tools on the existing FastMCP `SeestarController`, plus a new `observing-planner` skill. MCP = access/compute; Skill = judgment. Offline-first (only weather calls the network); every verdict/score is reason-tagged.

**Tech Stack:** Python 3.12, `astropy` (existing pin — ephemeris/AltAz/moon/sun), `httpx` (existing — weather), `numpy`, `pydantic`, FastMCP (`mcp`). Tests: `pytest`, `pytest-asyncio`, `respx`. Tooling: `uv`.

## Global Constraints

- Run everything via `uv`: `uv run pytest`, `uv run ruff check src tests`. NEVER bare `python`.
- **No new heavy dependencies.** Ephemeris uses `astropy` (already pinned); weather uses `httpx` (already pinned). If any new pin is unavoidable, stop and flag it.
- **Determinism:** no function in `planning/` may read the clock. All astronomy takes an explicit UTC timestamp (`astropy.time.Time` or ISO string). The *tool* layer resolves "tonight" and passes a concrete timestamp down. This keeps tests stable.
- **No unexplained verdicts:** every `ConditionsAssessment` and `TargetPlan` carries a `reasons: list[str]`.
- **Never raise on bad input** in tool-facing paths — return structured `{"ok": false, "error": ...}` (controller) or an error-tagged result (engine), matching existing modules.
- **Offline-first:** only `weather.py` makes a network call; a weather failure is non-fatal (`go=None`, `source="unknown"`).
- Commit each task with the trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and use `git -c core.autocrlf=false commit`.
- Field-rotation model: rank on **sweet-band** time `[min_altitude_deg, field_rotation_ceiling_deg]` (default 20°..60°); penalize near-zenith transits. Do NOT reward raw altitude.
- Spec of record: `docs/superpowers/specs/2026-07-05-observing-planner-design.md` (read it).

---

## File Structure

- `src/seestar_mcp/planning/__init__.py` — package marker + re-exports.
- `src/seestar_mcp/planning/catalog.py` — `DsoTarget`, `load_catalog()`, `find_target()`.
- `src/seestar_mcp/planning/data/dso_catalog.json` — bundled catalog (committed).
- `src/seestar_mcp/planning/site.py` — `SiteProfile`, `load_site()`, `save_site()`, `site_from_gps()`, `is_blocked()`.
- `src/seestar_mcp/planning/astro.py` — `dark_window()`, `field_rotation_rate()`, `observability()`, `Observability`.
- `src/seestar_mcp/planning/lightpollution.py` — `bortle_for()`, `lp_suitability()`.
- `src/seestar_mcp/planning/weather.py` — `WeatherSource`, `OpenMeteoSource`, `ConditionsAssessment`, `assess_conditions()`.
- `src/seestar_mcp/planning/ranker.py` — `TargetPlan`, `rank_targets()`.
- `src/seestar_mcp/server.py` — MODIFY: add 5 controller methods + 5 `@mcp.tool` wrappers.
- `skills/observing-planner/SKILL.md` — the judgment skill (verbatim content in Task 8).
- `SECURITY.md`, `deploy/seestar-mcp.service` — MODIFY: note the weather egress host.
- Tests: `tests/test_planning_catalog.py`, `_site.py`, `_astro.py`, `_lightpollution.py`, `_weather.py`, `_ranker.py`, `_planning_tools.py`.

---

### Task 1: DSO catalog

**Files:**
- Create: `src/seestar_mcp/planning/__init__.py`, `src/seestar_mcp/planning/catalog.py`, `src/seestar_mcp/planning/data/dso_catalog.json`
- Test: `tests/test_planning_catalog.py`

**Interfaces:**
- Produces: `DsoTarget(id:str, name:str, ra_deg:float, dec_deg:float, type:str, size_arcmin:float, magnitude:float|None)`; `load_catalog() -> list[DsoTarget]`; `find_target(name_or_id:str) -> DsoTarget|None` (case-insensitive match on `id` or `name`, tolerant of spaces).

- [ ] **Step 1: Write failing tests**
```python
# tests/test_planning_catalog.py
from seestar_mcp.planning.catalog import load_catalog, find_target, DsoTarget

def test_catalog_loads_known_targets():
    cat = load_catalog()
    assert len(cat) >= 30
    ids = {t.id for t in cat}
    assert {"M27", "M31", "M42", "M13", "M51"} <= ids
    for t in cat:
        assert -360 <= t.ra_deg <= 360 and -90 <= t.dec_deg <= 90
        assert t.type and t.size_arcmin > 0

def test_find_target_by_id_and_name():
    assert find_target("m27").id == "M27"
    assert find_target("Dumbbell Nebula").id == "M27"
    assert find_target("nonexistent") is None
```
- [ ] **Step 2: Run — expect FAIL** (`uv run pytest tests/test_planning_catalog.py -v` → ModuleNotFoundError).
- [ ] **Step 3: Implement.** Create `data/dso_catalog.json` as a list of objects `{id,name,ra_deg,dec_deg,type,size_arcmin,magnitude}` covering **all 110 Messier objects** plus a handful of popular Caldwell/NGC (M27 Dumbbell, M31 Andromeda, M42 Orion, M13, M51, M8 Lagoon, M20 Trifid, M57 Ring, M45, NGC7000 North America, C33/C34 Veil, etc.). Use J2000 coords in **degrees**. `type` ∈ {emission_nebula, planetary_nebula, galaxy, open_cluster, globular_cluster, supernova_remnant, reflection_nebula, other}. `catalog.py`: `@dataclass DsoTarget`; `load_catalog()` reads the JSON relative to `__file__` (`Path(__file__).parent/"data"/"dso_catalog.json"`), returns `[DsoTarget(**row) ...]`; `find_target()` normalizes (`.strip().lower().replace(" ", "")`) and matches id then name.
- [ ] **Step 4: Run — expect PASS.** Also `uv run ruff check src tests`.
- [ ] **Step 5: Commit** `git add src/seestar_mcp/planning/__init__.py src/seestar_mcp/planning/catalog.py src/seestar_mcp/planning/data/dso_catalog.json tests/test_planning_catalog.py` → `feat(planning): bundled DSO catalog + loader`.

---

### Task 2: Site profile

**Files:**
- Create: `src/seestar_mcp/planning/site.py`
- Test: `tests/test_planning_site.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SiteProfile` dataclass (fields per spec: `name, lat_deg, lon_deg, elevation_m=0.0, bortle:int|None=None, sqm:float|None=None, horizon_mask:list[tuple[float,float,float]]=[], min_altitude_deg=20.0, field_rotation_ceiling_deg=60.0`); `load_site(path:Path|None=None) -> SiteProfile|None`; `save_site(profile, path=None) -> Path`; `site_from_gps(lat, lon, elevation_m=0.0) -> SiteProfile`; `is_blocked(profile, az_deg, alt_deg) -> bool` (True if below `min_altitude_deg`, or within a horizon-mask arc and below that arc's `alt_min`).

- [ ] **Step 1: Write failing tests**
```python
# tests/test_planning_site.py
from seestar_mcp.planning.site import SiteProfile, save_site, load_site, site_from_gps, is_blocked

def test_profile_roundtrip(tmp_path):
    p = SiteProfile(name="Backyard", lat_deg=40.0, lon_deg=-74.0, bortle=6,
                    horizon_mask=[(45.0, 135.0, 30.0)])
    path = save_site(p, tmp_path / "site.json")
    got = load_site(path)
    assert got.name == "Backyard" and got.bortle == 6
    assert got.horizon_mask == [(45.0, 135.0, 30.0)]

def test_load_missing_returns_none(tmp_path):
    assert load_site(tmp_path / "nope.json") is None

def test_horizon_mask_blocking():
    p = SiteProfile(name="x", lat_deg=40, lon_deg=-74, horizon_mask=[(45.0,135.0,30.0)])
    assert is_blocked(p, az_deg=90, alt_deg=25) is True    # in arc, below 30
    assert is_blocked(p, az_deg=90, alt_deg=35) is False   # in arc, above 30
    assert is_blocked(p, az_deg=200, alt_deg=25) is True   # outside arc but below global floor 20? 25>20 -> False
    assert is_blocked(p, az_deg=200, alt_deg=15) is True   # below global floor

def test_site_from_gps():
    s = site_from_gps(37.5, -122.0)
    assert s.lat_deg == 37.5 and s.bortle is None
```
- [ ] **Step 2: Run — expect FAIL.** (Note: fix the `az=200,alt=25` assertion during Step 3 review — 25>20 and outside arc → `False`; correct the test to `is False` before it can pass.)
- [ ] **Step 3: Implement.** `@dataclass SiteProfile`. `save_site` → `json.dump(asdict(profile))` (convert tuples to lists) to `path` (default `data/site_profile.json`), return path. `load_site` → None if missing; else parse, restoring `horizon_mask` tuples. `site_from_gps` → `SiteProfile(name="GPS", lat_deg=lat, lon_deg=lon, elevation_m=elevation_m)`. `is_blocked(profile, az, alt)`: `if alt < profile.min_altitude_deg: return True`; for each `(a0,a1,amin)` in mask, if `a0 <= az <= a1 and alt < amin: return True`; else `False`.
- [ ] **Step 4: Run — expect PASS** + ruff.
- [ ] **Step 5: Commit** `feat(planning): site profile (persistence, GPS fallback, horizon mask)`.

---

### Task 3: Observability engine (astro.py)

**Files:**
- Create: `src/seestar_mcp/planning/astro.py`
- Test: `tests/test_planning_astro.py`

**Interfaces:**
- Consumes: `SiteProfile`, `is_blocked` (Task 2); `DsoTarget` (Task 1).
- Produces: `field_rotation_rate(lat_deg, az_deg, alt_deg) -> float` (deg/hr, magnitude); `dark_window(site, when_utc) -> tuple[str,str]` (astro dusk/dawn ISO, sun < -18°); `observability(site, target, when_utc) -> Observability` (dataclass exactly per spec). `when_utc` is an ISO string or `astropy.time.Time`.

- [ ] **Step 1: Write failing tests** (hand-verifiable physics + astropy-pinned tolerances)
```python
# tests/test_planning_astro.py
import math
from seestar_mcp.planning.astro import field_rotation_rate, observability, dark_window
from seestar_mcp.planning.site import SiteProfile
from seestar_mcp.planning.catalog import find_target

def test_field_rotation_formula_hand_check():
    # 15.041 * cos(40) * cos(0) / cos(45) = 16.29 deg/hr
    r = field_rotation_rate(lat_deg=40.0, az_deg=0.0, alt_deg=45.0)
    assert abs(r - 16.29) < 0.1
    # az=90 (due east) -> cos(az)=0 -> rate ~0
    assert field_rotation_rate(40.0, 90.0, 45.0) < 0.01

def test_m27_transit_altitude_and_ceiling_flag():
    # transit alt = 90 - |lat - dec|; M27 dec +22.72, lat 40 -> ~72.7 deg (above 60 ceiling)
    site = SiteProfile(name="x", lat_deg=40.0, lon_deg=-74.0, bortle=6)
    obs = observability(site, find_target("M27"), "2026-07-05T04:00:00Z")
    assert 71.0 < obs.max_alt_deg < 74.0
    assert obs.transits_above_ceiling is True
    assert obs.dark_minutes_in_sweet_band <= obs.dark_minutes_above_floor
    assert 0.0 <= obs.moon_illum_frac <= 1.0

def test_dark_window_is_night():
    site = SiteProfile(name="x", lat_deg=40.0, lon_deg=-74.0)
    dusk, dawn = dark_window(site, "2026-07-05T04:00:00Z")
    assert dusk < dawn   # ISO strings compare lexically for same-format UTC
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** with astropy. `EarthLocation.from_geodetic(lon, lat, height)`. `field_rotation_rate = abs(15.041 * cos(radians(lat)) * cos(radians(az)) / cos(radians(alt)))` (guard `alt≈90` → clamp `cos(alt)` to ≥1e-6). `dark_window`: build a `Time` grid (e.g. from 12:00 UTC that day for 24h at 5-min steps — actually center on the given night: from `when - 12h` to `when + 12h`), compute `get_sun(times).transform_to(AltAz(location, times)).alt`; the contiguous span with alt < −18° that contains local midnight → dusk/dawn; return ISO. `observability`: build a 2-min grid over the dark window; `SkyCoord(ra_deg, dec_deg, unit=deg).transform_to(AltAz)`; per sample compute alt, az, `is_blocked`, whether in `[min_alt, ceiling]`. Sum minutes for `dark_minutes_above_floor` (alt≥floor & not blocked) and `dark_minutes_in_sweet_band` (floor≤alt≤ceiling & not blocked). `max_alt_deg`=max sample alt; `transit_utc`=time of max; `transits_above_ceiling = max_alt_deg > ceiling`. Moon via `get_body("moon", times, location)`: illumination from sun-moon elongation (`0.5*(1 - cos(elong))`), sep = target.separation(moon) at transit, moon alt at transit. Field rotation: `field_rotation_deg_per_hr_at_transit` at the max-alt sample; `usable_sub_minutes` = smear-threshold minutes = `(threshold_deg / rate)`*60 where `threshold_deg` = angular rotation giving ~1.5px at FOV edge for a 10s sub (document: `threshold_deg = degrees(1.5 * pixscale_rad ... )` — use a simple parameter `SMEAR_DEG=0.03` and note it's tunable). `best_window_utc` = longest contiguous sweet-band+unblocked span. Return `Observability`. Never raise: on any error return an `Observability` with zeros and `transit_utc=None`.
- [ ] **Step 4: Run — expect PASS** + ruff. If a pinned astropy value is off, compute the real value once (`uv run python -c "..."`) and set the assertion to that value ± tolerance (documented as astropy-derived).
- [ ] **Step 5: Commit** `feat(planning): astropy observability engine with field-rotation sweet-band`.

---

### Task 4: Light pollution

**Files:**
- Create: `src/seestar_mcp/planning/lightpollution.py`
- Test: `tests/test_planning_lightpollution.py`

**Interfaces:**
- Consumes: `SiteProfile`.
- Produces: `bortle_for(site) -> int` (site.bortle if set, else a documented default of 5); `lp_suitability(target_type:str, bortle:int) -> float` (0..1 multiplier).

- [ ] **Step 1: Write failing tests**
```python
# tests/test_planning_lightpollution.py
from seestar_mcp.planning.lightpollution import bortle_for, lp_suitability
from seestar_mcp.planning.site import SiteProfile

def test_bortle_uses_profile_then_default():
    assert bortle_for(SiteProfile(name="x", lat_deg=0, lon_deg=0, bortle=8)) == 8
    assert bortle_for(SiteProfile(name="x", lat_deg=0, lon_deg=0)) == 5

def test_lp_suitability_favours_emission_under_high_lp():
    # bright city (Bortle 8): emission/narrowband targets favoured over galaxies
    assert lp_suitability("emission_nebula", 8) > lp_suitability("galaxy", 8)
    # dark site (Bortle 3): galaxies fine
    assert lp_suitability("galaxy", 3) >= 0.8
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** `bortle_for` returns `site.bortle if site.bortle is not None else 5`. `lp_suitability`: narrowband-friendly types (`emission_nebula, planetary_nebula, supernova_remnant`) → high suitability that stays ~1.0 even at Bortle 8; broadband types (`galaxy, reflection_nebula, open_cluster`) → suitability that falls as Bortle rises (e.g. `max(0.2, 1.0 - 0.12*(bortle-3))`); globulars mid. Return clamped [0,1]. Document the mapping table in a module constant.
- [ ] **Step 4: Run — expect PASS** + ruff.
- [ ] **Step 5: Commit** `feat(planning): light-pollution Bortle + target-type suitability`.

---

### Task 5: Weather (Open-Meteo)

**Files:**
- Create: `src/seestar_mcp/planning/weather.py`
- Test: `tests/test_planning_weather.py`

**Interfaces:**
- Consumes: `SiteProfile`.
- Produces: `ConditionsAssessment` dataclass (per spec); `class OpenMeteoSource` with `async assess(self, site, window_utc:tuple[str,str], *, client:httpx.AsyncClient|None=None) -> ConditionsAssessment`; a `WeatherSource` Protocol; module async `assess_conditions(site, window_utc, moon_illum_frac, source=None, client=None) -> ConditionsAssessment` that merges weather + the caller-supplied moon illumination + dark window into the final verdict.

- [ ] **Step 1: Write failing tests** (`respx`, `asyncio_mode=auto`)
```python
# tests/test_planning_weather.py
import httpx, respx
from seestar_mcp.planning.weather import OpenMeteoSource, assess_conditions
from seestar_mcp.planning.site import SiteProfile

SITE = SiteProfile(name="x", lat_deg=40.0, lon_deg=-74.0)
WINDOW = ("2026-07-05T02:00:00Z", "2026-07-05T08:00:00Z")

@respx.mock
async def test_clear_night_is_go():
    respx.get(url__startswith="https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json={"hourly": {
            "time": ["2026-07-05T02:00","2026-07-05T03:00"],
            "cloudcover_low":[0,5], "cloudcover_mid":[0,0], "cloudcover_high":[10,10],
            "relativehumidity_2m":[60,62], "dewpoint_2m":[8,8], "temperature_2m":[18,17],
            "windspeed_10m":[5,6], "precipitation_probability":[0,0]}}))
    a = await assess_conditions(SITE, WINDOW, moon_illum_frac=0.1)
    assert a.go is True and a.suitability >= 60 and a.source == "open-meteo"

@respx.mock
async def test_network_failure_is_unknown_not_fatal():
    respx.get(url__startswith="https://api.open-meteo.com").mock(side_effect=httpx.ConnectError("down"))
    a = await assess_conditions(SITE, WINDOW, moon_illum_frac=0.1)
    assert a.go is None and a.source == "unknown" and "manual" in " ".join(a.reasons).lower()
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** `OpenMeteoSource.assess`: GET `https://api.open-meteo.com/v1/forecast` with params `latitude, longitude, hourly="cloudcover_low,cloudcover_mid,cloudcover_high,relativehumidity_2m,dewpoint_2m,temperature_2m,windspeed_10m,precipitation_probability", timezone="UTC", forecast_days=2`. Filter hourly rows to the window. Representative `cloud_cover_pct` = max total cloud over window (`low+mid+high` capped 100, or max of the three — document choice: use max of the three layers). `dew_risk` from min `(temp - dewpoint)` spread (<2°→high, <5°→moderate, else low). `suitability` 0..100 = start 100, subtract cloud%, subtract wind penalty, subtract precip penalty. `go = suitability >= 50 and max(precip_prob) < 40`. transparency proxy from humidity+cloud; seeing proxy from wind. Wrap the whole HTTP call in `try/except httpx.RequestError` → return `ConditionsAssessment(go=None, source="unknown", suitability=0, ... reasons=["weather unavailable — assess the sky manually"])`. `assess_conditions` merges the moon illumination into `moon_illum_frac`, sets `dark_window_utc=window_utc`, and appends a moon reason (bright moon lowers suitability but does not force no-go).
- [ ] **Step 4: Run — expect PASS** + ruff.
- [ ] **Step 5: Commit** `feat(planning): Open-Meteo weather source + conditions verdict`.

---

### Task 6: Ranker

**Files:**
- Create: `src/seestar_mcp/planning/ranker.py`
- Test: `tests/test_planning_ranker.py`

**Interfaces:**
- Consumes: `DsoTarget`, `SiteProfile`, `observability`/`Observability`, `bortle_for`/`lp_suitability`, `ConditionsAssessment`.
- Produces: `TargetPlan` dataclass (per spec); `rank_targets(site, when_utc, catalog, conditions, *, types=None, min_alt=None, limit=None, observability_fn=observability) -> list[TargetPlan]` (sorted desc by score). `observability_fn` injectable for tests.

- [ ] **Step 1: Write failing tests** (inject a fake `observability_fn` for determinism)
```python
# tests/test_planning_ranker.py
from seestar_mcp.planning.ranker import rank_targets, TargetPlan
from seestar_mcp.planning.astro import Observability
from seestar_mcp.planning.site import SiteProfile
from seestar_mcp.planning.catalog import DsoTarget
from seestar_mcp.planning.weather import ConditionsAssessment

def _obs(minutes_band, above_ceiling=False, sep=90.0):
    return Observability(target_id="t", max_alt_deg=50.0, transit_utc="2026-07-05T04:00:00Z",
        rise_utc=None, set_utc=None, dark_minutes_above_floor=minutes_band+30,
        dark_minutes_in_sweet_band=minutes_band, field_rotation_deg_per_hr_at_transit=10.0,
        usable_sub_minutes=40.0, transits_above_ceiling=above_ceiling, moon_sep_deg=sep,
        moon_alt_deg=20.0, moon_illum_frac=0.1, best_window_utc=("a","b"))

def test_more_sweet_band_time_ranks_higher():
    site = SiteProfile(name="x", lat_deg=40, lon_deg=-74, bortle=6)
    cond = ConditionsAssessment(go=True, suitability=90, cloud_cover_pct=5, dew_risk="low",
        wind_kph=5, transparency="good", seeing="good", moon_illum_frac=0.1,
        dark_window_utc=("2026-07-05T02:00:00Z","2026-07-05T08:00:00Z"), source="open-meteo", reasons=[])
    cat = [DsoTarget("A","A",0,0,"emission_nebula",20,7), DsoTarget("B","B",0,0,"emission_nebula",20,7)]
    obs_map = {"A": _obs(120), "B": _obs(20)}
    plans = rank_targets(site, "2026-07-05T04:00:00Z", cat, cond,
                         observability_fn=lambda s,t,w: obs_map[t.id])
    assert [p.target.id for p in plans] == ["A", "B"]
    assert plans[0].reasons  # non-empty

def test_never_up_target_excluded():
    site = SiteProfile(name="x", lat_deg=40, lon_deg=-74)
    cond = ConditionsAssessment(None,0,None,"low",None,None,None,0.1,("a","b"),"unknown",[])
    cat = [DsoTarget("Z","Z",0,-80,"galaxy",10,9)]
    plans = rank_targets(site, "2026-07-05T04:00:00Z", cat, cond,
                         observability_fn=lambda s,t,w: _obs(0))  # zero sweet-band
    assert plans == []  # dropped
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** `rank_targets`: filter catalog by `types`; for each, call `observability_fn(site, target, when_utc)`; **drop** targets with `dark_minutes_in_sweet_band <= 0` (reason logged but excluded from output). Score 0..100 = weighted sum: sweet-band minutes (normalized, primary weight ~0.4), `usable_sub_minutes` (0.2), `lp_suitability(target.type, bortle_for(site))` (0.2), moon penalty `min(1, moon_sep/90) * (1 - 0.5*moon_illum)` (0.1), framing fit (0.1: 1.0 if `size_arcmin` within ~[5,70], lower if too big/small). Apply a penalty and reason if `transits_above_ceiling`. **Sub-count math (corrected):** `recommended_subs = int(dark_minutes_in_sweet_band * 60 / recommended_exposure_s)` — total clean sweet-band time ÷ exposure; `recommended_exposure_s=10`. Do NOT multiply by `usable_sub_minutes` — that is the *at-transit worst-case* per-sub smear window (tiny for near-zenith targets), not a total-time budget. Use `transits_above_ceiling` and a low sweet-band fraction (`dark_minutes_in_sweet_band / max(1, dark_minutes_above_floor)`) as the field-rotation penalty; if `usable_sub_minutes*60 < recommended_exposure_s`, add a reason that subs trail near transit. `framing_note` from size vs FOV (78'×42'). Build `reasons` naming each contributing factor (see spec example). Sort desc by score; apply `limit`. Never raise.
- [ ] **Step 4: Run — expect PASS** + ruff.
- [ ] **Step 5: Commit** `feat(planning): target ranker (sweet-band + LP + moon + framing, reasoned)`.

---

### Task 7: MCP planning tools

**Files:**
- Modify: `src/seestar_mcp/server.py` (add controller methods + `@mcp.tool` wrappers)
- Test: `tests/test_planning_tools.py`

**Interfaces:**
- Consumes: everything above; existing `SeestarController`, `mcp`, `get_controller`.
- Produces: controller methods `get_site_profile()`, `set_site_profile(...)`, `assess_conditions(date=None)`, `get_target_observability(target, date=None)`, `plan_targets(date=None, types=None, min_alt=None, limit=None)` — each returns a JSON-serializable dict with `ok`; and 5 `@mcp.tool` wrappers of the same names. `date` default None → tool resolves "tonight" as an ISO UTC string (this is the ONLY place the clock is read; import inside the method).

- [ ] **Step 1: Write failing tests** (drive the controller directly, mock the engine/weather)
```python
# tests/test_planning_tools.py
import asyncio
from seestar_mcp.server import mcp

async def _tool_names():
    return {t.name for t in await mcp.list_tools()}

def test_planning_tools_registered():
    names = asyncio.run(_tool_names())
    assert {"get_site_profile","set_site_profile","assess_conditions",
            "get_target_observability","plan_targets"} <= names

def test_set_then_get_site_profile(tmp_path, monkeypatch):
    from seestar_mcp.server import SeestarController
    monkeypatch.setenv("SEESTAR_DATA_DIR", str(tmp_path))  # if site path derives from data_dir
    c = SeestarController.from_settings()
    r = asyncio.run(c.set_site_profile(name="Yard", lat=40.0, lon=-74.0, bortle=6))
    assert r["ok"] is True
    g = asyncio.run(c.get_site_profile())
    assert g["ok"] and g["profile"]["bortle"] == 6
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** Add controller methods delegating to `planning.*`. `set_site_profile` → build `SiteProfile`, `save_site` (path under `settings.data_dir/"site_profile.json"`), return `{"ok":True,"profile":asdict}`. `get_site_profile` → `load_site`; if None `{"ok":False,"error":"no site profile set"}`. For `assess_conditions`/`get_target_observability`/`plan_targets`: resolve `when = date or datetime.now(timezone.utc).isoformat()`; load site (fallback to `site_from_gps` via a best-effort `get_device_state` GPS read, else `{"ok":False,"error":"no site"}`); compute `dark_window`; for conditions call `await assess_conditions(...)` with moon illum from a quick `observability` of any target or a dedicated moon calc; for `plan_targets` call `rank_targets(load_catalog(), ...)` and return `[asdict(p)... ]` trimmed (omit the bulky nested `observability.raw` if any). Every method wrapped so exceptions → `{"ok":False,"error":str(e)}`; log provenance (tool name, args). Register 5 thin `@mcp.tool()` async wrappers with honest docstrings, delegating to `get_controller()`.
- [ ] **Step 4: Run — expect PASS** (whole suite) + ruff.
- [ ] **Step 5: Commit** `feat(planning): 5 MCP planning tools (site/conditions/observability/plan)`.

---

### Task 8: observing-planner skill + docs

**Files:**
- Create: `skills/observing-planner/SKILL.md`
- Modify: `SECURITY.md`, `deploy/seestar-mcp.service`, `README.md`
- Test: `tests/test_planning_tools.py` (extend: skill frontmatter presence check optional)

**Interfaces:** none (docs/skill).

- [ ] **Step 1** Write `skills/observing-planner/SKILL.md` with YAML frontmatter (`name: observing-planner`, a `description` covering "plan tonight / what should I image / is it clear enough") and body implementing the Phase-0/1/2 flow from the spec's "New skill" section: load site → `assess_conditions` (state go/no-go + reason) → `plan_targets` (present ranked top-3 with best window, recommended integration, one-line why; sequence to catch targets near their sweet-band window and minimize slews; respect horizon mask; field-rotation-aware limits; moon/dew notes) → hand chosen target to `run-session`. Compact, phone-friendly. Hard rules: never invent ephemeris/weather (always call the tools); never suggest a horizon-blocked or never-up target; state the conditions caveat when `go` is False/unknown.
- [ ] **Step 2** Update `SECURITY.md`: add `api.open-meteo.com` (HTTPS 443) as the one new outbound weather host under SSRF/egress; note it's the only external planning call and carries no secret. Update `deploy/seestar-mcp.service` `IPAddressAllow` comment to include the weather host (or note DNS-based egress can't be pinned by IP, so document it).
- [ ] **Step 3** Update `README.md`: add the 5 planning tools to the tools list and a one-line "Planning / observing-planner" section.
- [ ] **Step 4** Run full suite + ruff: `uv run pytest -q && uv run ruff check src tests`. Expect all green.
- [ ] **Step 5: Commit** `feat(planning): observing-planner skill + security/README notes`.

---

## Self-Review

**Spec coverage:** site profile (T2) ✓, catalog (T1) ✓, observability + field-rotation sweet-band (T3) ✓, weather/conditions verdict (T5) ✓, light pollution (T4) ✓, ranker with reasons (T6) ✓, 5 MCP tools (T7) ✓, observing-planner skill (T8) ✓, SECURITY egress (T8) ✓, determinism (Global Constraints + engine takes `when_utc`) ✓, offline-first weather (T5) ✓.

**Placeholder scan:** none — each task has concrete tests + implementation instructions and exact file paths.

**Type consistency:** `SiteProfile`, `DsoTarget`, `Observability`, `ConditionsAssessment`, `TargetPlan` field names match the spec dataclasses and are used consistently across T3/T6/T7. `observability_fn` injection keeps ranker tests independent of astropy. `field_rotation_ceiling_deg` used in T2 (profile), T3 (sweet band), T6 (penalty).

**Note for implementers:** astropy-derived assertions (moon illumination, exact transit times) may need pinning to a one-time computed value ± tolerance — the plan flags where (T3 Step 4). The `is_blocked` test in T2 has one assertion to correct during implementation (documented in T2 Step 2).

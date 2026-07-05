# Learned Horizon Mask + Location Awareness (Feature A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Learn fixed obstructions from cross-night, weather-gated solve failures and suggest horizon-mask arcs (never auto-applied); make the horizon mask location-aware (GPS-checked + disclosed each plan).

**Architecture:** New `src/seestar_mcp/planning/obstructions.py` (pure core + JSON log + haversine `location_status`); `SiteProfile.location_tolerance_km`; GPS reconcile + a `location` block wired into the existing planning tools; 3 obstruction tools (→ 33). Pure/deterministic cores, human-confirm only.

**Tech Stack:** Python 3.12, dataclasses, `pytest`. No new deps. `uv`.

## Global Constraints

- `uv run pytest`, `uv run ruff check src tests`. NEVER bare `python`.
- Pure cores take injected `now_utc`/inputs — no clock/no I/O. Never-raise on tool paths → `{"ok": False, "error": ...}`; corrupt/missing store → empty.
- **Weather-gating & human-confirm:** bad-weather failures never count toward obstruction inference; the mask is only ever changed by an explicit user `add_horizon_mask`/`set_site_profile`.
- Local state `data/sky_failures.json` gitignored (repo ignores `data/`). No secrets, no new deps/network. Provenance-log tool calls.
- `# FIRMWARE-DEPENDENT`: scope GPS parse from `get_device_state` — isolate to one helper (like `_parse_device_health`).
- Spec of record: `docs/superpowers/specs/2026-07-05-learned-horizon-mask-design.md` (read it).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`; `git -c core.autocrlf=false commit`.

---

### Task 1: Obstruction core (`obstructions.py`) + `location_status`

**Files:** Create `src/seestar_mcp/planning/obstructions.py`; Test `tests/test_planning_obstructions.py`.

**Interfaces — Produces:** `SkyBin`, `ObstructionCandidate` dataclasses (per spec); `AZ_BIN_DEG=10.0`, `ALT_BIN_DEG=5.0`; `load_sky_log(path=None)`, `save_sky_log(log, path=None)`; `record_sky_result(az_deg, alt_deg, ok, *, weather_ok, now_utc, lat=None, lon=None, path=None) -> None`; `suggest_obstructions(log_or_path=None, *, cur_lat=None, cur_lon=None, min_nights=3, min_attempts=4, min_failure_rate=0.7, max_obstruction_alt=40.0, location_tolerance_km=1.0) -> list[ObstructionCandidate]`; `location_status(profile, cur_lat, cur_lon) -> tuple[bool, float]` (haversine km vs `profile.location_tolerance_km`); `haversine_km(lat1, lon1, lat2, lon2) -> float`.

- [ ] **Step 1: failing tests**
```python
# tests/test_planning_obstructions.py
from seestar_mcp.planning.obstructions import (record_sky_result, suggest_obstructions,
    load_sky_log, haversine_km, location_status)
from seestar_mcp.planning.site import SiteProfile

def test_haversine_and_location_status():
    assert abs(haversine_km(40.0, -74.0, 40.0, -74.0)) < 0.01
    d = haversine_km(40.0, -74.0, 40.5, -74.0)          # ~55.6 km
    assert 50 < d < 60
    prof = SiteProfile(name="x", lat_deg=40.0, lon_deg=-74.0)  # default tol 1.0 km (added in T2)
    ok, dist = location_status(prof, 40.001, -74.0)      # ~0.1 km -> within
    assert ok is True

def test_clear_sky_failures_build_candidate(tmp_path):
    p = tmp_path / "sky.json"
    # a low-alt bin (az~90, alt~22) fails on 3 distinct clear nights, neighbors OK
    for night in ("2026-07-04","2026-07-05","2026-07-06","2026-07-07"):
        record_sky_result(92.0, 22.0, ok=False, weather_ok=True, now_utc=f"{night}T04:00:00Z", lat=40.0, lon=-74.0, path=p)
        record_sky_result(60.0, 22.0, ok=True, weather_ok=True, now_utc=f"{night}T04:10:00Z", lat=40.0, lon=-74.0, path=p)
    cands = suggest_obstructions(p, cur_lat=40.0, cur_lon=-74.0)
    assert cands and any(c.az_min_deg <= 92 <= c.az_max_deg and c.alt_min_deg >= 20 for c in cands)

def test_bad_weather_failures_excluded(tmp_path):
    p = tmp_path / "sky.json"
    for night in ("2026-07-04","2026-07-05","2026-07-06"):
        record_sky_result(92.0, 22.0, ok=False, weather_ok=False, now_utc=f"{night}T04:00:00Z", lat=40.0, lon=-74.0, path=p)
    assert suggest_obstructions(p, cur_lat=40.0, cur_lon=-74.0) == []   # weather-excluded

def test_high_altitude_never_obstruction(tmp_path):
    p = tmp_path / "sky.json"
    for night in ("2026-07-04","2026-07-05","2026-07-06","2026-07-07"):
        record_sky_result(92.0, 70.0, ok=False, weather_ok=True, now_utc=f"{night}T04:00:00Z", lat=40.0, lon=-74.0, path=p)
    assert suggest_obstructions(p, cur_lat=40.0, cur_lon=-74.0) == []   # altitude prior

def test_far_location_records_excluded(tmp_path):
    p = tmp_path / "sky.json"
    for night in ("2026-07-04","2026-07-05","2026-07-06","2026-07-07"):
        record_sky_result(92.0, 22.0, ok=False, weather_ok=True, now_utc=f"{night}T04:00:00Z", lat=10.0, lon=10.0, path=p)
    assert suggest_obstructions(p, cur_lat=40.0, cur_lon=-74.0) == []   # different site
```
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** per spec. Bin by `int(az//AZ_BIN_DEG)`, `int(alt//ALT_BIN_DEG)`. `record_sky_result`: night id = UTC date of `now_utc`; bad-weather failure → `weather_excluded += 1` (no attempts/failures bump); else `attempts += 1`, on failure `failures += 1` + add night id + store lat/lon on the bin (or per-record; simplest: keep a representative lat/lon per bin + a set of contributing coords). `suggest_obstructions`: filter bins to those whose stored coords are within `location_tolerance_km` of `(cur_lat,cur_lon)` when given; candidate iff alt-bin ceiling ≤ `max_obstruction_alt`, `len(distinct fail_nights) ≥ min_nights`, `attempts ≥ min_attempts`, `failures/attempts ≥ min_failure_rate`, and NOT (all az bins at that alt equally bad → whole-ring = weather/twilight). Merge adjacent qualifying az bins → one arc `(az_min,az_max, alt_min=alt_bin ceiling)`; `confidence` from nights×rate; `reasons` list. `haversine_km` standard formula. `location_status` uses `getattr(profile,"location_tolerance_km",1.0)`. Never raise.
- [ ] **Step 4: run → PASS** + ruff.
- [ ] **Step 5: commit** `feat(planning): obstruction learner core + haversine location status` (`git add src/seestar_mcp/planning/obstructions.py tests/test_planning_obstructions.py`).

---

### Task 2: SiteProfile tolerance + GPS reconcile in planning tools

**Files:** Modify `src/seestar_mcp/planning/site.py` (add field), `src/seestar_mcp/server.py` (GPS reconcile + `location` block); Test `tests/test_planning_site.py` + `tests/test_planning_tools.py` (extend).

**Interfaces — Produces:** `SiteProfile.location_tolerance_km: float = 1.0` (additive). Controller helpers `_current_gps(self) -> tuple[float,float]|None` (`# FIRMWARE-DEPENDENT` parse of `get_device_state`; best-effort; None on failure) and `_location_block(self, site) -> dict` returning `{"matched": bool|None, "distance_km": float|None, "site_name": str, "mask_applied": bool, "warning": str|None}`. `plan_targets`, `assess_conditions`, `simulate_night` gain a `location` field in their return and apply the mask only when matched (or GPS unknown); on mismatch they use a mask-stripped copy of the site (horizon_mask=[], min_altitude_deg kept) and set `mask_applied=False` + a warning.

- [ ] **Step 1: failing tests**
```python
# tests/test_planning_site.py (add)
def test_profile_has_location_tolerance_default():
    from seestar_mcp.planning.site import SiteProfile
    assert SiteProfile(name="x", lat_deg=0, lon_deg=0).location_tolerance_km == 1.0

# tests/test_planning_tools.py (add) — build controller with Settings(_env_file=None, data_dir=tmp)
def test_plan_targets_location_block_within(tmp_path, monkeypatch):
    # site at 40,-74; scope GPS ~same -> mask_applied True
    ...  # monkeypatch controller._current_gps -> (40.0, -74.0); set_site_profile(...horizon_mask=[...])
    #     plan_targets -> res["location"]["mask_applied"] is True, no warning
def test_plan_targets_location_mismatch_discloses(tmp_path, monkeypatch):
    # scope GPS far away -> mask_applied False + warning present; blocked target NOT dropped
    ...  # monkeypatch _current_gps -> (10.0, 10.0); res["location"]["mask_applied"] is False and res["location"]["warning"]
def test_location_block_gps_unavailable(tmp_path, monkeypatch):
    # _current_gps -> None -> matched None, mask_applied True, "unverified" note
```
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement.** Add the SiteProfile field (ensure `load_site`/`save_site` round-trip it). `_current_gps`: `try: dev = await self.alpaca.method_sync("get_device_state"); parse GPS via a FIRMWARE-DEPENDENT helper (keys like result.setting.lat/lon or result.location — unconfirmed) except: return None`. `_location_block`: if `_current_gps()` None → `{matched:None, mask_applied:True, warning:"GPS unverified — assuming saved site '<name>'", ...}`; else `ok,dist = location_status(site, lat, lon)`; if ok → `{matched:True, mask_applied:True, distance_km:dist, ...}`; else → `{matched:False, mask_applied:False, distance_km:dist, warning:f"Scope is ~{dist:.0f} km from saved site '<name>' — horizon mask NOT applied. Set/confirm a profile for this location.", ...}`. In `plan_targets`/`assess_conditions`/`simulate_night`, compute the block once; if not `mask_applied`, pass a mask-stripped `replace(site, horizon_mask=[])` into the engine; include `res["location"] = block`. Keep everything else unchanged. Determinism: GPS read is I/O at the tool layer (fine — like weather); cores stay pure.
- [ ] **Step 4: run → PASS** (whole suite) + ruff.
- [ ] **Step 5: commit** `feat(planning): GPS-checked location-aware horizon mask in planning tools` (`git add src/seestar_mcp/planning/site.py src/seestar_mcp/server.py tests/test_planning_site.py tests/test_planning_tools.py`).

---

### Task 3: Obstruction MCP tools (→ 33)

**Files:** Modify `src/seestar_mcp/server.py`; Test `tests/test_planning_tools.py` (extend) + `tests/test_server.py` (30 → 33).

**Interfaces — Produces:** controller methods + tools `log_sky_result(target=None, az=None, alt=None, solved=True, weather_go=None)`, `suggest_horizon_mask()`, `add_horizon_mask(az_min, az_max, alt_min)`. Tool count 30 → 33.

- [ ] **Step 1: failing tests**
```python
def test_obstruction_tools_registered():  # 33 tools incl the 3 new names
def test_add_horizon_mask_appends(tmp_path):  # set_site_profile then add_horizon_mask -> get_site_profile shows the arc appended
def test_log_then_suggest(tmp_path, monkeypatch):
    # log_sky_result several clear-sky failures at a low-alt bearing across nights (pass explicit az/alt + weather_go=True)
    # -> suggest_horizon_mask returns a candidate with evidence
def test_log_sky_result_needs_target_or_azalt(tmp_path):  # neither -> ok:false
```
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement.** `_sky_log_path(self)` = `self.settings.data_dir/"sky_failures.json"`; `now = datetime.now(timezone.utc).isoformat()`. `log_sky_result`: if `az`/`alt` omitted and `target` given → compute via `observability`/a small az/alt-at-now helper from catalog+site (or reuse astro AltAz for the target at `now`); if `weather_go` None → best-effort `(await assess_conditions_weather(site, dark_window(site,now), 0.0)).go`; `record_sky_result(az, alt, ok=solved, weather_ok=(weather_go is not False), now_utc=now, lat=site.lat_deg, lon=site.lon_deg, path=...)`; return `{"ok":True, "az":az, "alt":alt}`. Missing target+azalt → `{"ok":False,"error":...}`. `suggest_horizon_mask`: load site (for cur lat/lon + tolerance); `cands = suggest_obstructions(self._sky_log_path(), cur_lat=site.lat_deg, cur_lon=site.lon_deg, location_tolerance_km=site.location_tolerance_km)`; return `{"ok":True, "candidates":[asdict(c)...], "count":N}`. `add_horizon_mask`: load site (or ok:false); append `(az_min,az_max,alt_min)` to `horizon_mask`; `save_site`; return `{"ok":True,"profile":asdict}`. Register 3 `@mcp.tool()` wrappers, honest docstrings (note `suggest_horizon_mask` is read-only). Update module docstring 30→33.
- [ ] **Step 4: run → PASS** (whole suite) + ruff. Update `tests/test_server.py` count 30→33 + names.
- [ ] **Step 5: commit** `feat(planning): 3 obstruction tools (log/suggest/add horizon mask)` (`git add src/seestar_mcp/server.py tests/test_planning_tools.py tests/test_server.py`).

---

### Task 4: Skill notes + docs

**Files:** Modify `skills/observing-planner/SKILL.md`, `skills/anomaly-playbook/SKILL.md`, `README.md`, `SECURITY.md`.

- [ ] **Step 1** `observing-planner/SKILL.md`: add a note that every plan reports a `location` block — if `mask_applied` is False (scope moved), **disclose it in one line** ("scope ~48 km from 'Backyard' — horizon mask off; set a profile here") and do not treat the mask as active; add a step to log solve outcomes via `log_sky_result` and to surface `suggest_horizon_mask` candidates ("seen 92°/22° fail on 4 clear nights — add to mask?") for the user to confirm via `add_horizon_mask`.
- [ ] **Step 2** `anomaly-playbook/SKILL.md`: in the plate-solve-fails branch, add: after corroborating weather, `log_sky_result(target=..., solved=False, weather_go=...)` so the histogram learns (weather-gated); never auto-add a mask.
- [ ] **Step 3** `README.md`: add the 3 obstruction tools (→ 33) + a "Learned horizon mask" note (weather-gated, cross-night, GPS-checked, suggest-and-confirm).
- [ ] **Step 4** `SECURITY.md`: note the mask is never auto-edited (human confirm), obstruction inference is weather-gated + location-scoped, and the GPS reconcile prevents applying a stale mask at a new site; `data/sky_failures.json` is local, no secrets.
- [ ] **Step 5** `uv run pytest -q` (green) + `uv run ruff check src tests`; skills' frontmatter valid.
- [ ] **Step 6: commit** `feat(planning): learned-mask + location-awareness skill notes + docs`.

---

## Self-Review

**Spec coverage:** obstruction core + 4 discriminators (persistence/weather-gate/isolation/altitude) (T1) ✓, location_status haversine (T1) + SiteProfile tolerance + GPS reconcile + disclosure in planning tools (T2) ✓, 3 obstruction tools location-scoped (T3) ✓, skills + docs (T4) ✓, human-confirm-only + never-auto-edit (T3/T4) ✓.

**Placeholder scan:** none.

**Type consistency:** `SkyBin`/`ObstructionCandidate` T1↔T3; `location_status`/`_current_gps`/`_location_block` T1↔T2; tool count 30→33 in T3; `record_sky_result`/`suggest_obstructions` signatures consistent T1↔T3.

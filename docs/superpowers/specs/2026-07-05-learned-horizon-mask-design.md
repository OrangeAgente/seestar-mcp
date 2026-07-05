# Learned Horizon Mask (Obstruction Detection) — Design Spec

**Date:** 2026-07-05
**Project:** seestar-mcp
**Status:** Approved direction (from discussion) — ready for implementation planning
**Builds on:** Phase 1 (site profile + horizon mask + observability), Phase 2 (history store).

---

## Goal

Turn the horizon mask from *manually declared* into *learned-and-confirmed*: accumulate plate‑solve / frame‑rejection failures **binned by (azimuth, altitude) across nights**, and — only when the evidence is unambiguous — **suggest** horizon‑mask arcs for the user to confirm. Fixed obstructions (trees, roofline, power lines) are inferred from geometry + persistence; the system **never auto‑edits the mask**.

## The problem it must not get wrong

A single cloud can blank one target, then another, in sequence — naive "blank frame → blocked" would falsely wall off good sky. Obstruction inference must be robust to that. Four discriminators (clouds cannot fake all four):

1. **Cross‑night persistence** — a bin must fail on ≥ `min_nights` *distinct* nights before it is even a candidate. One bad session never counts.
2. **Weather‑gating** — a failure recorded while `assess_conditions.go` is False (or cloud is high) is tagged **weather** and **excluded** from obstruction inference. Only clear‑sky failures accumulate obstruction evidence.
3. **Spatial isolation** — a candidate bin must be persistently bad while neighboring azimuth bins at the *same* altitude are OK (a tree is a narrow bearing; a cloud is sky‑wide/moving).
4. **Altitude prior** — obstructions are low. Only bins below `max_obstruction_alt` (default 40°) are eligible; a blank at 70° is never a tree.

Everything is a *suggestion the user approves*; nothing edits the mask automatically.

## Design principles (inherited)

MCP = access/compute, Skills = judgment. Pure, deterministic cores (injected `now_utc`); never‑raise tool paths; offline; provenance‑logged; no secrets; no new heavy deps; reason‑tagged suggestions. Local JSON state (gitignored).

---

## Architecture

New module `src/seestar_mcp/planning/obstructions.py` (pure core + a JSON‑backed log); 3 new MCP tools on `SeestarController` (→ 33 total); notes added to `observing-planner` / `anomaly-playbook` skills so the loop records solve outcomes and surfaces suggestions.

### Data model (`obstructions.py`)

```python
@dataclass
class SkyBin:
    az_bin: int          # azimuth bucket index (AZ_BIN_DEG-wide)
    alt_bin: int         # altitude bucket index (ALT_BIN_DEG-wide)
    attempts: int        # clear-sky attempts (weather-excluded ones not counted here)
    failures: int        # clear-sky failures (solve/no-stars) in this bin
    fail_nights: list[str]   # distinct night ids (YYYY-MM-DD) with a clear-sky failure
    weather_excluded: int    # failures dropped because weather was bad (audit only)
    last_utc: str

@dataclass
class ObstructionCandidate:
    az_min_deg: float
    az_max_deg: float
    alt_min_deg: float      # suggested horizon-mask ceiling for the arc
    confidence: float       # 0..1
    reasons: list[str]      # evidence: nights, attempts, failure rate, isolation
```

Bin sizes: `AZ_BIN_DEG = 10.0`, `ALT_BIN_DEG = 5.0`. Store = `dict[(az_bin,alt_bin) -> SkyBin]` persisted to `data/sky_failures.json` (gitignored). A "night id" is the UTC date of the local evening — derived from `now_utc` (simplest: the UTC date of `now_utc`; documented).

### Core functions (pure where possible; the log wraps a path)

- `load_sky_log(path=None) -> dict`, `save_sky_log(log, path=None) -> Path` (default `data/sky_failures.json`; missing/corrupt → empty; never raises).
- `record_sky_result(az_deg, alt_deg, ok, *, weather_ok, now_utc, path=None) -> None` — bucket the (az,alt); if `weather_ok is False`, increment `weather_excluded` on a failure and do **not** count it toward `attempts/failures` (a success in bad weather still counts as an attempt/OK). Else increment `attempts`, and on failure `failures` + add the night id. Update `last_utc`.
- `suggest_obstructions(log_or_path=None, *, min_nights=3, min_attempts=4, min_failure_rate=0.7, max_obstruction_alt=40.0, az_bin_deg=AZ_BIN_DEG, alt_bin_deg=ALT_BIN_DEG) -> list[ObstructionCandidate]` — pure over the log. A bin is a candidate iff: `alt_bin` center ≤ `max_obstruction_alt`; `len(fail_nights) ≥ min_nights`; `attempts ≥ min_attempts`; `failures/attempts ≥ min_failure_rate`; **and** the same‑altitude neighbor az bins (±1) are NOT both equally bad (spatial isolation — if a whole altitude ring is bad, that's weather/twilight, not an obstruction). Merge adjacent qualifying az bins at the same altitude into one arc. Emit an `ObstructionCandidate` with the arc `(az_min,az_max)`, `alt_min = alt_bin ceiling`, a `confidence` from nights×rate, and human‑readable `reasons`.

### New MCP tools (on `SeestarController`)

| Tool | Purpose |
|---|---|
| `log_sky_result(target?, az?, alt?, solved, weather_go?)` | Record one pointing outcome. If `az`/`alt` omitted but `target` given, compute them from the catalog + site + now (via `astro`). `weather_go` defaults to a fresh `assess_conditions` read. Provenance‑logged. |
| `suggest_horizon_mask()` | Return `ObstructionCandidate`s with evidence — the review list. **Read‑only; suggests, never applies.** |
| `add_horizon_mask(az_min, az_max, alt_min)` | Convenience to APPEND one arc to the current site profile's `horizon_mask` and save (so the user confirms a suggestion without re‑entering the whole profile). Explicit user action. |

Tool count 30 → 33.

### Skill notes (additive)

- **`observing-planner` / `run-session`:** after a `plate_solve` at a target (in acquire or a mid‑session re‑solve), call `log_sky_result(target=..., solved=<bool>, weather_go=<from assess_conditions>)`. On session wind‑down or when asked "what's blocking my view?", call `suggest_horizon_mask` and present any candidates with evidence, offering `add_horizon_mask` for the user to confirm each.
- **`anomaly-playbook` (plate‑solve fails branch):** when a solve fails, still `log_sky_result(solved=False, weather_go=...)` so the histogram learns — but keep the existing "corroborate with weather before blaming pointing" discipline; a bad‑weather failure is logged weather‑excluded.

## Data flow

```
run-session/anomaly-playbook: plate_solve outcome + current weather_go
  → log_sky_result → sky_failures.json (weather-gated bins)
"what's blocking my view?" / wind-down
  → suggest_horizon_mask → candidates (cross-night, isolated, low-alt)
  → USER confirms → add_horizon_mask → site_profile.horizon_mask
  → future planning: is_blocked() drops those targets
```

## Error handling

- Missing/corrupt `sky_failures.json` → empty log; never raises.
- `log_sky_result` with no site + no az/alt → `{"ok": False, "error": "need target+site or explicit az/alt"}`.
- `suggest_obstructions` on a thin log → `[]` (not enough evidence yet).
- All timestamps injected (`now_utc`) for determinism.

## Testing

- `record_sky_result`: clear‑sky failure increments the right bin + adds the night id; a bad‑weather failure goes to `weather_excluded` and does NOT count toward failures/attempts; a clear‑sky success counts as attempt+OK.
- `suggest_obstructions`: a low‑alt bin failing on 3 distinct clear nights (rate ≥ 0.7, isolated) → one candidate arc with `alt_min` at the bin ceiling; the same failures but weather‑excluded → NO candidate; a high‑alt (70°) persistent failure → NO candidate (altitude prior); a whole altitude ring failing (all az bins) → NO candidate (not isolated → weather/twilight); adjacent bad az bins merge into one arc.
- tools: 33 registered; `log_sky_result` computes az/alt from a target when omitted (mock astro or use a real fixture date); `suggest_horizon_mask` returns candidates; `add_horizon_mask` appends to the profile and `get_site_profile` shows it. No‑site error path.
- Skills: frontmatter valid; the new log/suggest steps referenced.
- No network/hardware in tests (weather + astro mocked or fixture‑dated).

## Security / reproducibility

New local state `data/sky_failures.json` (gitignored) — no secrets, no new network host, no new deps. Deterministic via injected `now_utc`. Provenance logs the tool calls. The mask is only ever changed by an explicit `add_horizon_mask` (or `set_site_profile`) call the user makes after reviewing evidence — never automatically.

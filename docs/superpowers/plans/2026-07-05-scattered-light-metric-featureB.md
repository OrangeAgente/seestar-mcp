# Scattered-Light / Halo Metric (Feature B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a `scattered_light` sub-metric to Tier-2 QA (bright-star halo ratio + background non-uniformity) so thin cirrus that slips the SNR/star-count floors gets flagged.

**Architecture:** Extend the existing `qa_tier2.py` (compute the metric in `analyze_sub`, verdict in `classify`), add 3 config thresholds, add a seeded `hazy.fits` fixture. No new module, no new deps. Backward compatible (None metric = no behavior change).

**Tech Stack:** Python 3.12, `photutils`/`astropy`/`numpy` (already pinned), `pytest`. `uv`.

## Global Constraints

- `uv run pytest`, `uv run ruff check src tests`. NEVER bare `python`.
- Deterministic (pure function of the FITS + seeded fixture); `analyze_sub` NEVER raises (metric → `None` on failure); non-finite must never reach the report (`_finite` guard + `render_json(allow_nan=False)` already enforced).
- **Backward compatible:** a session where every `scattered_light` is `None` must produce identical verdicts to today (regression test).
- Find the qa_tier2 module first: `git ls-files | grep qa_tier2` — it lives at `src/seestar_mcp/planning/qa_tier2.py` or `src/seestar_mcp/qa_tier2.py`; read it before editing.
- Spec of record: `docs/superpowers/specs/2026-07-05-scattered-light-metric-design.md` (read it).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`; `git -c core.autocrlf=false commit`.

---

### Task 1: Compute `scattered_light` in `analyze_sub` + `hazy` fixture

**Files:** Modify `qa_tier2.py` (locate it), `tests/fixtures/make_fixtures.py`; Create `tests/fixtures/hazy.fits`; Test `tests/test_qa_tier2.py` (extend).

**Interfaces — Produces:** `SubMetrics` gains `scattered_light: float | None = None`; `analyze_sub` computes it. Verify photutils 2.3.0 API first (`uv run python -c "from photutils.aperture import CircularAnnulus, aperture_photometry; from photutils.background import Background2D"`).

- [ ] **Step 1: extend the fixture generator + regenerate.** In `make_fixtures.py` add a `make_hazy()` (seeded, `np.random.default_rng([1234, 4])`): same round stars as `good.fits` (low FWHM, adequate star count) PLUS a smooth large-scale background gradient/pedestal AND a broad low-amplitude Gaussian halo added around the ~5 brightest stars. Write `tests/fixtures/hazy.fits`. Commit the fixture (allow-listed by `.gitignore`).
- [ ] **Step 2: failing tests**
```python
# tests/test_qa_tier2.py (add)
def test_scattered_light_low_on_good_fixture():
    m = analyze_sub(FIX / "good.fits")
    assert m.scattered_light is not None and m.scattered_light < 0.15   # tune to measured

def test_scattered_light_high_on_hazy_fixture():
    good = analyze_sub(FIX / "good.fits")
    hazy = analyze_sub(FIX / "hazy.fits")
    assert hazy.scattered_light > good.scattered_light * 1.5            # clearly elevated
    assert hazy.star_count >= 20 and (hazy.fwhm or 99) < 7              # would PASS the OLD floors

def test_scattered_light_none_on_garbage(tmp_path):
    assert analyze_sub(tmp_path / "nope.fits").scattered_light is None  # no raise
```
- [ ] **Step 3: run → FAIL.**
- [ ] **Step 4: implement** in `analyze_sub`: after source detection + background, compute the two components (spec):
  - **halo ratio:** for the brightest `K=10` sources, a `CircularAnnulus(positions, r_in≈3*median_fwhm_px, r_out≈5*median_fwhm_px)` `aperture_photometry` → per-star `(annulus_mean − global_bkg) / (peak − global_bkg)`; take the median (guard divide-by-zero / non-positive denom).
  - **background non-uniformity:** `Background2D(data, box_size=...)`; `nonuniformity = float(bkg.background_rms_median?)` — better: `std(bkg.background_mesh)/(median+eps)` (large-scale structure). VERIFY the attribute names against installed photutils.
  - `scattered_light = max(halo_ratio_component, nonuniformity_component)` (or a documented weighted blend), passed through `_finite` (→ None if not computable / < K stars). Store on `SubMetrics`. Never raise (existing broad-except path covers it).
- [ ] **Step 5: run → PASS** + ruff. If a fixture threshold is off, tune the assertion to the measured values (document) and/or the fixture amplitude so `good` is low and `hazy` is clearly higher while still passing the old floors.
- [ ] **Step 6: commit** `feat(qa): scattered-light/halo metric in analyze_sub + hazy fixture` (`git add <qa_tier2 path> tests/fixtures/make_fixtures.py tests/fixtures/hazy.fits tests/test_qa_tier2.py`).

---

### Task 2: Verdict rule + config thresholds

**Files:** Modify `qa_tier2.py`, `src/seestar_mcp/config.py`; Test `tests/test_qa_tier2.py` (extend) + `tests/test_config.py` (defaults).

**Interfaces — Produces:** `Settings` gains `qa_scatter_reject_sigma: float = 2.0`, `qa_scatter_marginal_sigma: float = 1.0`, `qa_scatter_absolute: float | None = None`. `classify` scores subs on the scattered-light axis.

- [ ] **Step 1: failing tests**
```python
def test_classify_rejects_high_scatter():
    # session of clean subs (low scattered_light) + one with much higher scattered_light
    # -> the outlier is REJECT (or MARGINAL) with a reason naming "scattered light"
def test_classify_scatter_none_is_noop():
    # a session where every SubMetrics.scattered_light is None -> identical verdicts to
    # the same session pre-feature (regression: no scatter reasons, same PASS/REJECT).
def test_scatter_absolute_override():
    # qa_scatter_absolute set low -> a sub above it REJECTs regardless of session median.
# tests/test_config.py: assert the 3 new defaults.
```
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement.** In `classify`, over subs with non-None `scattered_light`: session median + σ; REJECT if `> median + qa_scatter_reject_sigma*σ` (or `> qa_scatter_absolute` if set), MARGINAL if `> median + qa_scatter_marginal_sigma*σ`. Append a reason `f"REJECT: scattered light {v:.2f} > {thr:.2f} (median + {k}σ) — likely thin cirrus / bright-star halos"`. Fits the existing any-REJECT-wins / MARGINAL logic. Subs with `None` are skipped on this axis (backward compatible). Add the 3 config fields with the spec defaults.
- [ ] **Step 4: run → PASS** (whole suite) + ruff.
- [ ] **Step 5: commit** `feat(qa): session-relative scattered-light verdict + thresholds` (`git add <qa_tier2 path> src/seestar_mcp/config.py tests/test_qa_tier2.py tests/test_config.py`).

---

### Task 3: `qa-policy` skill note + docs

**Files:** Modify `skills/qa-policy/SKILL.md`, `README.md`.

- [ ] **Step 1** `qa-policy/SKILL.md`: add `scattered_light` to the metrics list (what it means: bright-star halos + background non-uniformity = thin cirrus / scattered light) and a pattern line: **halo ratio / background non-uniformity up, FWHM roughly stable, SNR & star-count only mildly down ⇒ thin cirrus / scattered light** (distinct from focus, and from thick cloud which collapses star count). Note the session-relative threshold + that it catches veils that slip the SNR/star-count floors.
- [ ] **Step 2** `README.md`: mention `scattered_light` in the Tier-2 QA description (one line).
- [ ] **Step 3** `uv run pytest -q` (green) + `uv run ruff check src tests`; qa-policy frontmatter valid.
- [ ] **Step 4: commit** `feat(qa): scattered-light qa-policy note + docs`.

---

## Self-Review

**Spec coverage:** `scattered_light` metric (halo ratio + non-uniformity) in `analyze_sub` (T1) ✓, `hazy` fixture that passes old floors but flags scatter (T1) ✓, session-relative verdict + config + backward-compat no-op (T2) ✓, qa-policy pattern note (T3) ✓, never-raise / strict-JSON / `_finite` (Global Constraints + T1) ✓.

**Placeholder scan:** none (fixture thresholds explicitly "tune to measured").

**Type consistency:** `SubMetrics.scattered_light: float|None` T1↔T2; config field names `qa_scatter_*` T2 match the verdict use; `analyze_sub`/`classify` signatures unchanged.

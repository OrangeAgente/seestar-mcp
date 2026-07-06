# AstroPipe Stage 2 — Background / gradient removal

**Date:** 2026-07-05
**Roadmap:** `docs/superpowers/specs/2026-07-05-astropipe-design.md` (Stage 2)
**Goal:** Flatten large-scale background gradients (light pollution, moon glow, amp/vignette) on a linear master *before* stretch, so the stretch doesn't amplify an uneven sky. The DBE/ABE equivalent, pure-Python, no external app.

## Why

Even after a good stack, a Bortle-8 / moonlit sky leaves a smooth low-order gradient across the frame. Our stretch then lifts it unevenly (the "bright background" we saw on the Veil was noise, but real gradients also occur — e.g. moon glow on one edge). Removing the gradient on the *linear* master is the single biggest cleanliness win and a prerequisite for honest color.

## Approach

A new module `src/seestar_refine/gradient.py`, self-contained and pure (no new heavy deps — `photutils.Background2D` is already available; `numpy`/`scipy` only).

Two-tier, both operating on the linear `(H, W, 3)` (or `(H, W)`) master:

1. **`sample_background(channel, *, box, mask_sigma)`** — estimate the smooth sky per channel:
   - Sigma-clip the channel to reject stars/nebulosity (`mask_sigma`, e.g. 3σ above the sky), so the model fits *sky*, not signal.
   - Use `photutils.Background2D` with a coarse `box` (e.g. 128 px) + median estimator over the star-masked data → a smooth 2-D background map.
2. **`subtract_gradient(img, *, box, mask_sigma, protect_percentile, degree)`** — the public entry:
   - For each channel, build the background map, **subtract** it, and re-add a small constant pedestal (the map's global median) so the image stays positive and channels keep a common zero.
   - **Model choice:** `Background2D` (spline-ish, adapts to real gradients) by default; a `degree`-N 2-D polynomial fallback (`numpy.polynomial`) for a stricter low-order surface when `Background2D` is unavailable or the user wants only a gentle tilt removed.
   - Fully protects signal: because the model is fit on star/nebula-masked sky, subtracting it leaves the target intact.
   - Returns `(corrected_img, info)` where `info` records the per-channel gradient amplitude removed (max−min of each background map) for provenance.

Pure, NaN/inf-safe, never-raises (mirrors preview/crop): on any failure return the input unchanged.

## Wiring

- `make_preview` (`preview.py`) gains a `gradient` step: when `params.get("gradient", False)` (default **off** to preserve current behavior; opt-in), run `subtract_gradient` **after autocrop, before color-balance/stretch**. `info` is added to `stats["gradient"]`.
- A standalone MCP-facing path is deferred; for now it's a preview parameter so the whole pipeline (stack → preview) can flatten in one call.

## Testing (TDD)

- `subtract_gradient` on a synthetic frame = flat sky + a **known linear ramp** + a few bright "stars" → assert the output background is flat (ramp removed to within tolerance) and the star pixels are preserved (not subtracted away).
- Star protection: a bright compact source on a gradient → its peak value survives (masked out of the fit).
- Never-raises: NaN/garbage input returns the input unchanged.
- `make_preview(..., params={"gradient": True})` on a synthetic gradient master → `stats["gradient"]` present and the saved PNG background is flatter (measure Background2D amplitude before/after).

## Validation

Re-render the **Veil** master (which had the real gradient concern) and the **M31 deep** master with `gradient=True`, compare before/after, and place beside the professional reference. Confirm the gradient amplitude (Background2D max−min) drops materially while the nebula/galaxy is untouched.

## Success criteria

A linear master's per-channel background gradient amplitude is reduced ≥5× on a frame with a real gradient, with no measurable loss of target signal, and the flattened preview looks cleaner than the un-flattened one — verified visually against a reference.

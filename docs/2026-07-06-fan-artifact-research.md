# Field-rotation fan artifacts on Seestar OSC stacks — research synthesis

**Date:** 2026-07-06
**Method:** deep-research workflow — 22 sources, 25 claims adversarially verified (23 confirmed, 2 refuted).

## Root cause

On the alt-az Seestar, the field rotates over a session. Vignetting / sensor
response / light-pollution gradient is **fixed to the detector**, so it rotates
relative to the sky and, after registration + integration, smears into colored
"fan" artifacts at the frame corners (Moehler et al. 2010, PASP 122, 93 —
rotating flat-field on a field-rotator instrument).

## Key verified findings

1. **Vignetting is MULTIPLICATIVE → remove by DIVISION (a flat).** Light-pollution
   gradients are ADDITIVE → remove by SUBTRACTION. (Siril docs.) A naive
   linear-plane *subtraction* is the wrong operation for vignetting — which is
   exactly why our first attempt made the corners worse.
2. **Flat-fielding is the primary fix.** Seestar subs are dark-subtracted but not
   flat-fielded — the missing step.
3. **No flat frames → synthetic flat.** Two methods:
   - *Dark-sky flat*: stack unregistered lights with aggressive rejection.
     **Pitfall:** needs dither + a *small* target; a large centered rotation-only
     target (M31/NGC 281) leaves residue → not suitable for us.
   - *Per-frame smooth model*: fit a low-order source-masked model and divide.
     **This is the robust route for centered targets** (what we implemented).
4. **Additive gradient removal:** photutils Background2D or low-degree polynomial
   (degree 1 for linear). Can run per-sub (stack gradient = sum of sub gradients).
5. **Rejection:** astroscrappy / L.A.Cosmic (`sigclip≈4.5`, raise `objlim` if star
   cores get eaten) + sigma/winsorized clip for trails.
6. **Then crop.** Siril's explicit Seestar advice: "crop the image if the edges
   are not pretty." No static flat is perfect on a rotating field.
7. **Correct order:** debayer → (dark, on-scope) → **synthetic-flat DIVIDE** →
   per-sub additive gradient SUBTRACT → register → sigma-clip integrate →
   background extraction on master → **crop** → stretch.

## Refuted
- "Seestar-only-lights makes flat correction impossible" — FALSE (synthetic flats work).
- "Flats reusable only if radially symmetric" — only partly supported (treat the
  Seestar's near-radial vignetting as plausible-but-unverified).

## Implementation (this repo)
- `pystack._synthetic_flat`: per-channel degree-2 sigma-clipped polynomial model,
  normalized, applied by **division** (`min_value` floor). Wired as
  `flat_correct=True` (default) — the fan fix.
- Additive gradient removal already exists (`gradient.subtract_gradient`, master).
- Coverage-crop remains the final edge cleanup.
- TODO: astroscrappy CR/hot-pixel step (needs Seestar IMX462 gain/read-noise).

**Sources:** Moehler 2010 (arXiv:1001.1099), Byun 2025 K-DRIFT (arXiv:2504.14914),
Siril background/Seestar tutorials, ccdproc/photutils/astroscrappy docs,
TrappedPhotons + Westwood synthetic-flat guides.

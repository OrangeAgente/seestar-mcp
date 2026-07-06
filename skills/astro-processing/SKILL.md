---
name: astro-processing
description: >
  The processing RECIPE for turning Seestar OSC subs into a finished image with the
  pure-Python AstroPipe pipeline — the correct calibration/stacking/stretch ORDER and
  the object-type-specific parameters. Use whenever choosing pystack + stretch_master
  params, tuning a result, or diagnosing artifacts (rotation "fans", blown cores, noisy
  backgrounds, small off-center galaxies). Complements image-refinement (which picks the
  backend); this skill decides the PARAMETERS. Grounded in the research synthesis at
  docs/2026-07-06-fan-artifact-research.md.
---

# AstroPipe Processing Recipe (Seestar OSC)

How to process Seestar one-shot-color subs to near-professional quality with the free
`seestar-refine` pipeline. This is the parameter/ordering playbook; `image-refinement`
chooses the engine, `qa-policy` builds the keep-list.

## The correct order (do not reorder)

Debayer → (dark: already on-scope) → **synthetic-flat DIVIDE** → per-sub gradient
SUBTRACT → register → sigma-clipped integrate → **coverage crop** → background
extraction on the master → **stretch** → denoise → saturation → (opt-in) upscale.

`pystack.stack` does debayer → flat → register → integrate → coverage-crop.
`stretch_master`/`make_preview` does gradient → stretch → denoise → saturation → upscale.

## Why the order matters (the physics)

- **Vignetting is MULTIPLICATIVE → DIVIDE by a flat** (`flat_correct=True`, on by
  default). Subtracting it is wrong and *worsens* the corners. This is the fix for the
  alt-az field-rotation **"fan" artifacts**: the vignetting is fixed to the sensor and
  rotates with the field, smearing into colored corner fans unless divided out per-frame
  *before* registration.
- **Light-pollution gradients are ADDITIVE → SUBTRACT** (`params={"gradient": true}`).
- No static flat is perfect on a rotating field, so **crop residual edges** last
  (coverage crop handles most; hand-crop galaxies to taste).

## Stacking (`stack_keep_list(engine="pystack")` / `pystack.stack`)

- `flat_correct=True` (default) — the synthetic-flat division. Keep it on.
- `coverage_frac=0.5` (default) — masks low-coverage border, keeps large diagonal
  objects whole. Lower (0.3) to keep more of a big target; higher (0.7) to trim harder.
- Rejection is sigma-clipped mean (kappa 2, 5 iters). Deep sets (500+ subs) reject
  satellite trails well; astroscrappy is available for aggressive hot-pixel/CR cleanup.

## Stretch + finish params by OBJECT TYPE

Pass via `stretch_master(master_path, params={...})`.

**Galaxies (M31, M51, M81, M33)** — high dynamic range, keep the core golden:
```
stretch=asinh, asinh_beta=0.05–0.09 (smaller = more disk lift),
gradient=true, gradient_box=96, white_balance=true,
black_point_sigma=-0.3 (clip sky to black), white_percentile=99.9,
saturation=1.4–1.5, denoise=0.03–0.08 (more for short stacks)
```
Small/off-center galaxies (M51/M81/M33 in the ~1.3° FOV): **crop to center the galaxy**
(suppress bright stars first — cap luminance at ~98th percentile, then argmax a heavily
Gaussian-smoothed luminance to find the extended galaxy).

**Emission nebulae (M42, NGC 281, IC 5146, Veil)** — MTF + strong Hα/OIII color:
```
gradient=true, white_balance=true, black_point_sigma=-0.3 to -0.4 (clip noise),
midtone=0.16–0.18, white_percentile=99.5, saturation=1.6–1.7, denoise=0.05–0.09
```
M42's Trapezium core will clip white (needs HDR to tame — accept it).

**Planetaries (M27, M57)** — small bright, the percentile white point is critical:
```
gradient=true, white_balance=true, deconv=true (gentle: deconv_sigma=1.4,
deconv_iters=4, protect_stars on by default), black_point_sigma=-0.2, midtone=0.15,
white_percentile=99.7, saturation=1.7, denoise=0.1–0.12 (short stacks are noisy)
```

## Diagnosing common problems

| Symptom | Cause | Fix |
|---|---|---|
| Colored **fan streaks** in corners | rotating vignetting not divided out | ensure `flat_correct=True`; then crop residual |
| Galaxy **core blown white** | MTF clips high dynamic range | use `stretch=asinh` |
| Object **sliced / distorted** | coverage crop too tight for a large object | lower `coverage_frac`; hand-crop galaxies |
| **Noisy speckled** background | short-sub stack + under-clipped stretch | raise black point (more negative `black_point_sigma`) + `denoise` 0.08–0.12 |
| Galaxy **small & off-center** | Seestar wide field | star-suppressed centroid crop |
| **Ringing halos** on stars after deconv | RL overshoot | keep `deconv_protect_stars=true`, gentle sigma/iters |

## Hard limits (data ceiling, not pipeline)

Shallow single-night stacks (< ~150 subs) and tiny targets (M57) stay noisy — that's
photons, not processing. Deep sets (M51 1386, M31 500+, NGC 281 571) reach near-pro. To
go deeper: more integration, or EQ mode (avoids field rotation entirely) so the fans and
the coverage-crop loss disappear.

## References
- `docs/2026-07-06-fan-artifact-research.md` — the verified research synthesis.
- `docs/superpowers/specs/2026-07-05-astropipe-design.md` — the pipeline roadmap.

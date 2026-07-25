# AstroPipe — Pure-Python Refinement Pipeline (design + roadmap)

**Date:** 2026-07-05
**Goal:** A free, cross-platform, DSS/PixInsight-free refinement pipeline for `seestar-refine`: raw Bayer subs → finished image, built in stages, each validated against the DSS master **and** a professional reference.

## Motivation

DSS is Windows-only and its CLI reads GUI/registry settings (fragile, untestable in CI). PixInsight is €300. A pure-Python pipeline built on the scientific-Python stack (`astropy`, `ccdproc`, `photutils`, `numpy`, `scipy`, `astroalign`, `scikit-image`) removes both dependencies, runs anywhere, and is fully unit-testable. This project already depends on `ccdproc`/`photutils`/`astropy`/`numpy`, so we are extending an existing foundation.

## Roadmap (staged; each stage = its own spec → plan → implement → validate)

| Stage | Deliverable | Tools | Notes |
|---|---|---|---|
| 0. Calibrate *(deferred)* | dark/flat subtraction | ccdproc | Seestar auto-darks on-scope; skip initially |
| **1. Stack** | debayer + register + integrate → linear `(3,H,W)` master | astroalign + numpy/scipy | **this spec**; optional drizzle experiment |
| 2. Gradient removal | flatten LP/moon background | polynomial/RBF model or GraXpert | biggest visual win |
| 3. Color calibration | neutral/correct color | extend neutralize/SCNR → star-based WB | partly exists |
| 4. Stretch | linear → display | percentile MTF | **exists** (`preview.auto_stretch`) |
| 5. Deconvolution | recover real detail | Richardson–Lucy (`skimage.restoration`) | honest sharpening |
| 6. Finish | saturation, denoise, output | existing preview + saturation | mostly exists |
| 7. AI upscale *(opt-in, labeled)* | cosmetic super-resolution | Real-ESRGAN/SwinIR | provenance-flagged "AI-generated detail, not captured signal" |

**Principles carried through every stage:** never-raise pure cores; each step provenance-logged; every AI/synthetic step explicitly labeled in provenance; validate against DSS + a professional reference image before advancing.

---

## Stage 1 — `pystack` (this spec)

A new module `src/seestar_refine/pystack.py` providing a DSS-free stacking backend with the **same result contract as `dss.stack`** so it is a drop-in alternative.

### Facts (validated against real data)

- Seestar light subs: raw 2-D Bayer, `1920×1080` uint16, **`BAYERPAT = GRBG`** (from the FITS header), 10 s, gain 80.
- DSS master output: `(3, 1920, 1080)` float32 RGB cube — the shape/format to match.
- ~286 QA-passed M27 subs available locally at `<local-subs-dir>/M27_sub`.

### Units (each pure, NaN/inf-safe, never-raising)

1. **`debayer(raw, pattern="GRBG") -> np.ndarray`**
   `(H, W)` uint16/float CFA → `(H, W, 3)` float via bilinear CFA interpolation (per-channel mask + `scipy.ndimage.convolve` with the standard bilinear kernels). Pattern-aware (GRBG default; supports RGGB/BGGR/GBRG). Returns full resolution.

2. **`register(ref_lum, frame_rgb, frame_lum) -> np.ndarray | None`**
   Uses `astroalign.find_transform(frame_lum, ref_lum)` to get the affine transform on luminance, then `astroalign.apply_transform` to warp each RGB channel into the reference frame. Returns the registered `(H, W, 3)` or `None` if astroalign cannot solve (too few stars / no match).

3. **`integrate(stack_memmap, kappa=2.0, iters=5) -> np.ndarray`**
   Sigma-clipped mean across the frame axis, computed in **row-blocks** so peak memory is bounded regardless of frame count. Per pixel: iteratively reject values > `kappa*std` from the mean, average survivors. Input is a `(N, H, W, 3)` float32 array (real memmap on disk, or an in-RAM array — both accepted).

4. **`stack(keep_list, settings, *, master_name="master.fit") -> dict`**
   Orchestrates: load each sub → `debayer` → luminance (`0.2126R+0.7152G+0.0722B`); pick reference = first kept sub; `register` all others; stream registered frames into a temp `np.memmap` of shape `(N, H, W, 3)`; `integrate`; write the `(3, H, W)` float32 FITS master to `settings.output_dir/master_name`. Returns `{"ok", "master_path", "stats", "error"}` matching `dss.stack`. Drops-and-logs any sub that fails to load/debayer/register; `ok=False` if fewer than 3 register or astroalign is unavailable.

### Memory strategy

286 × 1920×1080×3 float32 ≈ 7 GB; the deep M51 set (1386 subs) ≈ 34 GB. Registered frames stream to a temp `np.memmap` on disk; `integrate` reads row-blocks (e.g. 64 rows at a time across all N frames). Peak RAM stays a few hundred MB regardless of N. (64 GB RAM available, but memmap keeps the big sets feasible too.)

### Dependencies

Add **`astroalign`** to `pyproject.toml` (pulls in `sep` + `scikit-image`). Justified: removes the Windows-only DSS desktop dependency, making stacking cross-platform and CI-testable. `scipy` is already transitively present (astropy/photutils).

### Error handling

Mirrors the DSS backend exactly: `stack()` never raises; a bad sub is dropped-and-logged; a configured-but-unavailable backend (astroalign not importable) returns `{"ok": False, "error": "astroalign backend unavailable"}` and is surfaced by `check_backends`.

### Wiring

- `check_backends` reports `pystack` availability (astroalign importable).
- `stack_keep_list` MCP tool: extend the `engine` param with `pystack` → routes to `pystack.stack`.
- Provenance: `pystack.stack` logs command-equivalent metadata (engine, sub count, kappa, iters, reference frame) to `refine_provenance.jsonl`.

### Testing (TDD)

- `debayer`: synthetic 4×4 GRBG array with known pixel values → assert the RGB channels interpolate to expected values at known sites; assert `(H,W,3)` shape; NaN-safe.
- `integrate`: synthetic `(N,H,W,3)` stack with a planted single-frame outlier pixel → assert the outlier is rejected and the result equals the clean mean; assert row-blocking gives the same answer as a whole-array clip.
- `register`: synthetic star field (≥10 random points) shifted/rotated by a known transform → assert `register` recovers alignment (registered star positions match the reference within tolerance). Requires astroalign (now a dep).
- `stack`: orchestration test with a tiny set of synthetic subs (monkeypatched register = identity) → assert `(3,H,W)` master written, `ok=True`, stats populated; unavailable-backend path returns `ok=False`.
- Integration (manual/opt-in, not CI): stack the real 286 M27 subs, compare to the DSS master + a professional reference.

### Success criteria

A `(3,1920,1080)` pystack M27 master from the 286-sub keep-list, run through the existing preview, shown **three-up: DSS vs pystack vs professional reference** — with round stars, correct color, clean background, comparable to DSS.

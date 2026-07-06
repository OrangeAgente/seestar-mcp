# ZWO Seestar S50 — hardware specifications

Reference for the hardware this project drives. Verified against ZWO official +
reputable reviews (see Sources). Unofficial; "Seestar"/"ZWO" are trademarks of
Suzhou ZWO Co., Ltd. (see NOTICE).

## Optics
- **Aperture:** 50 mm
- **Focal length:** 250 mm
- **Focal ratio:** f/5
- **Design:** triplet apochromatic (APO), includes one ED-glass element
- **Field of view:** ~1.29° × 0.73° (with the IMX462 sensor)
- Built-in **light-pollution (LP) / dual-band filter** (electronically switchable)

## Sensor
- **Sony IMX462** color CMOS (one-shot-color / OSC)
- **2.1 MP**, **1920 × 1080** pixels, 1/2.8″ format, ~2.9 µm pixels
- **Bayer pattern: GRBG** (as read from the sub FITS headers)
- Raw subs are saved as `.fit` (uint16, 2-D Bayer)

## Mount / motion
- **GoTo + tracking motors** — the scope slews and tracks under its own power.
- **Two modes:**
  - **Alt-azimuth (default):** no accessories needed. Tracks the target's
    *position* but **NOT its field orientation**, so the field **rotates** around
    the target over a session (field rotation). Exposures are capped at **10 s**
    to limit per-sub rotation smear; the app de-rotates subs in software when
    live-stacking.
  - **Equatorial (EQ) mode:** requires an **EQ wedge / latitude base** (the scope
    is physically tilted to the site latitude and polar-aligned). This aligns the
    azimuth axis with Earth's rotation axis, so the field **does not rotate** —
    enabling **up to 30 s** exposures (community reports up to 60 s), pinpoint
    stars across the frame, a fully illuminated field, and lower noise.

## Power / storage / connectivity
- Battery ~**6000 mAh**, ~**6 h** runtime; USB-C charging
- **64 GB** internal storage (subs saved to `EMMC Images/MyWorks/<Target>_sub/`)
- Wi-Fi (station + AP modes); driven via `seestar_alp` (Alpaca) in this project

## Why this matters for the processing pipeline

**Field rotation is inherent to alt-az mode and is the root cause of the
"fan" artifacts** in deep stacks: the vignetting / sensor response is fixed to
the detector, so as the field rotates it smears into colored corner fans after
registration. Perfect tracking does **not** prevent this — the tracking motor
holds position, not orientation.

Implications, in order of effectiveness:
1. **Hardware cure:** shoot in **EQ mode** (needs an EQ wedge, ~$30–60) — no field
   rotation, no fans, longer exposures, no coverage-crop loss.
2. **Software mitigation (this repo, alt-az):** synthetic-flat **division**
   (`pystack flat_correct`) removes the per-frame vignetting, then **crop** the
   rotation-corrupted edges. See `docs/2026-07-06-fan-artifact-research.md` and
   the `astro-processing` skill.
3. **Target strategy (alt-az):** favor **compact targets** (globulars,
   planetaries, small nebulae) that sit in the central always-covered zone, so
   the rotation crop costs nothing. Large sprawling targets (M31, wide M51) are
   where alt-az hurts most.

The 10 s alt-az exposure cap also means deep results need **many subs** (hundreds
to 1000+), which the pipeline is built to handle (memmap-bounded integration).

## Sources
- ZWO official: https://www.zwoastro.com/product/seestar-s50/
- AstroBackyard review: https://astrobackyard.com/seestar-s50-review/
- Agena EQ-mode guide: https://agenaastro.com/articles/imaging/how-to-set-up-and-use-eq-mode-on-the-zwo-seestar-s50-s30
- ZWO EQ-mode tutorial: https://www.seestar.com/blogs/tutorial/enable-equatorial-mount-mode-seestar

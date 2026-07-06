"""Stage 5: Richardson-Lucy deconvolution (honest detail recovery)."""

from __future__ import annotations

import numpy as np


def test_deconvolve_sharpens_blurred_star():
    from scipy.ndimage import gaussian_filter

    from seestar_refine.deconv import deconvolve

    star = np.zeros((64, 64), dtype="float64")
    star[32, 32] = 1.0
    blurred = gaussian_filter(star, 2.0)
    img = np.stack([blurred] * 3, axis=-1)

    out = deconvolve(img, psf_sigma=2.0, iterations=20)
    # Deconvolution tightens the profile: the wing/peak ratio drops sharply
    # (the star concentrates back toward a point).
    blurred_ratio = blurred[32, 34] / blurred[32, 32]
    out_ratio = out[32, 34, 1] / out[32, 32, 1]
    assert out_ratio < 0.8 * blurred_ratio
    # The peak stays the frame max (per-channel max-normalized), not degraded.
    assert out[32, 32, 1] >= blurred[32, 32] * 0.99


def test_deconvolve_never_raises():
    from seestar_refine.deconv import deconvolve

    out = deconvolve(np.full((8, 8, 3), np.nan), psf_sigma=1.5, iterations=5)
    assert out.shape == (8, 8, 3)


def test_deconvolve_protect_stars_suppresses_ring():
    from scipy.ndimage import gaussian_filter

    from seestar_refine.deconv import deconvolve

    h, w = 96, 96
    bump = np.zeros((h, w), dtype="float64")
    bump[48, 48] = 0.9
    img = gaussian_filter(bump, 1.5) + 0.1  # a blurred star on a 0.1 background
    img3 = np.stack([img] * 3, axis=-1)

    unprot = deconvolve(img3, psf_sigma=1.5, iterations=30, protect_stars=False)
    prot = deconvolve(img3, psf_sigma=1.5, iterations=30, protect_stars=True)

    # Sample an annulus ~4 px from the star where RL ringing dips below background.
    ann = [(48 + dy, 48 + dx) for dy, dx in [(4, 0), (-4, 0), (0, 4), (0, -4)]]
    unprot_ring = min(unprot[y, x, 1] for y, x in ann)
    prot_ring = min(prot[y, x, 1] for y, x in ann)
    assert unprot_ring < 0.1 - 1e-3       # unprotected rings below background
    assert prot_ring >= unprot_ring + 1e-3  # protection lifts the ring back up

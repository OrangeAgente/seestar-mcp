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

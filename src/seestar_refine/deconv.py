"""Stage 5 of AstroPipe: Richardson-Lucy deconvolution (honest detail recovery).

Reverses the Gaussian-ish blur from optics + seeing to recover real detail that
is present in the data (unlike AI upscaling, this invents nothing — it
redistributes captured signal). Per-channel Richardson-Lucy
(``skimage.restoration``) with a Gaussian PSF whose sigma approximates the star
profile. Deconvolution amplifies noise, so keep ``iterations`` modest and run on
the *linear* master before stretch.

Pure, NaN/inf-safe, never-raising: on any failure the input is returned unchanged.
"""

from __future__ import annotations

import numpy as np


def _gaussian_psf(sigma: float) -> np.ndarray:
    """Normalized 2-D Gaussian PSF kernel for the given ``sigma`` (px)."""
    s = max(0.3, float(sigma))
    r = max(1, int(round(3.0 * s)))
    ax = np.arange(-r, r + 1)
    xx, yy = np.meshgrid(ax, ax)
    psf = np.exp(-(xx**2 + yy**2) / (2.0 * s**2))
    total = psf.sum()
    return psf / total if total > 0 else psf


def deconvolve(
    img: np.ndarray, *, psf_sigma: float = 1.5, iterations: int = 10
) -> np.ndarray:
    """Richardson-Lucy deconvolve a linear image with a Gaussian PSF.

    Each channel is normalized to ``[0, 1]``, deconvolved with
    ``skimage.restoration.richardson_lucy`` for ``iterations`` passes, then scaled
    back to its original range. Handles ``(H, W)`` mono and ``(H, W, 3)`` color.
    Never raises: on any failure (incl. skimage missing) the input is returned
    unchanged.
    """
    try:
        from skimage.restoration import richardson_lucy

        arr = np.asarray(img, dtype="float64")
        mono = arr.ndim == 2
        if mono:
            arr = arr[..., None]
        if arr.ndim != 3:
            return np.asarray(img)

        psf = _gaussian_psf(psf_sigma)
        iters = max(1, int(iterations))
        out = arr.copy()
        for c in range(arr.shape[-1]):
            ch = np.where(np.isfinite(arr[..., c]), arr[..., c], 0.0)
            mx = float(ch.max())
            if mx <= 0:
                continue
            norm = np.clip(ch / mx, 0.0, 1.0)
            dec = richardson_lucy(norm, psf, num_iter=iters, clip=True)
            out[..., c] = np.clip(dec, 0.0, 1.0) * mx

        return out[..., 0] if mono else out
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        return np.asarray(img)

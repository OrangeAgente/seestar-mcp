"""Stage 3 of AstroPipe: star-based color calibration (white balance).

Stars are, on average, near-white, so a good broadband color calibration scales
the channels so the brightest star pixels are neutral. This is a catalog-free
approximation of PixInsight's photometric color calibration: pick the brightest
``star_frac`` of pixels by luminance (one shared mask so all channels see the
same pixels), and multiply each channel so their star-level means match.

Pure, NaN/inf-safe, never-raising: on any failure the input is returned
unchanged.
"""

from __future__ import annotations

import numpy as np


def white_balance(
    img: np.ndarray, *, star_frac: float = 0.005
) -> tuple[np.ndarray, dict]:
    """Neutralize the color of the brightest stars in a linear ``(H, W, 3)`` image.

    The top ``star_frac`` fraction of pixels by luminance are treated as stars;
    each channel is scaled by ``max_level / channel_star_mean`` so those pixels
    become neutral (scales are >= 1, so no channel is darkened below itself).
    Returns ``(balanced, info)`` with ``info["scales"]`` the per-channel
    multipliers applied. Never raises.
    """
    try:
        arr = np.asarray(img, dtype="float64")
        if arr.ndim != 3 or arr.shape[-1] != 3:
            return np.asarray(img), {"scales": [1.0, 1.0, 1.0]}

        lum = np.nanmean(arr, axis=-1)
        finite = np.isfinite(lum)
        if not finite.any():
            return np.asarray(img), {"scales": [1.0, 1.0, 1.0]}

        frac = float(np.clip(star_frac, 1e-4, 0.5))
        thresh = np.nanpercentile(lum[finite], 100.0 * (1.0 - frac))
        star_mask = finite & (lum >= thresh)
        if not star_mask.any():
            return np.asarray(img), {"scales": [1.0, 1.0, 1.0]}

        levels = []
        for c in range(3):
            vals = arr[..., c][star_mask]
            vals = vals[np.isfinite(vals)]
            levels.append(float(vals.mean()) if vals.size else 0.0)
        target = max(levels)
        if target <= 0:
            return np.asarray(img), {"scales": [1.0, 1.0, 1.0]}

        scales = [target / lv if lv > 0 else 1.0 for lv in levels]
        out = arr.copy()
        for c in range(3):
            out[..., c] = arr[..., c] * scales[c]
        return out, {"scales": [round(s, 5) for s in scales]}
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        return np.asarray(img), {"scales": [1.0, 1.0, 1.0]}


# Luminance weights shared with saturation (Rec 709).
_LUM_W = np.array([0.2126, 0.7152, 0.0722], dtype="float64")


def boost_saturation(rgb: np.ndarray, amount: float = 1.0) -> np.ndarray:
    """Scale chroma about the per-pixel luminance: ``gray + amount*(rgb-gray)``.

    ``amount == 1`` is a no-op; ``>1`` boosts saturation, ``<1`` mutes it. Neutral
    (gray) pixels are unchanged. Preserves the input dtype: ``uint8`` in ->
    ``uint8`` out (clipped to 0..255), float in -> float out (clipped >= 0).
    Never raises: on bad input the array is returned unchanged.
    """
    try:
        src = np.asarray(rgb)
        if src.ndim != 3 or src.shape[-1] != 3:
            return src
        arr = src.astype("float64")
        gray = (arr @ _LUM_W)[..., None]
        out = gray + float(amount) * (arr - gray)
        if np.issubdtype(src.dtype, np.integer):
            return np.clip(out + 0.5, 0, 255).astype("uint8")
        return np.clip(out, 0.0, None)
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        return np.asarray(rgb)

"""Edge-preserving denoise for short-sub stacks (total-variation, star-safe).

Short Seestar sessions leave grainy backgrounds that an aggressive stretch makes
obvious. Total-variation denoising (`skimage.restoration.denoise_tv_chambolle`)
smooths that grain while preserving edges (stars, nebula/galaxy structure). Applied
post-stretch and gently; opt-in.

Pure, NaN/inf-safe, never-raising: on any failure the input is returned unchanged.
"""

from __future__ import annotations

import numpy as np


def denoise(rgb: np.ndarray, strength: float = 0.08) -> np.ndarray:
    """Total-variation denoise an RGB image; ``strength`` is the TV weight.

    Higher ``strength`` = smoother (typical 0.03–0.15). Preserves the input dtype
    (uint8 -> uint8, float -> float). Never raises.
    """
    try:
        from skimage.restoration import denoise_tv_chambolle

        src = np.asarray(rgb)
        if src.ndim != 3 or src.shape[-1] != 3:
            return src
        is_int = np.issubdtype(src.dtype, np.integer)
        arr = src.astype("float64")
        scale = 255.0 if is_int else max(1.0, float(np.nanmax(arr)) or 1.0)
        norm = np.clip(np.nan_to_num(arr / scale), 0.0, 1.0)
        den = denoise_tv_chambolle(norm, weight=float(strength), channel_axis=-1)
        out = den * scale
        if is_int:
            return np.clip(out + 0.5, 0, 255).astype("uint8")
        return np.clip(out, 0.0, None)
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        return np.asarray(rgb)

"""Stage 2 of AstroPipe: background / gradient removal (the DBE/ABE equivalent).

Flattens large-scale sky gradients (light pollution, moon glow, vignette) on a
*linear* master before stretch, so the stretch does not amplify an uneven sky.
Pure Python: ``photutils.Background2D`` over star-masked sky, no external app.

The model is fit on sigma-clipped sky (stars/nebulosity rejected), so subtracting
it flattens the background while leaving the target untouched. Pure, NaN/inf-safe,
never-raising: on any failure the input is returned unchanged.
"""

from __future__ import annotations

import numpy as np


def _background_map(
    channel: np.ndarray, *, box: int, mask_sigma: float
) -> np.ndarray | None:
    """Smooth 2-D sky map for one channel via star-masked ``Background2D``.

    Sigma-clips stars/nebulosity out of the fit (``mask_sigma``) so the estimate
    tracks *sky*, not signal. Returns the background map, or ``None`` if
    photutils is unavailable / the fit fails.
    """
    try:
        from astropy.stats import SigmaClip
        from photutils.background import Background2D, MedianBackground

        h, w = channel.shape
        bs = max(4, min(int(box), h, w))
        bkg = Background2D(
            np.asarray(channel, dtype="float64"),
            box_size=bs,
            filter_size=3,
            sigma_clip=SigmaClip(sigma=float(mask_sigma)),
            bkg_estimator=MedianBackground(),
        )
        return np.asarray(bkg.background, dtype="float64")
    except Exception:  # noqa: BLE001 - fall back to no map (caller no-ops)
        return None


def subtract_gradient(
    img: np.ndarray, *, box: int = 128, mask_sigma: float = 3.0
) -> tuple[np.ndarray, dict]:
    """Flatten the per-channel background gradient of a linear image.

    For each channel: fit a star-masked smooth sky map (:func:`_background_map`),
    subtract it, and re-add the map's global median as a pedestal so the image
    stays positive and every channel shares a common near-zero sky. Handles
    ``(H, W)`` mono and ``(H, W, 3)`` color.

    Returns ``(corrected, info)`` where ``info["amplitude"]`` is the per-channel
    gradient amplitude removed (map max−min) — recorded for provenance. Never
    raises: on any failure the input is returned unchanged with an empty info.
    """
    try:
        arr = np.asarray(img, dtype="float64")
        mono = arr.ndim == 2
        if mono:
            arr = arr[..., None]
        if arr.ndim != 3:
            return np.asarray(img), {"amplitude": []}

        out = arr.copy()
        amps: list[float] = []
        for c in range(arr.shape[-1]):
            ch = arr[..., c]
            bg = _background_map(ch, box=box, mask_sigma=mask_sigma)
            if bg is None or bg.shape != ch.shape:
                amps.append(0.0)
                continue
            amps.append(float(np.nanmax(bg) - np.nanmin(bg)))
            pedestal = float(np.nanmedian(bg))
            out[..., c] = ch - bg + pedestal

        out = np.clip(out, 0.0, None)
        if mono:
            out = out[..., 0]
        return out, {"amplitude": amps}
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        return np.asarray(img), {"amplitude": []}

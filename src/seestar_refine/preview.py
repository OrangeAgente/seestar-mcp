"""Auto-stretch preview: a stacked master -> an 8-bit PNG for quick review.

Two layers, both pure/never-raising:

- :func:`auto_stretch` — a screen-transfer / midtone-transfer-function (MTF)
  auto-stretch. Given a float image (2-D mono or 3-D ``(H, W, 3)`` color) it
  sets a sigma-clipped black point, normalizes to ``[0, 1]``, applies the
  PixInsight MTF with the ``midtone`` parameter, and scales to ``uint8``. Pure,
  NaN/inf-safe, and never raises (bad input -> a best-effort array).
- :func:`make_preview` — load a master (FITS via astropy, else via Pillow),
  coerce to float, :func:`auto_stretch`, save the ``uint8`` result as a PNG
  through Pillow, and return ``{"ok", "preview_path", "stats"}``. Never raises:
  a missing/unreadable input maps to ``{"ok": False, "error": ...}``.

The stretch is deliberately simple and deterministic (no display server, no
interactivity) so it can be unit-tested on synthetic FITS without any external
app.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _sigma_clipped_stats(
    flat: np.ndarray, *, sigma: float = 3.0, iters: int = 3
) -> tuple[float, float]:
    """Return (median, std) of ``flat`` after simple sigma-clipping.

    Operates on finite values only. Falls back to plain median/std (and finally
    to ``(0.0, 0.0)``) when clipping would empty the sample. Never raises.
    """
    values = flat[np.isfinite(flat)]
    if values.size == 0:
        return 0.0, 0.0
    for _ in range(iters):
        median = float(np.median(values))
        std = float(np.std(values))
        if std <= 0.0:
            break
        keep = np.abs(values - median) <= sigma * std
        clipped = values[keep]
        if clipped.size == 0 or clipped.size == values.size:
            values = clipped if clipped.size else values
            break
        values = clipped
    if values.size == 0:
        return 0.0, 0.0
    return float(np.median(values)), float(np.std(values))


def _mtf(midtone: float, x: np.ndarray) -> np.ndarray:
    """PixInsight midtone-transfer function on ``x`` in ``[0, 1]``.

    ``mtf(m, x) = ((m - 1) * x) / ((2 * m - 1) * x - m)``. Endpoints are fixed
    (``mtf(m, 0) = 0``, ``mtf(m, 1) = 1``); ``m = 0.5`` is the identity. The
    denominator never hits zero for ``x`` in ``[0, 1]`` and ``0 < m < 1``, but we
    guard it anyway. Never raises.
    """
    m = float(np.clip(midtone, 1e-6, 1.0 - 1e-6))
    denom = (2.0 * m - 1.0) * x - m
    # Guard the (theoretically unreachable) zero denominator.
    denom = np.where(np.abs(denom) < 1e-12, -m, denom)
    out = ((m - 1.0) * x) / denom
    return np.clip(out, 0.0, 1.0)


def _stretch_channel(
    channel: np.ndarray,
    *,
    black_point_sigma: float,
    midtone: float,
    white_percentile: float = 99.7,
) -> np.ndarray:
    """Auto-stretch a single 2-D channel to float ``[0, 1]``. Never raises."""
    median, std = _sigma_clipped_stats(channel)
    return _apply_stretch(
        channel,
        median=median,
        std=std,
        black_point_sigma=black_point_sigma,
        midtone=midtone,
        white_percentile=white_percentile,
    )


def _apply_stretch(
    channel: np.ndarray,
    *,
    median: float,
    std: float,
    black_point_sigma: float,
    midtone: float,
    white: float | None = None,
    white_percentile: float = 99.7,
) -> np.ndarray:
    """Normalize + MTF a channel using pre-computed ``median``/``std``.

    Split out from :func:`_stretch_channel` so a *linked* stretch can share one
    ``(median, std, white)`` transform across all channels. When ``white`` is
    ``None`` the white point is the ``white_percentile`` percentile of the finite
    pixels (default 99.7) rather than the raw maximum: a few saturated stars no
    longer crush the whole stretch, so faint/compact targets (planetaries,
    galaxy cores) keep real dynamic range. Never raises.
    """
    # Replace non-finite pixels with the robust median so they don't blow up
    # min/max normalization.
    clean = np.where(np.isfinite(channel), channel, median).astype("float64")

    black = median - black_point_sigma * std
    if white is None:
        finite = channel[np.isfinite(channel)]
        white = (
            float(np.percentile(finite, white_percentile))
            if finite.size
            else float(np.max(clean))
        )
    if not np.isfinite(white) or white <= black:
        # Degenerate/flat channel: nothing to stretch.
        return np.zeros_like(clean)

    norm = (clean - black) / (white - black)
    norm = np.clip(norm, 0.0, 1.0)
    return _mtf(midtone, norm)


def neutralize_background(
    img: np.ndarray, *, bg_percentile: float = 25.0
) -> np.ndarray:
    """Subtract each channel's sky background so the background is neutral gray.

    For a float ``(H, W, 3)`` image, each channel's background level is its
    ``bg_percentile`` percentile over finite pixels; subtracting it aligns all
    channels to a shared (~0) background. The result is clipped to ``>= 0``.
    A 2-D mono image (or any non-3-channel input) is returned unchanged. Pure,
    NaN/inf-safe, and never raises.
    """
    try:
        arr = np.asarray(img, dtype="float64")
        if arr.ndim != 3 or arr.shape[-1] != 3:
            return np.asarray(img)
        pct = float(np.clip(bg_percentile, 0.0, 100.0))
        out = arr.copy()
        for c in range(3):
            chan = arr[..., c]
            finite = chan[np.isfinite(chan)]
            bg = float(np.percentile(finite, pct)) if finite.size else 0.0
            out[..., c] = chan - bg
        return np.clip(out, 0.0, None)
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        return np.asarray(img)


def scnr_green(img: np.ndarray, *, amount: float = 1.0) -> np.ndarray:
    """Average-neutral SCNR: remove the green (OSC) tint, leaving R and B.

    On a float ``(H, W, 3)`` image, clamp green toward the red/blue average::

        G' = G - amount * max(0, G - (R + B) / 2)

    so green is never brighter than ``(R + B) / 2`` (for ``amount == 1``). Red
    and blue are untouched. A 2-D mono image (or any non-3-channel input) is
    returned unchanged. Pure, NaN/inf-safe, and never raises.
    """
    try:
        arr = np.asarray(img, dtype="float64")
        if arr.ndim != 3 or arr.shape[-1] != 3:
            return np.asarray(img)
        amt = float(amount)
        out = arr.copy()
        r = arr[..., 0]
        g = arr[..., 1]
        b = arr[..., 2]
        avg = (r + b) / 2.0
        excess = np.clip(g - avg, 0.0, None)
        excess = np.where(np.isfinite(excess), excess, 0.0)
        out[..., 1] = g - amt * excess
        return out
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        return np.asarray(img)


def auto_stretch(
    data: np.ndarray,
    *,
    black_point_sigma: float = 2.8,
    midtone: float = 0.25,
    linked: bool = False,
    white_percentile: float = 99.7,
) -> np.ndarray:
    """Auto-stretch a float image to ``uint8`` via a sigma-clipped MTF.

    Handles 2-D mono ``(H, W)`` and 3-D color ``(H, W, 3)``. NaN/inf pixels are
    replaced with the sigma-clipped median so they never dominate the
    black/white points. Pure and never raises: on bad input it returns a
    best-effort ``uint8`` array (all-zeros if it must).

    Approach: sigma-clipped median + std, black point at
    ``median - black_point_sigma * std`` (clamped to the data min), normalize to
    ``[0, 1]``, apply the PixInsight MTF with ``midtone``, scale to ``0..255``.

    Color images use a *per-channel* stretch by default (``linked=False``, the
    historical behavior). With ``linked=True`` a single black point + midtone is
    derived from the combined luminance (mean of channels) and the **same** MTF
    transform is applied to every channel, preserving color balance instead of
    equalizing the channels.
    """
    try:
        arr = np.asarray(data, dtype="float64")
        if arr.ndim == 3 and arr.shape[-1] in (3, 4):
            n = min(arr.shape[-1], 3)
            if linked:
                # One (median, std, white) from the combined data so the
                # identical transform is applied to every channel (preserving
                # color) instead of equalizing them.
                sub = arr[..., :n]
                lum = np.nanmean(sub, axis=-1)
                median, std = _sigma_clipped_stats(lum)
                finite = sub[np.isfinite(sub)]
                white = (
                    float(np.percentile(finite, white_percentile))
                    if finite.size
                    else None
                )
                channels = [
                    _apply_stretch(
                        arr[..., c],
                        median=median,
                        std=std,
                        black_point_sigma=black_point_sigma,
                        midtone=midtone,
                        white=white,
                        white_percentile=white_percentile,
                    )
                    for c in range(n)
                ]
            else:
                channels = [
                    _stretch_channel(
                        arr[..., c],
                        black_point_sigma=black_point_sigma,
                        midtone=midtone,
                        white_percentile=white_percentile,
                    )
                    for c in range(n)
                ]
            stretched = np.stack(channels, axis=-1)
        else:
            # Treat anything else (mono, or an odd shape) as a 2-D-ish channel.
            stretched = _stretch_channel(
                np.squeeze(arr),
                black_point_sigma=black_point_sigma,
                midtone=midtone,
                white_percentile=white_percentile,
            )
            if stretched.shape != arr.shape and stretched.size == arr.size:
                stretched = stretched.reshape(arr.shape)
        scaled = np.clip(stretched * 255.0 + 0.5, 0.0, 255.0)
        return scaled.astype(np.uint8)
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        try:
            return np.zeros(np.asarray(data).shape, dtype=np.uint8)
        except Exception:  # noqa: BLE001 - last-resort best effort
            return np.zeros((1,), dtype=np.uint8)


def _load_master_array(master_path: Path) -> np.ndarray:
    """Load a master (FITS via astropy, else Pillow) as a float array.

    FITS ``(3, H, W)`` color cubes are transposed to ``(H, W, 3)`` for display.
    Raises on unreadable/missing input (the caller guards).
    """
    suffix = master_path.suffix.lower()
    if suffix in (".fit", ".fits", ".fts"):
        from astropy.io import fits

        with fits.open(master_path) as hdul:
            data = None
            for hdu in hdul:
                if getattr(hdu, "data", None) is not None:
                    data = hdu.data
                    break
        if data is None:
            raise ValueError("FITS master contains no image data")
        arr = np.asarray(data, dtype="float64")
        # Astropy returns color cubes channel-first (C, H, W); make them H, W, C.
        if arr.ndim == 3 and arr.shape[0] in (3, 4) and arr.shape[-1] not in (
            3,
            4,
        ):
            arr = np.moveaxis(arr, 0, -1)
        return arr

    from PIL import Image

    with Image.open(master_path) as img:
        return np.asarray(img, dtype="float64")


def make_preview(
    master_path,
    out_png,
    *,
    params: dict | None = None,
) -> dict:
    """Auto-stretch a stacked master to an 8-bit PNG preview.

    Loads ``master_path`` (FITS via astropy; TIFF/other via Pillow), coerces to a
    float array, :func:`auto_stretch`es it, and writes ``out_png`` via Pillow.
    ``params`` may carry ``black_point_sigma`` / ``midtone`` overrides.

    For a 3-channel (OSC) master, ``color_balance`` (default ``True``) runs
    :func:`neutralize_background` -> :func:`scnr_green` -> a *linked*
    :func:`auto_stretch` (``linked`` param, default ``True``) so the quick-look
    color is neutral instead of green-cast. Set ``color_balance=False`` for the
    historical per-channel behavior (no neutralize/SCNR, unlinked stretch). Mono
    masters are unaffected either way.

    Returns ``{"ok": True, "preview_path": str, "stats": {...}}`` on success, or
    ``{"ok": False, "error": ...}`` on any failure (missing/unreadable input,
    bad data). Never raises.
    """
    master_path = Path(master_path)
    out_png = Path(out_png)
    params = params or {}
    try:
        arr = _load_master_array(master_path)
        if arr.size == 0:
            return {"ok": False, "error": "master image is empty"}

        stats = {
            "min": float(np.nanmin(arr)),
            "median": float(np.nanmedian(arr)),
            "max": float(np.nanmax(arr)),
            "shape": list(arr.shape),
        }

        # Trim the alt-az field-rotation border (black sheared parallelogram) to
        # the largest clean data rectangle BEFORE color-balance/stretch. Default
        # on; a full-valid image crops to itself (no visible change).
        if bool(params.get("autocrop", True)):
            from . import crop as _crop

            arr, crop_bbox = _crop.autocrop(arr)
            stats["crop_bbox"] = [int(v) for v in crop_bbox]
            stats["cropped_shape"] = list(arr.shape)

        kwargs = {}
        if "black_point_sigma" in params:
            kwargs["black_point_sigma"] = float(params["black_point_sigma"])
        if "midtone" in params:
            kwargs["midtone"] = float(params["midtone"])
        if "white_percentile" in params:
            kwargs["white_percentile"] = float(params["white_percentile"])

        color_balance = bool(params.get("color_balance", True))
        is_color = arr.ndim == 3 and arr.shape[-1] == 3
        if is_color and color_balance:
            balanced = neutralize_background(arr)
            balanced = scnr_green(balanced)
            linked = bool(params.get("linked", True))
            stretched = auto_stretch(balanced, linked=linked, **kwargs)
        else:
            stretched = auto_stretch(arr, **kwargs)

        from PIL import Image

        mode = "RGB" if stretched.ndim == 3 else "L"
        img = Image.fromarray(stretched, mode=mode)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_png, format="PNG")

        return {
            "ok": True,
            "preview_path": str(out_png),
            "stats": stats,
        }
    except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
        return {"ok": False, "error": str(exc)}

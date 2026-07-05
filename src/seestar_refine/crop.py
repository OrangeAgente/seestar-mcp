"""Auto-crop the alt-az field-rotation border from a stacked master.

An alt-az mount rotates the field over a session, so a stacked master's valid
data is a sheared parallelogram inside a black frame (border pixels are near
zero where not every sub covered the pixel). This module finds the largest
axis-aligned rectangle of *only* valid data and crops to it, so the downstream
color-balance / stretch never sees the black border.

Three pure, NaN/inf-safe, never-raising helpers:

- :func:`data_mask` — a boolean valid-data mask via an auto (or explicit)
  brightness threshold.
- :func:`largest_inscribed_rectangle` — the classic maximal-rectangle-in-a-
  binary-matrix scan (per-row histogram + a monotonic stack), returning the
  ``(r0, r1, c0, c1)`` bounding box of the largest all-True rectangle.
- :func:`autocrop` — glue that masks, finds the rectangle, shrinks it inward by
  a safety margin, and crops the ``(H, W)`` or ``(H, W, 3)`` image to it.

Every entry point matches the existing preview pattern: a bare-except fallback
returns the input unchanged rather than raising.
"""

from __future__ import annotations

import numpy as np


def data_mask(lum: np.ndarray, *, threshold: float | None = None) -> np.ndarray:
    """Boolean valid-data mask (same ``H x W`` as ``lum``).

    When ``threshold is None`` an auto threshold is derived from the luminance:
    ``lo = nanpercentile(lum, 1)``, ``med = nanmedian(lum)``,
    ``threshold = lo + 0.25 * (med - lo)`` — comfortably above the near-zero
    border but below real sky/signal. The mask is
    ``np.isfinite(lum) & (lum > threshold)``. Non-finite pixels are False. Never
    raises: on bad input it falls back to an all-False mask of the input shape.
    """
    try:
        arr = np.asarray(lum, dtype="float64")
        finite = np.isfinite(arr)
        if threshold is None:
            if not finite.any():
                return np.zeros(arr.shape, dtype=bool)
            lo = float(np.nanpercentile(arr, 1.0))
            med = float(np.nanmedian(arr))
            threshold = lo + 0.25 * (med - lo)
        return finite & (arr > float(threshold))
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        try:
            return np.zeros(np.asarray(lum).shape, dtype=bool)
        except Exception:  # noqa: BLE001 - last-resort best effort
            return np.zeros((1, 1), dtype=bool)


def largest_inscribed_rectangle(
    mask: np.ndarray,
) -> tuple[int, int, int, int] | None:
    """Largest all-True axis-aligned rectangle in a boolean matrix.

    Returns ``(r0, r1, c0, c1)`` with inclusive-exclusive bounds (rows
    ``r0:r1``, cols ``c0:c1``) of the maximum-area rectangle containing only
    True pixels, or ``None`` when ``mask`` has no True pixel.

    Classic O(H*W) maximal-rectangle-in-a-binary-matrix: maintain, per row, a
    histogram of the run of consecutive True heights ending at each column, then
    for each row solve largest-rectangle-in-histogram with a monotonic
    (increasing-height) stack, tracking the best area and its bounding box.
    Never raises: on bad input it returns ``None``.
    """
    try:
        m = np.asarray(mask, dtype=bool)
        if m.ndim != 2 or m.size == 0 or not m.any():
            return None

        rows, cols = m.shape
        heights = np.zeros(cols, dtype=np.int64)
        best_area = 0
        best = None  # (r0, r1, c0, c1)

        for r in range(rows):
            row = m[r]
            # Grow heights where True, reset to 0 where False.
            heights = np.where(row, heights + 1, 0)

            # Largest rectangle in this histogram via a monotonic stack.
            # Stack holds column indices with strictly increasing heights.
            stack: list[int] = []
            for c in range(cols + 1):
                cur = int(heights[c]) if c < cols else 0
                while stack and heights[stack[-1]] >= cur:
                    top = stack.pop()
                    h = int(heights[top])
                    left = stack[-1] + 1 if stack else 0
                    width = c - left
                    area = h * width
                    if area > best_area and h > 0 and width > 0:
                        best_area = area
                        # Rectangle spans rows [r - h + 1, r] and cols
                        # [left, c) -> inclusive-exclusive box.
                        best = (r - h + 1, r + 1, left, c)
                stack.append(c)

        return best
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        return None


def autocrop(
    img: np.ndarray, *, threshold: float | None = None, margin: int = 2
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Crop ``img`` to the largest inscribed rectangle of valid data.

    Computes luminance (mean over the last axis for ``(H, W, 3)``, else the
    array itself for ``(H, W)``), a :func:`data_mask`, and the
    :func:`largest_inscribed_rectangle`. The rectangle is shrunk inward by
    ``margin`` px (clamped so it stays valid and non-empty) to shave any soft
    edge, then ``img`` is cropped to ``[r0:r1, c0:c1]`` (works for both mono and
    color). Returns ``(cropped, (r0, r1, c0, c1))``.

    When no rectangle is found (empty mask / all-border / garbage) the original
    image and its full bounding box are returned. Never raises.
    """
    try:
        arr = np.asarray(img)
        if arr.ndim == 3:
            lum = np.asarray(arr, dtype="float64").mean(axis=-1)
        else:
            lum = np.asarray(arr, dtype="float64")

        mask = data_mask(lum, threshold=threshold)
        box = largest_inscribed_rectangle(mask)
        h, w = lum.shape[0], lum.shape[1]
        if box is None:
            return arr, (0, h, 0, w)

        r0, r1, c0, c1 = box
        mg = max(0, int(margin))
        if mg > 0:
            # Shrink inward, but never past a 1-px rectangle.
            nr0 = min(r0 + mg, r1 - 1)
            nr1 = max(r1 - mg, nr0 + 1)
            nc0 = min(c0 + mg, c1 - 1)
            nc1 = max(c1 - mg, nc0 + 1)
            r0, r1, c0, c1 = nr0, nr1, nc0, nc1

        cropped = arr[r0:r1, c0:c1]
        return cropped, (r0, r1, c0, c1)
    except Exception:  # noqa: BLE001 - never-raise contract, return input as-is
        arr = np.asarray(img)
        try:
            h, w = arr.shape[0], arr.shape[1]
        except Exception:  # noqa: BLE001 - last-resort best effort
            h, w = 0, 0
        return arr, (0, h, 0, w)

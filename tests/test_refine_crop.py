"""Unit tests for seestar_refine.crop (auto-crop the field-rotation border)."""

from __future__ import annotations

import numpy as np


def _no_false_inside(mask, box):
    r0, r1, c0, c1 = box
    return bool(np.all(mask[r0:r1, c0:c1]))


def test_largest_inscribed_rectangle_simple():
    from seestar_refine.crop import largest_inscribed_rectangle

    mask = np.zeros((10, 12), dtype=bool)
    mask[2:7, 3:9] = True  # a known True rectangle in the middle
    box = largest_inscribed_rectangle(mask)
    assert box == (2, 7, 3, 9)
    assert _no_false_inside(mask, box)


def test_largest_inscribed_rectangle_parallelogram():
    from seestar_refine.crop import largest_inscribed_rectangle

    h, w = 40, 40
    mask = np.zeros((h, w), dtype=bool)
    # A sheared parallelogram of True inside a False frame.
    for r in range(h):
        shift = r // 2
        lo = 5 + shift
        hi = 30 + shift
        lo = max(0, min(w, lo))
        hi = max(0, min(w, hi))
        mask[r, lo:hi] = True

    box = largest_inscribed_rectangle(mask)
    assert box is not None
    r0, r1, c0, c1 = box
    assert (r1 - r0) > 0 and (c1 - c0) > 0  # non-trivial
    assert _no_false_inside(mask, box)  # fully inside the valid region


def test_largest_inscribed_rectangle_empty():
    from seestar_refine.crop import largest_inscribed_rectangle

    assert largest_inscribed_rectangle(np.zeros((6, 6), dtype=bool)) is None

    full = np.ones((6, 8), dtype=bool)
    assert largest_inscribed_rectangle(full) == (0, 6, 0, 8)


def test_data_mask_thresholds_border():
    from seestar_refine.crop import data_mask

    lum = np.zeros((20, 20), dtype="float64")
    lum[5:15, 5:15] = 1000.0
    mask = data_mask(lum)
    assert mask.dtype == bool
    assert mask.shape == lum.shape
    # Border (0) excluded, inner signal included.
    assert not mask[0, 0]
    assert mask[10, 10]


def test_data_mask_nan_safe():
    from seestar_refine.crop import data_mask

    lum = np.full((8, 8), np.nan)
    lum[2:6, 2:6] = np.linspace(200.0, 2000.0, 16).reshape(4, 4)
    mask = data_mask(lum)  # must not raise
    assert mask.dtype == bool
    assert not mask[0, 0]  # NaN is not finite -> False
    assert mask[2:6, 2:6].any()  # bright signal above the auto threshold


def test_autocrop_trims_black_border():
    from seestar_refine.crop import autocrop

    h, w = 30, 30
    img = np.zeros((h, w, 3), dtype="float64")
    img[6:24, 8:22, :] = 1000.0  # bright inner rectangle on a 0 border
    cropped, box = autocrop(img, margin=2)
    r0, r1, c0, c1 = box
    assert cropped.shape[0] < h and cropped.shape[1] < w
    assert 0 <= r0 < r1 <= h and 0 <= c0 < c1 <= w
    # Every corner of the crop is real data (> 0), border trimmed.
    assert cropped[0, 0, 0] > 0
    assert cropped[-1, -1, 0] > 0
    assert cropped[0, -1, 0] > 0
    assert cropped[-1, 0, 0] > 0


def test_autocrop_mono_trims_black_border():
    from seestar_refine.crop import autocrop

    h, w = 30, 30
    img = np.zeros((h, w), dtype="float64")
    img[6:24, 8:22] = 1000.0
    cropped, box = autocrop(img, margin=2)
    assert cropped.ndim == 2
    assert cropped.shape[0] < h and cropped.shape[1] < w
    assert cropped[0, 0] > 0
    assert cropped[-1, -1] > 0


def test_autocrop_no_border_is_noop():
    from seestar_refine.crop import autocrop

    # A uniformly bright, borderless image: every pixel clears the
    # coverage-relative threshold (0.85 * median), so the whole frame is valid
    # and the crop is the full frame minus the safety margin.
    img = np.full((24, 24, 3), 1000.0)
    cropped, box = autocrop(img, margin=2)
    r0, r1, c0, c1 = box
    # Full-valid image -> crop is essentially the whole frame (allowing margin).
    assert r0 <= 2 and c0 <= 2
    assert r1 >= 24 - 2 and c1 >= 24 - 2
    assert cropped.shape[0] >= 24 - 4 and cropped.shape[1] >= 24 - 4


def test_autocrop_never_raises_on_garbage():
    from seestar_refine.crop import autocrop

    img = np.full((10, 10, 3), np.nan)
    cropped, box = autocrop(img)  # all-NaN -> empty mask -> original returned
    assert cropped.shape == (10, 10, 3)
    assert box == (0, 10, 0, 10)

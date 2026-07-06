"""Tests for Stage 2 background/gradient removal."""

from __future__ import annotations

import numpy as np


def test_subtract_gradient_removes_ramp_preserves_stars():
    from seestar_refine.gradient import subtract_gradient

    h, w = 128, 128
    _, xx = np.mgrid[0:h, 0:w]
    sky = 100.0 + (xx / w) * 60.0  # linear ramp 100 -> 160 across x
    img = np.stack([sky] * 3, axis=-1).astype("float64")
    img[30, 30, :] = 5000.0  # bright stars on the gradient
    img[90, 100, :] = 4000.0

    # box ~ 1/16 of the frame gives an adequate mesh for this small synthetic
    # (the 128 px default suits real 1920x1080 frames).
    out, info = subtract_gradient(img, box=8, mask_sigma=3.0)

    # Gradient removed: left vs right background now nearly equal (was ~55 apart).
    left = out[10:118, 5:15, 1].mean()
    right = out[10:118, 113:123, 1].mean()
    assert abs(left - right) < 5.0
    # Stars preserved (masked out of the background fit).
    assert out[30, 30, 1] > 3000.0
    assert info["amplitude"][1] > 45.0  # it reported a real gradient removed


def test_subtract_gradient_never_raises():
    from seestar_refine.gradient import subtract_gradient

    bad = np.full((8, 8, 3), np.nan)
    out, info = subtract_gradient(bad)
    assert out.shape == (8, 8, 3)  # returns input-shaped, no exception


def test_make_preview_gradient_flag(tmp_path):
    from astropy.io import fits

    from seestar_refine.preview import make_preview

    h, w = 128, 128
    _, xx = np.mgrid[0:h, 0:w]
    sky = 100.0 + (xx / w) * 60.0
    cube = np.stack([sky] * 3, axis=0).astype("float32")  # (3, H, W) like a master
    p = tmp_path / "m.fit"
    fits.writeto(p, cube, overwrite=True)

    r = make_preview(
        p, tmp_path / "m.png",
        params={"gradient": True, "gradient_box": 8, "autocrop": False},
    )
    assert r["ok"] is True
    assert "gradient" in r["stats"]
    assert r["stats"]["gradient"]["amplitude"][1] > 40.0

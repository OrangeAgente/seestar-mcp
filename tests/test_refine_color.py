"""Stage 3: star-based color calibration (white balance)."""

from __future__ import annotations

import numpy as np


def test_white_balance_neutralizes_star_color():
    from seestar_refine.color import white_balance

    rng = np.random.RandomState(0)
    h, w = 128, 128
    img = np.full((h, w, 3), 10.0)  # gray background
    # 100 neutral (white) stars
    ys = rng.randint(0, h, 100)
    xs = rng.randint(0, w, 100)
    img[ys, xs, :] = 1000.0
    # impose a color cast: R too strong, B too weak
    cast = img * np.array([1.5, 1.0, 0.7])

    out, info = white_balance(cast, star_frac=0.006)
    # brightest stars should be neutral again (R ~= G ~= B)
    star = out[ys[0], xs[0]]
    assert abs(star[0] - star[1]) < 0.08 * star[1]
    assert abs(star[2] - star[1]) < 0.08 * star[1]
    assert len(info["scales"]) == 3


def test_white_balance_never_raises():
    from seestar_refine.color import white_balance

    out, info = white_balance(np.full((4, 4, 3), np.nan))
    assert out.shape == (4, 4, 3)


def test_boost_saturation_pushes_color_keeps_gray():
    from seestar_refine.color import boost_saturation

    reddish = np.array([[[200.0, 100.0, 100.0]]])
    out = boost_saturation(reddish, 1.6)
    assert out[0, 0, 0] > reddish[0, 0, 0]      # red channel pushed further up
    assert out[0, 0, 1] < reddish[0, 0, 1]      # green/blue pushed down
    # a neutral gray pixel is unchanged by any saturation factor
    gray = np.array([[[128.0, 128.0, 128.0]]])
    assert np.allclose(boost_saturation(gray, 2.0), gray, atol=1e-6)


def test_boost_saturation_uint8_roundtrips_dtype():
    from seestar_refine.color import boost_saturation

    img = np.full((4, 4, 3), 100, dtype="uint8")
    img[..., 0] = 180
    out = boost_saturation(img, 1.5)
    assert out.dtype == np.uint8
    assert out.max() <= 255

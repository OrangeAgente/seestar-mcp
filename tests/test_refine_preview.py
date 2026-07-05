"""Unit tests for seestar_refine.preview (auto_stretch + make_preview)."""

from __future__ import annotations

import numpy as np


def test_auto_stretch_maps_to_uint8():
    from seestar_refine.preview import auto_stretch

    data = np.random.default_rng(0).normal(1000, 50, (64, 64)).astype("float32")
    out = auto_stretch(data)
    assert out.dtype == np.uint8
    assert out.shape == (64, 64)
    assert out.max() > out.min()


def test_auto_stretch_all_nan_returns_uint8():
    from seestar_refine.preview import auto_stretch

    data = np.full((32, 32), np.nan, dtype="float32")
    out = auto_stretch(data)  # must not raise
    assert out.dtype == np.uint8
    assert out.shape == (32, 32)


def test_auto_stretch_color_stays_three_channel():
    from seestar_refine.preview import auto_stretch

    rng = np.random.default_rng(1)
    data = rng.normal(1000, 50, (48, 48, 3)).astype("float32")
    out = auto_stretch(data)
    assert out.dtype == np.uint8
    assert out.shape == (48, 48, 3)


def test_make_preview_writes_png(tmp_path):
    from astropy.io import fits

    d = np.random.default_rng(0).normal(1000, 50, (64, 64)).astype("float32")
    d[30:34, 30:34] += 5000  # a star
    fp = tmp_path / "master.fit"
    fits.writeto(fp, d)

    from seestar_refine.preview import make_preview

    r = make_preview(fp, tmp_path / "prev.png")
    assert r["ok"] is True
    assert (tmp_path / "prev.png").exists()
    assert "median" in r["stats"]
    assert r["preview_path"].endswith("prev.png")


def test_make_preview_missing_file(tmp_path):
    from seestar_refine.preview import make_preview

    r = make_preview(tmp_path / "nope.fit", tmp_path / "out.png")
    assert r["ok"] is False
    assert r.get("error")
    assert not (tmp_path / "out.png").exists()


def _bg_percentile(channel, pct=25.0):
    finite = channel[np.isfinite(channel)]
    return float(np.percentile(finite, pct))


def test_neutralize_background_aligns_channels():
    from seestar_refine.preview import neutralize_background

    rng = np.random.default_rng(2)
    base = rng.normal(100, 5, (40, 40))
    img = np.stack(
        [base + 100.0, base + 300.0, base + 200.0], axis=-1
    ).astype("float64")

    out = neutralize_background(img)
    assert out.shape == img.shape
    bgs = [_bg_percentile(out[..., c]) for c in range(3)]
    # All channel backgrounds should now be ~equal and near zero.
    assert max(bgs) - min(bgs) < 2.0
    for b in bgs:
        assert abs(b) < 2.0
    # Clipped to >= 0.
    assert float(np.min(out)) >= 0.0


def test_neutralize_background_mono_unchanged():
    from seestar_refine.preview import neutralize_background

    mono = np.random.default_rng(3).normal(100, 5, (20, 20)).astype("float64")
    out = neutralize_background(mono)
    assert np.array_equal(out, mono)


def test_neutralize_background_never_raises():
    from seestar_refine.preview import neutralize_background

    img = np.full((8, 8, 3), np.nan, dtype="float64")
    out = neutralize_background(img)  # must not raise
    assert out.shape == (8, 8, 3)


def test_scnr_green_reduces_green_cast():
    from seestar_refine.preview import scnr_green

    # G strongly above (R + B) / 2 everywhere.
    r = np.full((16, 16), 100.0)
    b = np.full((16, 16), 100.0)
    g = np.full((16, 16), 300.0)
    img = np.stack([r, g, b], axis=-1)

    out = scnr_green(img)
    eps = 1e-6
    assert np.all(out[..., 1] <= (out[..., 0] + out[..., 2]) / 2.0 + eps)
    # R and B untouched.
    assert np.array_equal(out[..., 0], r)
    assert np.array_equal(out[..., 2], b)


def test_scnr_green_leaves_low_green_unchanged():
    from seestar_refine.preview import scnr_green

    r = np.full((16, 16), 300.0)
    b = np.full((16, 16), 300.0)
    g = np.full((16, 16), 100.0)  # already below (R + B) / 2
    img = np.stack([r, g, b], axis=-1)

    out = scnr_green(img)
    assert np.array_equal(out, img)


def test_scnr_green_mono_unchanged():
    from seestar_refine.preview import scnr_green

    mono = np.random.default_rng(4).normal(100, 5, (12, 12))
    out = scnr_green(mono)
    assert np.array_equal(out, mono)


def test_auto_stretch_linked_uses_one_transform():
    from seestar_refine.preview import auto_stretch

    rng = np.random.default_rng(5)
    base = rng.normal(1000, 50, (48, 48))
    img = np.stack(
        [base, base + 400.0, base + 200.0], axis=-1
    ).astype("float64")

    linked = auto_stretch(img, linked=True)
    unlinked = auto_stretch(img, linked=False)

    assert linked.dtype == np.uint8
    assert linked.shape == (48, 48, 3)
    # A single shared transform preserves the per-channel offsets, so linked and
    # per-channel outputs must differ.
    assert not np.array_equal(linked, unlinked)


def test_auto_stretch_default_is_unlinked():
    from seestar_refine.preview import auto_stretch

    rng = np.random.default_rng(6)
    img = rng.normal(1000, 50, (32, 32, 3)).astype("float64")
    assert np.array_equal(auto_stretch(img), auto_stretch(img, linked=False))


def _make_green_cast_fits(tmp_path):
    from astropy.io import fits

    rng = np.random.default_rng(7)
    h, w = 64, 64
    r = rng.normal(500, 20, (h, w))
    b = rng.normal(500, 20, (h, w))
    g = rng.normal(500, 20, (h, w))
    # Common stars (equal in every channel) set a shared white point.
    ys = rng.integers(0, h, 10)
    xs = rng.integers(0, w, 10)
    for y, x in zip(ys, xs):
        r[y, x] += 15000.0
        g[y, x] += 15000.0
        b[y, x] += 15000.0
    # A broad green-only signal: real extra structure (not an affine scale of
    # R), so per-channel stretch keeps green dominant while SCNR removes it.
    grad = np.linspace(0.0, 4000.0, h)[:, None] * np.ones((1, w))
    g = g + grad
    cube = np.stack([r, g, b], axis=0).astype("float32")  # (3, H, W)
    fp = tmp_path / "green_master.fit"
    fits.writeto(fp, cube)
    return fp


def _png_channel_means(path):
    from PIL import Image

    arr = np.asarray(Image.open(path).convert("RGB"), dtype="float64")
    return (
        float(arr[..., 0].mean()),
        float(arr[..., 1].mean()),
        float(arr[..., 2].mean()),
    )


def test_make_preview_color_balance_reduces_green(tmp_path):
    from seestar_refine.preview import make_preview

    fp = _make_green_cast_fits(tmp_path)

    balanced = make_preview(
        fp, tmp_path / "balanced.png", params={"color_balance": True}
    )
    assert balanced["ok"] is True
    mr, mg, mb = _png_channel_means(tmp_path / "balanced.png")
    # Green cast removed: green no longer dominant.
    assert mg <= max(mr, mb) + 5.0

    raw = make_preview(
        fp, tmp_path / "raw.png", params={"color_balance": False}
    )
    assert raw["ok"] is True
    rr, rg, rb = _png_channel_means(tmp_path / "raw.png")
    # Without balancing, green stays dominant.
    assert rg > max(rr, rb)

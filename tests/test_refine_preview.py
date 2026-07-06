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


def _make_bordered_fits(tmp_path):
    from astropy.io import fits

    rng = np.random.default_rng(11)
    h, w = 64, 64
    r = np.zeros((h, w))
    g = np.zeros((h, w))
    b = np.zeros((h, w))
    # A valid data rectangle inside a black (0) field-rotation border.
    r[10:54, 12:52] = rng.normal(800, 20, (44, 40))
    g[10:54, 12:52] = rng.normal(800, 20, (44, 40))
    b[10:54, 12:52] = rng.normal(800, 20, (44, 40))
    cube = np.stack([r, g, b], axis=0).astype("float32")  # (3, H, W)
    fp = tmp_path / "bordered_master.fit"
    fits.writeto(fp, cube)
    return fp, (h, w)


def _png_size(path):
    from PIL import Image

    with Image.open(path) as im:
        return im.size  # (width, height)


def test_make_preview_autocrop(tmp_path):
    from seestar_refine.preview import make_preview

    fp, (h, w) = _make_bordered_fits(tmp_path)

    cropped = make_preview(
        fp, tmp_path / "cropped.png", params={"autocrop": True}
    )
    assert cropped["ok"] is True
    box = cropped["stats"]["crop_bbox"]
    r0, r1, c0, c1 = box
    # The crop bbox is strictly smaller than the full frame.
    assert (r1 - r0) < h and (c1 - c0) < w
    assert cropped["stats"]["cropped_shape"][0] == (r1 - r0)
    assert cropped["stats"]["cropped_shape"][1] == (c1 - c0)
    cw, ch = _png_size(tmp_path / "cropped.png")
    assert cw == (c1 - c0) and ch == (r1 - r0)

    full = make_preview(
        fp, tmp_path / "full.png", params={"autocrop": False}
    )
    assert full["ok"] is True
    fw, fh = _png_size(tmp_path / "full.png")
    assert fw == w and fh == h


def test_make_preview_autocrop_default_noop_on_full_valid(tmp_path):
    from astropy.io import fits

    from seestar_refine.preview import make_preview

    # Uniformly bright, borderless master: nothing is below the auto threshold
    # boundary, so autocrop is a no-op (empty mask -> full frame returned).
    d = np.full((48, 48), 1000.0, dtype="float32")
    fp = tmp_path / "full_valid.fit"
    fits.writeto(fp, d)

    r = make_preview(fp, tmp_path / "prev.png")  # autocrop defaults True
    assert r["ok"] is True
    # A full-valid image crops to itself (allowing the inward margin).
    box = r["stats"]["crop_bbox"]
    r0, r1, c0, c1 = box
    assert r0 <= 2 and c0 <= 2 and r1 >= 46 and c1 >= 46


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


def test_auto_stretch_percentile_white_reveals_faint_signal():
    # A few saturated stars must NOT crush the stretch of a faint compact target.
    # Percentile white (default) ignores the hot pixels; a max white point (100)
    # maps the target to a fraction of the range and buries it.
    import numpy as np

    from seestar_refine.preview import auto_stretch

    img = np.full((100, 100), 0.01, dtype="float64")
    img[40:60, 40:60] = 0.12  # faint compact target (e.g. a small planetary)
    img[0, 0] = img[0, 1] = img[1, 0] = 1.0  # a handful of saturated star pixels

    hi = auto_stretch(img, white_percentile=99.7)   # ignores the hot pixels
    lo = auto_stretch(img, white_percentile=100.0)   # max = star crushes the stretch
    target_hi = float(hi[40:60, 40:60].mean())
    target_lo = float(lo[40:60, 40:60].mean())
    assert target_hi > target_lo + 30  # meaningfully brighter with percentile white
    assert target_hi > 150             # and actually visible


def test_make_preview_full_pipeline(tmp_path):
    # Every opt-in stage chained: gradient -> white_balance -> deconv -> stretch
    # -> saturation -> upscale. Assert it runs end-to-end and records each stage.
    from astropy.io import fits

    from seestar_refine.preview import make_preview

    h, w = 128, 128
    _, xx = np.mgrid[0:h, 0:w]
    base = 100.0 + (xx / w) * 40.0
    cube = np.stack([base, base, base], axis=0).astype("float32")
    cube[0, 30:33, 30:33] = 3000.0  # a red-biased star (exercises white balance)
    p = tmp_path / "m.fit"
    fits.writeto(p, cube, overwrite=True)

    r = make_preview(p, tmp_path / "m.png", params={
        "autocrop": False, "gradient": True, "gradient_box": 8,
        "white_balance": True, "deconv": True, "deconv_iters": 3,
        "saturation": 1.4, "upscale": 2,
    })
    assert r["ok"] is True
    s = r["stats"]
    for key in ("gradient", "white_balance", "deconv", "upscale"):
        assert key in s
    from PIL import Image

    im = Image.open(r["preview_path"])
    assert im.width == w * 2  # upscaled 2x (autocrop off)


def test_asinh_stretch_compresses_core_preserves_color():
    from seestar_refine.preview import asinh_stretch

    img = np.full((16, 16, 3), [0.010, 0.009, 0.008])  # dark sky background
    img[4:12, 4:12] = [0.045, 0.036, 0.027]            # faint golden disk over sky
    img[8, 8] = [1.0, 0.7, 0.4]                        # bright golden core

    out = asinh_stretch(img, beta=0.08, black_point_sigma=-0.5)
    assert out.dtype == np.uint8
    # faint disk lifted to clearly visible; sky stays dark (highlight-safe stretch)
    assert out[6, 6].max() > 40
    assert out[0, 0].max() < 30
    # core stays warm (blue clearly below red), not a neutral white blob
    core = out[8, 8]
    assert int(core[2]) < int(core[0]) - 30

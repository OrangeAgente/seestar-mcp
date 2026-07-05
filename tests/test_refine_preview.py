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

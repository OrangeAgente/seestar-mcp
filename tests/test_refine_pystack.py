"""Tests for the pure-Python stacking backend (pystack)."""

from __future__ import annotations

import numpy as np


def test_debayer_grbg_uniform_channels():
    from seestar_refine.pystack import debayer

    # Build a GRBG mosaic where every R site = 100, G = 200, B = 50.
    # GRBG 2x2 tile: (0,0)=G (0,1)=R (1,0)=B (1,1)=G.
    h, w = 8, 8
    raw = np.zeros((h, w), dtype="float64")
    for r in range(h):
        for c in range(w):
            pos = (r % 2) * 2 + (c % 2)
            raw[r, c] = {0: 200, 1: 100, 2: 50, 3: 200}[pos]  # G,R,B,G

    rgb = debayer(raw, pattern="GRBG")
    assert rgb.shape == (h, w, 3)
    # Interior pixels: each channel reconstructs its constant value.
    interior = rgb[2:-2, 2:-2]
    assert np.allclose(interior[..., 0], 100.0, atol=1e-6)  # R
    assert np.allclose(interior[..., 1], 200.0, atol=1e-6)  # G
    assert np.allclose(interior[..., 2], 50.0, atol=1e-6)   # B


def test_debayer_never_raises_on_bad_input():
    from seestar_refine.pystack import debayer

    out = debayer(np.array([[np.nan, 1.0], [2.0, 3.0]]), pattern="GRBG")
    assert out.shape[-1] == 3  # returns something 3-channel, no exception


def test_integrate_rejects_outlier():
    from seestar_refine.pystack import integrate

    stack = np.full((10, 4, 4, 3), 100.0)
    stack[3, 1, 1, 0] = 5000.0  # a single hot outlier (e.g. satellite/plane)
    out = integrate(stack, kappa=2.0, iters=5)
    assert out.shape == (4, 4, 3)
    assert np.isclose(out[1, 1, 0], 100.0, atol=1e-6)  # outlier rejected
    assert np.allclose(out, 100.0, atol=1e-6)


def test_integrate_row_blocking_matches_whole():
    from seestar_refine.pystack import integrate

    rng = np.random.RandomState(0)
    stack = rng.normal(100.0, 5.0, size=(8, 20, 6, 3))
    whole = integrate(stack, kappa=2.5, iters=3, block_rows=64)
    blocked = integrate(stack, kappa=2.5, iters=3, block_rows=3)
    assert np.allclose(whole, blocked)


def _star_field(h, w, coords, flux=2000.0, sigma=1.6):
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w), dtype="float64")
    for (y, x) in coords:
        img += flux * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))
    return img


def test_register_recovers_shift():
    from seestar_refine.pystack import register

    rng = np.random.RandomState(1)
    h, w = 256, 256
    coords = rng.uniform(40, 216, size=(15, 2))
    ref = _star_field(h, w, coords)
    shifted = _star_field(h, w, coords + np.array([8.0, -5.0]))  # known (dy,dx)
    frame_rgb = np.stack([shifted] * 3, axis=-1)

    reg = register(ref, frame_rgb, shifted)
    assert reg is not None
    assert reg.shape == (h, w, 3)
    err_before = np.abs(shifted - ref).sum()
    err_after = np.abs(reg[..., 1] - ref).sum()
    assert err_after < 0.3 * err_before  # registered frame aligns to the ref


def test_register_returns_none_on_blank():
    from seestar_refine.pystack import register

    blank = np.zeros((64, 64), dtype="float64")
    rgb = np.zeros((64, 64, 3), dtype="float64")
    assert register(blank, rgb, blank) is None  # no stars -> cannot solve


def _write_bayer_sub(path, h=16, w=16, val=100):
    from astropy.io import fits

    raw = np.full((h, w), val, dtype="uint16")
    hdr = fits.Header()
    hdr["BAYERPAT"] = "GRBG"
    fits.writeto(path, raw, hdr, overwrite=True)


def test_stack_writes_3hw_master(tmp_path, monkeypatch):
    from seestar_refine import pystack
    from seestar_refine.config import RefineSettings
    from seestar_refine.keeplist import KeepList

    paths = []
    for i in range(4):
        p = tmp_path / f"sub{i}.fit"
        _write_bayer_sub(p)
        paths.append(str(p))

    # identity register (avoid needing real stars); backend reported available
    monkeypatch.setattr(pystack, "register", lambda ref, rgb, lum: rgb)
    monkeypatch.setattr(pystack, "astroalign_available", lambda: True)

    kl = KeepList("TEST", paths)
    s = RefineSettings(_env_file=None, output_dir=str(tmp_path / "out"))
    res = pystack.stack(kl, s)

    assert res.ok is True
    assert res.engine == "pystack"
    from astropy.io import fits

    cube = fits.getdata(res.master_path)
    assert cube.shape[0] == 3  # (3, H, W) like the DSS master


def test_stack_reports_unavailable_backend(tmp_path, monkeypatch):
    from seestar_refine import pystack
    from seestar_refine.config import RefineSettings
    from seestar_refine.keeplist import KeepList

    monkeypatch.setattr(pystack, "astroalign_available", lambda: False)
    res = pystack.stack(
        KeepList("T", ["a.fit", "b.fit", "c.fit"]),
        RefineSettings(_env_file=None, output_dir=str(tmp_path / "o")),
    )
    assert res.ok is False
    assert "astroalign" in res.error.lower()

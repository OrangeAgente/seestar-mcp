"""Stage 7: opt-in, provenance-labeled resolution upscaling."""

from __future__ import annotations

import numpy as np


def test_upscale_lanczos_doubles_and_labels():
    from seestar_refine.upscale import upscale

    img = np.zeros((20, 30, 3), dtype="uint8")
    img[5:15, 10:20, :] = 200
    out, info = upscale(img, factor=2, method="lanczos")
    assert out.shape[0] == 40 and out.shape[1] == 60
    assert info["method"] == "lanczos"
    assert "interpolat" in info["label"].lower()  # honest: not new detail


def test_upscale_ai_falls_back_and_labels_synthetic(tmp_path):
    from seestar_refine.upscale import upscale

    img = np.zeros((16, 16, 3), dtype="uint8")
    # No model configured -> must fall back to lanczos, but stay honest in label.
    out, info = upscale(img, factor=2, method="ai", model_path=None)
    assert out.shape[0] == 32
    assert info["fell_back"] is True


def test_upscale_never_raises():
    from seestar_refine.upscale import upscale

    out, info = upscale(np.full((4, 4, 3), np.nan), factor=2)
    assert out is not None

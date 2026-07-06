"""Denoise stage: edge-preserving noise reduction for short-sub stacks."""

from __future__ import annotations

import numpy as np


def test_denoise_reduces_noise_preserves_edge():
    from seestar_refine.denoise import denoise

    rng = np.random.RandomState(0)
    clean = np.full((64, 64, 3), 120.0)
    clean[:, :32] = 80.0  # a hard edge (dark left / bright right)
    noisy = np.clip(clean + rng.normal(0, 15, clean.shape), 0, 255).astype("uint8")

    out = denoise(noisy, strength=0.12)
    assert out.dtype == np.uint8
    # noise reduced in a flat region
    assert out[:, 40:].std() < noisy[:, 40:].std()
    # edge preserved (left still clearly darker than right)
    assert out[:, :20].mean() < out[:, 44:].mean() - 20


def test_denoise_never_raises():
    from seestar_refine.denoise import denoise

    out = denoise(np.full((4, 4, 3), np.nan))
    assert out.shape == (4, 4, 3)

"""Deterministic synthetic FITS fixtures for Tier-2 QA tests.

Renders three synthetic Seestar-like subs with a *seeded* numpy RNG so the
committed ``.fits`` files are byte-reproducible and the QA assertions are stable:

- ``good.fits``     ~40 ROUND, bright stars   -> PASS  (low FWHM, ecc~0, high SNR)
- ``bad_ecc.fits``  ~40 ELONGATED stars       -> REJECT (eccentricity ~0.81 >= 0.575)
- ``bad_snr.fits``  ~10 FAINT round stars      -> REJECT (SNR + star-count floors)

Each star is an ``astropy.modeling.models.Gaussian2D`` stamped into a local box,
plus a flat sky background and Gaussian read/shot noise. Star positions and noise
are drawn from independent child streams spawned off a single base seed (1234),
so every fixture is reproducible in isolation.

Run directly (``uv run python tests/fixtures/make_fixtures.py``) or import
``make_all`` from the test module to (re)create the fixtures on demand.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.modeling.models import Gaussian2D

# Base seed for the whole fixture set. Child streams are spawned per fixture so
# each file is independently reproducible.
BASE_SEED = 1234

# Image geometry (shared by all fixtures).
IMAGE_SHAPE = (512, 512)
# Half-width of the local render box around each star centre (pixels).
STAMP_HALF = 15
# Keep star centres this far from the frame edge so stamps stay in-bounds.
EDGE_MARGIN = 20


@dataclass(frozen=True)
class FixtureSpec:
    """Parameters for one synthetic sub."""

    name: str
    n_stars: int
    sigma_x: float
    sigma_y: float
    amplitude: float
    background: float
    noise: float
    seed_offset: int


# The three canonical fixtures. Parameters were tuned against photutils 2.3.0
# segmentation detection (5 sigma threshold, npixels=5) so detection is reliable:
#   good    -> 40 stars, FWHM ~4.78 px, ecc ~0.14, SNR ~214
#   bad_ecc -> ~37 stars, ecc ~0.81 (well above the 0.575 cutoff)
#   bad_snr -> ~10 faint stars, SNR ~29 (far below 0.5x the session median)
SPECS: tuple[FixtureSpec, ...] = (
    FixtureSpec("good", n_stars=40, sigma_x=2.0, sigma_y=2.0,
                amplitude=800.0, background=100.0, noise=10.0, seed_offset=1),
    FixtureSpec("bad_ecc", n_stars=40, sigma_x=3.2, sigma_y=1.6,
                amplitude=800.0, background=100.0, noise=10.0, seed_offset=2),
    FixtureSpec("bad_snr", n_stars=10, sigma_x=2.0, sigma_y=2.0,
                amplitude=200.0, background=200.0, noise=20.0, seed_offset=3),
)


def render(spec: FixtureSpec) -> np.ndarray:
    """Render one synthetic sub as a float32 image array from ``spec``."""
    rng = np.random.default_rng([BASE_SEED, spec.seed_offset])
    ny, nx = IMAGE_SHAPE
    img = np.zeros((ny, nx), dtype=np.float64)

    xs = rng.uniform(EDGE_MARGIN, nx - EDGE_MARGIN, spec.n_stars)
    ys = rng.uniform(EDGE_MARGIN, ny - EDGE_MARGIN, spec.n_stars)
    for x, y in zip(xs, ys):
        model = Gaussian2D(
            amplitude=spec.amplitude,
            x_mean=x, y_mean=y,
            x_stddev=spec.sigma_x, y_stddev=spec.sigma_y,
        )
        y0, y1 = int(y - STAMP_HALF), int(y + STAMP_HALF)
        x0, x1 = int(x - STAMP_HALF), int(x + STAMP_HALF)
        gy, gx = np.mgrid[y0:y1, x0:x1]
        img[y0:y1, x0:x1] += model(gx, gy)

    img += spec.background
    img += rng.normal(0.0, spec.noise, img.shape)
    return img.astype(np.float32)


def write_fixture(spec: FixtureSpec, out_dir: Path) -> Path:
    """Render ``spec`` and write ``<name>.fits`` into ``out_dir``; return path."""
    data = render(spec)
    header = fits.Header()
    header["OBJECT"] = spec.name
    header["QAFIX"] = (True, "synthetic QA fixture")
    header["NSTARS"] = (spec.n_stars, "injected star count")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{spec.name}.fits"
    fits.writeto(path, data, header=header, overwrite=True)
    return path


def make_all(out_dir: Path | str | None = None) -> dict[str, Path]:
    """(Re)create every fixture into ``out_dir`` (default: this directory)."""
    directory = Path(out_dir) if out_dir is not None else Path(__file__).parent
    return {spec.name: write_fixture(spec, directory) for spec in SPECS}


if __name__ == "__main__":
    for name, path in make_all().items():
        print(f"wrote {name}: {path}")

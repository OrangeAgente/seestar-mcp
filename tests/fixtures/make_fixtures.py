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


# --- hazy (thin cirrus / scattered light) fixture -------------------------
# Same clean round stars as ``good`` (low FWHM, adequate star count, so it would
# PASS the old FWHM/SNR/star-count floors) PLUS the *signature* of a veil:
#   (a) a smooth large-scale background gradient/pedestal across the frame, and
#   (b) a broad, low-amplitude Gaussian HALO around the brightest stars.
# Both are kept below the 5-sigma detection threshold so they do not inflate the
# per-star FWHM/eccentricity or knock down the star count -- they only lift the
# local pedestal (halo ratio) and the large-scale background structure
# (non-uniformity), which is exactly what the scattered_light metric measures.
HAZY_NAME = "hazy"
HAZY_SEED = (BASE_SEED, 4)          # np.random.default_rng([1234, 4])
HAZY_N_STARS = 40
HAZY_SIGMA = 2.0                    # same round core as ``good``
HAZY_AMPLITUDE = 800.0
HAZY_BACKGROUND = 100.0
HAZY_NOISE = 10.0
HAZY_GRADIENT = 45.0               # peak-to-peak large-scale gradient (counts)
HAZY_PEDESTAL = 20.0              # broad smooth central pedestal (counts)
HAZY_HALO_STARS = 5              # add halos around the N brightest stars
HAZY_HALO_AMPLITUDE = 40.0       # halo peak (well below the ~50-count 5-sigma floor)
HAZY_HALO_SIGMA = 10.0           # broad halo (px), several x the stellar core sigma


def render_hazy() -> np.ndarray:
    """Render the ``hazy`` sub: clean stars + gradient/pedestal + bright-star halos."""
    rng = np.random.default_rng(list(HAZY_SEED))
    ny, nx = IMAGE_SHAPE
    img = np.zeros((ny, nx), dtype=np.float64)

    xs = rng.uniform(EDGE_MARGIN, nx - EDGE_MARGIN, HAZY_N_STARS)
    ys = rng.uniform(EDGE_MARGIN, ny - EDGE_MARGIN, HAZY_N_STARS)
    amps = rng.uniform(0.85, 1.0, HAZY_N_STARS) * HAZY_AMPLITUDE
    for x, y, amp in zip(xs, ys, amps):
        model = Gaussian2D(
            amplitude=amp, x_mean=x, y_mean=y,
            x_stddev=HAZY_SIGMA, y_stddev=HAZY_SIGMA,
        )
        y0, y1 = int(y - STAMP_HALF), int(y + STAMP_HALF)
        x0, x1 = int(x - STAMP_HALF), int(x + STAMP_HALF)
        gy, gx = np.mgrid[y0:y1, x0:x1]
        img[y0:y1, x0:x1] += model(gx, gy)

    # (b) Broad low-amplitude halos around the brightest stars (full-frame grid so
    # the halo wings reach into the annulus radius). Below detection threshold.
    bright = np.argsort(amps)[::-1][:HAZY_HALO_STARS]
    fy, fx = np.mgrid[0:ny, 0:nx]
    for idx in bright:
        halo = Gaussian2D(
            amplitude=HAZY_HALO_AMPLITUDE, x_mean=xs[idx], y_mean=ys[idx],
            x_stddev=HAZY_HALO_SIGMA, y_stddev=HAZY_HALO_SIGMA,
        )
        img += halo(fx, fy)

    # (a) Smooth large-scale background: a linear gradient plus a broad central
    # pedestal -- lifts the frame non-uniformly (scattered-light signature).
    gx_norm = fx / (nx - 1)
    gy_norm = fy / (ny - 1)
    gradient = HAZY_GRADIENT * (0.6 * gx_norm + 0.4 * gy_norm)
    pedestal = HAZY_PEDESTAL * np.exp(
        -(((fx - nx / 2) ** 2 + (fy - ny / 2) ** 2) / (2.0 * (0.35 * nx) ** 2))
    )
    img += gradient + pedestal

    img += HAZY_BACKGROUND
    img += rng.normal(0.0, HAZY_NOISE, img.shape)
    return img.astype(np.float32)


def write_hazy(out_dir: Path) -> Path:
    """Render + write ``hazy.fits`` into ``out_dir``; return the path."""
    data = render_hazy()
    header = fits.Header()
    header["OBJECT"] = HAZY_NAME
    header["QAFIX"] = (True, "synthetic QA fixture")
    header["NSTARS"] = (HAZY_N_STARS, "injected star count")
    header["HAZE"] = (True, "gradient + bright-star halos (scattered light)")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{HAZY_NAME}.fits"
    fits.writeto(path, data, header=header, overwrite=True)
    return path


def make_all(out_dir: Path | str | None = None) -> dict[str, Path]:
    """(Re)create every fixture into ``out_dir`` (default: this directory)."""
    directory = Path(out_dir) if out_dir is not None else Path(__file__).parent
    out = {spec.name: write_fixture(spec, directory) for spec in SPECS}
    out[HAZY_NAME] = write_hazy(directory)
    return out


if __name__ == "__main__":
    for name, path in make_all().items():
        print(f"wrote {name}: {path}")

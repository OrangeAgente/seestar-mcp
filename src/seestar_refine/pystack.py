"""Pure-Python stacking backend (DSS-free) for seestar-refine.

Stage 1 of the AstroPipe roadmap (docs/superpowers/specs/2026-07-05-astropipe-
design.md): debayer the raw Seestar Bayer subs, register them with astroalign,
and integrate with a memory-bounded sigma-clipped mean into a ``(3, H, W)``
float32 master — matching ``dss.stack``'s result contract so it is a drop-in
alternative backend.

Every entry point is pure, NaN/inf-safe, and never raises on bad input (the
existing preview/DSS pattern): a bad frame is dropped, not fatal.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.ndimage import convolve

from .dss import _master_stats, _slug
from .keeplist import KeepList
from .models import StackResult

# Rec 601-ish luminance weights for star registration (green dominates).
_LUM_W = np.array([0.2126, 0.7152, 0.0722], dtype="float64")

# Bilinear CFA interpolation kernels (normalized). R and B sit on a quincunx
# grid (every other pixel in both axes); G is a checkerboard at half the pixels.
_K_RB = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], dtype="float64") / 4.0
_K_G = np.array([[0, 1, 0], [1, 4, 1], [0, 1, 0]], dtype="float64") / 4.0


def _cfa_masks(pattern: str, h: int, w: int) -> dict[str, np.ndarray]:
    """Boolean R/G/B site masks for a 2x2 CFA ``pattern`` over an ``h x w`` grid.

    ``pattern`` is the 4-char tile read row-major, e.g. ``"GRBG"`` means
    ``(0,0)=G (0,1)=R (1,0)=B (1,1)=G``. Position index at ``(r,c)`` is
    ``(r%2)*2 + (c%2)``.
    """
    pat = (pattern or "GRBG").upper()
    if len(pat) != 4 or any(ch not in "RGB" for ch in pat):
        pat = "GRBG"
    idx = (np.arange(h)[:, None] % 2) * 2 + (np.arange(w)[None, :] % 2)
    masks: dict[str, np.ndarray] = {}
    for color in "RGB":
        m = np.zeros((h, w), dtype=bool)
        for pos, ch in enumerate(pat):
            if ch == color:
                m |= idx == pos
        masks[color] = m
    return masks


def debayer(raw: np.ndarray, pattern: str = "GRBG") -> np.ndarray:
    """Bilinear-debayer a 2-D CFA frame to ``(H, W, 3)`` float RGB.

    Each color plane is the raw values at that color's sites (0 elsewhere)
    convolved with the standard bilinear CFA kernel, which reconstructs the full
    plane (and reproduces a constant channel exactly in the interior). Non-finite
    input pixels are treated as 0 so they never propagate NaNs through the
    convolution. Never raises: on bad input it returns a best-effort 3-channel
    array (all-zeros if it must).
    """
    try:
        arr = np.asarray(raw, dtype="float64")
        if arr.ndim != 2:
            arr = np.squeeze(arr)
        h, w = arr.shape
        clean = np.where(np.isfinite(arr), arr, 0.0)
        masks = _cfa_masks(pattern, h, w)
        out = np.empty((h, w, 3), dtype="float64")
        out[..., 0] = convolve(clean * masks["R"], _K_RB, mode="mirror")
        out[..., 1] = convolve(clean * masks["G"], _K_G, mode="mirror")
        out[..., 2] = convolve(clean * masks["B"], _K_RB, mode="mirror")
        return out
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        try:
            a = np.asarray(raw, dtype="float64")
            a = np.squeeze(a)
            return np.zeros((a.shape[0], a.shape[1], 3), dtype="float64")
        except Exception:  # noqa: BLE001 - last-resort best effort
            return np.zeros((1, 1, 3), dtype="float64")


def register(
    ref_lum: np.ndarray, frame_rgb: np.ndarray, frame_lum: np.ndarray
) -> np.ndarray | None:
    """Register ``frame_rgb`` onto the reference frame via star matching.

    ``astroalign.find_transform`` solves the affine transform aligning
    ``frame_lum`` to ``ref_lum`` (three-point asterism matching — rotation/scale
    invariant, so it handles alt-az field rotation), then that single transform
    is applied to each RGB channel. Returns the registered ``(H, W, 3)`` array,
    or ``None`` if astroalign cannot solve (too few stars / no match) or is not
    installed. Never raises.
    """
    try:
        import astroalign
    except Exception:  # noqa: BLE001 - backend optional; treat as unsolvable
        return None
    try:
        src = np.asarray(frame_lum, dtype="float32")
        tgt = np.asarray(ref_lum, dtype="float32")
        transform, _ = astroalign.find_transform(src, tgt)
        rgb = np.asarray(frame_rgb, dtype="float32")
        out = np.empty(rgb.shape, dtype="float64")
        for c in range(rgb.shape[-1]):
            reg, _ = astroalign.apply_transform(
                transform, rgb[..., c], tgt, fill_value=0.0
            )
            out[..., c] = reg
        return out
    except Exception:  # noqa: BLE001 - unsolvable frame -> drop it, never fatal
        return None


def _sigma_clip_mean(block: np.ndarray, kappa: float, iters: int) -> np.ndarray:
    """Sigma-clipped mean over axis 0 of ``block`` (``(N, ...)``).

    Iteratively drop values more than ``kappa*std`` from the running mean, then
    average the survivors. Non-finite values are excluded from the start. A
    pixel whose values are all identical keeps them (std 0 -> nothing to reject).
    """
    data = np.asarray(block, dtype="float64")
    mask = np.isfinite(data)
    for _ in range(max(1, int(iters))):
        cnt = mask.sum(axis=0)
        denom = np.maximum(cnt, 1)
        mean = np.where(mask, data, 0.0).sum(axis=0) / denom
        var = np.where(mask, (data - mean) ** 2, 0.0).sum(axis=0) / denom
        std = np.sqrt(var)
        keep = mask & (np.abs(data - mean) <= kappa * std + 1e-9)
        if keep.sum() == mask.sum():
            break
        mask = keep
    cnt = mask.sum(axis=0)
    return np.where(mask, data, 0.0).sum(axis=0) / np.maximum(cnt, 1)


def integrate(
    stack: np.ndarray, *, kappa: float = 2.0, iters: int = 5, block_rows: int = 64
) -> np.ndarray:
    """Sigma-clipped mean across the frame axis of an ``(N, H, W, C)`` stack.

    Computed in row-blocks of ``block_rows`` rows so peak memory stays bounded
    regardless of ``N`` (the input may be a real ``np.memmap`` on disk). Returns
    the ``(H, W, C)`` master. Blocking is exact — any ``block_rows`` gives the
    same result. Never raises.
    """
    try:
        arr = stack
        n = arr.shape[0]
        rest = arr.shape[1:]
        h = rest[0]
        out = np.empty(rest, dtype="float64")
        step = max(1, int(block_rows))
        for r0 in range(0, h, step):
            r1 = min(r0 + step, h)
            out[r0:r1] = _sigma_clip_mean(
                np.asarray(arr[:, r0:r1], dtype="float64"), kappa, iters
            )
        del n
        return out
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        try:
            return np.asarray(stack, dtype="float64").mean(axis=0)
        except Exception:  # noqa: BLE001 - last-resort best effort
            return np.zeros((1, 1, 3), dtype="float64")


def astroalign_available() -> bool:
    """True when the astroalign backend can be imported (mockable in tests)."""
    try:
        import astroalign  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 - any import failure => unavailable
        return False


def _luminance(rgb: np.ndarray) -> np.ndarray:
    """Weighted luminance ``(H, W)`` of an ``(H, W, 3)`` RGB frame."""
    return np.asarray(rgb, dtype="float64") @ _LUM_W


def _load_raw(path: str) -> tuple[np.ndarray | None, str]:
    """Load a raw 2-D Bayer FITS -> ``(array, bayer_pattern)``; ``(None, "")`` on error."""
    try:
        from astropy.io import fits

        with fits.open(path) as hdul:
            for hdu in hdul:
                if getattr(hdu, "data", None) is not None:
                    data = np.asarray(hdu.data)
                    pat = str(hdu.header.get("BAYERPAT", "GRBG")).strip() or "GRBG"
                    return np.squeeze(data), pat
        return None, ""
    except Exception:  # noqa: BLE001 - unreadable sub -> drop it
        return None, ""


def _coverage_crop(
    master: np.ndarray, coverage: np.ndarray, kept: int, coverage_frac: float
) -> tuple[np.ndarray, list[int]]:
    """Black out low-coverage pixels, then trim to the covered bounding box.

    Pixels covered by fewer than ``coverage_frac * kept`` frames are the
    field-rotation border / frame-edge artifacts; they are set to 0 (so an
    aggressive stretch renders them black rather than lifting them into colored
    streaks), and the result is trimmed to the bounding box of the covered
    region. This keeps a large diagonal object (e.g. M31) whole — unlike an
    inscribed-rectangle crop, which would slice its corners. Returns
    ``(cropped_master, [r0, r1, c0, c1])``.
    """
    try:
        thr = max(1, int(np.ceil(float(coverage_frac) * max(1, kept))))
        covered = coverage >= thr
        if not covered.any():
            h, w = coverage.shape
            return master, [0, h, 0, w]
        out = np.where(covered[..., None], master, 0.0)
        rows = np.where(covered.any(axis=1))[0]
        cols = np.where(covered.any(axis=0))[0]
        r0, r1 = int(rows[0]), int(rows[-1]) + 1
        c0, c1 = int(cols[0]), int(cols[-1]) + 1
        return out[r0:r1, c0:c1], [r0, r1, c0, c1]
    except Exception:  # noqa: BLE001 - crop is best-effort; return input
        h, w = coverage.shape
        return master, [0, h, 0, w]


def stack(
    keep_list: KeepList,
    settings,
    *,
    kappa: float = 2.0,
    iters: int = 5,
    pattern: str | None = None,
    master_name: str | None = None,
    coverage_frac: float = 0.5,
) -> StackResult:
    """Stack a keep-list into a ``(3, H, W)`` master with the pure-Python backend.

    Debayers each sub, registers it onto the first sub with astroalign, streams
    the registered frames into a temp ``np.memmap`` (bounded RAM), integrates
    with a sigma-clipped mean, and writes a float32 ``(3, H, W)`` FITS master —
    the same shape/contract as :func:`dss.stack`, returning a
    :class:`StackResult` with ``engine="pystack"``.

    Never raises. Subs that fail to load/debayer/register are dropped and
    counted in ``log``; fewer than 3 registered (or astroalign unavailable) ->
    ``ok=False``.
    """
    target = keep_list.target
    paths = list(keep_list.sub_paths)
    n_subs = len(paths)

    def _fail(msg: str) -> StackResult:
        return StackResult(
            ok=False, engine="pystack", target=target, n_subs=n_subs,
            master_path=None, preview_path=None, stats={}, log="", error=msg,
        )

    if not astroalign_available():
        return _fail("astroalign backend unavailable — pip/uv add astroalign")
    if n_subs < 3:
        return _fail(f"need >=3 subs to stack, got {n_subs}")

    tmp = None
    try:
        from astropy.io import fits

        output_dir = Path(settings.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        master_name = master_name or f"{_slug(target)}_master.fit"

        ref_raw, ref_pat = _load_raw(paths[0])
        if ref_raw is None:
            return _fail("reference sub could not be read")
        pat = pattern or ref_pat
        ref_rgb = debayer(ref_raw, pat)
        ref_lum = _luminance(ref_rgb)
        h, w = ref_lum.shape

        tmp = output_dir / f".{_slug(target)}_reg.dat"
        mm = np.memmap(tmp, dtype="float32", mode="w+", shape=(n_subs, h, w, 3))
        mm[0] = ref_rgb.astype("float32")  # reference is aligned to itself
        # Per-pixel coverage: the reference covers everything; each warped frame
        # covers only its footprint (astroalign 0-fills outside it).
        coverage = np.ones((h, w), dtype="int32")
        kept, dropped = 1, 0

        for p in paths[1:]:
            raw, _ = _load_raw(p)
            if raw is None or raw.shape != (h, w):
                dropped += 1
                continue
            rgb = debayer(raw, pat)
            reg = register(ref_lum, rgb, _luminance(rgb))
            if reg is None:
                dropped += 1
                continue
            mm[kept] = reg.astype("float32")
            coverage += (_luminance(reg) > 1e-6).astype("int32")
            kept += 1

        if kept < 3:
            return _fail(f"only {kept}/{n_subs} subs registered (need >=3)")

        master = integrate(mm[:kept], kappa=kappa, iters=iters)  # (H, W, 3)

        # Persist the coverage map (frames-per-pixel) so the crop threshold can be
        # re-tuned later without re-registering.
        try:
            np.save(output_dir / f"{_slug(target)}_coverage.npy", coverage)
        except Exception:  # noqa: BLE001 - sidecar is best-effort
            pass

        # Coverage crop: black out the low-coverage field-rotation border/edge
        # artifacts and trim to the covered bounding box (keeps large diagonal
        # objects whole; the DSS "intersection mode" idea, artifact-safe).
        master, cov_bbox = _coverage_crop(master, coverage, kept, coverage_frac)

        cube = np.transpose(master, (2, 0, 1)).astype("float32")  # (3, H, W)
        master_path = output_dir / master_name
        fits.writeto(master_path, cube, overwrite=True)
        mm.flush()
        del mm

        return StackResult(
            ok=True, engine="pystack", target=target, n_subs=n_subs,
            master_path=str(master_path), preview_path=None,
            stats=_master_stats(str(master_path)),
            log=f"registered {kept}/{n_subs} (dropped {dropped}), "
                f"kappa={kappa} iters={iters} pattern={pat} "
                f"intersection_crop={cov_bbox} cover>={coverage_frac}",
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - never raise; report structured error
        return _fail(f"{type(exc).__name__}: {exc}")
    finally:
        try:
            if tmp is not None and tmp.exists():
                tmp.unlink()
        except Exception:  # noqa: BLE001 - temp cleanup is best-effort
            pass

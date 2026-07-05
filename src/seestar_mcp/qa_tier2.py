"""Tier-2 photutils FITS quality analysis for the ZWO Seestar S50.

Tier-2 is the AUTHORITATIVE, and ONLY, layer allowed to call a sub good or bad.
It owns its own view of the pixels: it loads each RAW sub, measures per-star
metrics with photutils *segmentation* (FWHM, HFR, eccentricity, an SNR proxy,
background, star count), aggregates to per-sub medians, and scores each sub
PASS / MARGINAL / REJECT against **session-relative** thresholds (computed from
the session's own median/sigma), with optional absolute overrides from config.

Auditability contract (mirrors the qa-policy skill):
- "No unexplained rejects." Every non-PASS verdict carries a human-readable
  reason naming the metric + threshold + measured value, e.g.
  ``"REJECT: eccentricity 0.61 >= 0.575 cutoff"``.
- Session-relative by default; ``qa_*_absolute`` in Settings overrides.
- A sub that cannot be analyzed is REJECTed with reason "could not analyze" and
  excluded from the session medians (a bad sub never raises).
- Session quality scalar is the Siril-style ``wFWHM`` = FWHM weighted by star
  count, the single best transparency-aware quality number.

Metric provenance:
- FWHM / eccentricity come from second-moment ellipse fits
  (``SourceCatalog.fwhm`` / ``.eccentricity``); eccentricity ``sqrt(1-(b/a)^2)``
  is exactly the PixInsight-style roundness measure (0 = round).
- HFR is ``SourceCatalog.fluxfrac_radius(0.5)`` (half-flux radius, pixels).
- SNR is ``segment_flux / segment_fluxerr`` with a flat per-pixel error map set
  to the sigma-clipped sky noise — a documented, monotonic per-star SNR proxy
  (brighter star -> higher SNR) that falls with clouds / light pollution.
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from astropy.convolution import convolve
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from photutils.segmentation import (
    SourceCatalog,
    detect_sources,
    make_2dgaussian_kernel,
)

if TYPE_CHECKING:
    from .config import Settings
    from .provenance import ProvenanceLog, SessionManifest

# --- detection parameters (documented module constants) -------------------
# Tuned against the committed synthetic fixtures and typical Seestar subs. These
# govern source detection only; the QA *verdict* thresholds live in Settings.
DETECT_NSIGMA = 5.0        # detection threshold = median + NSIGMA * sky_sigma
DETECT_NPIXELS = 5         # minimum connected pixels above threshold for a source
KERNEL_FWHM = 3.0          # FWHM (px) of the smoothing kernel used before detection
KERNEL_SIZE = 5            # kernel support (px); odd, >= a few * KERNEL_FWHM/2
SIGMA_CLIP = 3.0           # sigma for the sigma-clipped background statistics

# Reason category labels (used for dominant_reject_cause aggregation).
CAUSE_FWHM = "fwhm"
CAUSE_ECC = "eccentricity"
CAUSE_SNR = "snr"
CAUSE_STARS = "star_count"
CAUSE_ERROR = "error"


# --- data model -----------------------------------------------------------


@dataclass
class SubMetrics:
    """Per-sub aggregated metrics (medians across detected stars)."""

    name: str
    star_count: int
    fwhm: float | None          # median FWHM across stars (pixels)
    hfr: float | None           # median half-flux radius (pixels)
    eccentricity: float | None  # median eccentricity (0 = round)
    snr: float | None           # median per-star SNR proxy
    background: float | None    # sigma-clipped median sky level
    error: str | None = None    # set if the sub could not be analyzed


@dataclass
class SubVerdict:
    """A scored sub: verdict + the reasons that justify it + its metrics."""

    name: str
    verdict: str                # "PASS" | "MARGINAL" | "REJECT"
    reasons: list[str]
    metrics: SubMetrics


@dataclass
class SessionReport:
    """Aggregate result of scoring a whole session of subs."""

    target: str | None
    subs: list[SubVerdict]
    keep_list: list[str]        # names of subs with verdict != REJECT
    medians: dict               # session medians used (fwhm, snr, star_count, ...)
    wfwhm: float | None         # star-count-weighted mean FWHM across subs
    dominant_reject_cause: str | None
    total: int
    kept: int


# --- per-sub analysis -----------------------------------------------------


def _load_image(path: Path) -> np.ndarray:
    """Return the first 2-D image array from a FITS file (float64).

    Raises on load failure / no 2-D HDU; callers wrap this and tag the error.
    """
    with fits.open(path) as hdul:
        for hdu in hdul:
            data = getattr(hdu, "data", None)
            if data is not None and getattr(data, "ndim", 0) == 2:
                return np.asarray(data, dtype=np.float64)
    raise ValueError("no 2-D image HDU found")


def _median(values: np.ndarray) -> float | None:
    """NaN-safe median of a 1-D array, or None if empty / all-NaN."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0 or np.all(np.isnan(arr)):
        return None
    return float(np.nanmedian(arr))


def analyze_sub(path: str | Path, *, name: str | None = None) -> SubMetrics:
    """Analyze one FITS sub into a :class:`SubMetrics`.

    Never raises for a bad sub: on load failure or zero detected stars, returns
    an error-tagged ``SubMetrics(star_count=0, ...=None, error=...)``.
    """
    path = Path(path)
    sub_name = name if name is not None else path.stem

    try:
        data = _load_image(path)
    except (FileNotFoundError, OSError, ValueError) as exc:
        return SubMetrics(
            name=sub_name, star_count=0, fwhm=None, hfr=None,
            eccentricity=None, snr=None, background=None,
            error=f"could not load: {exc}",
        )

    try:
        _, median, std = sigma_clipped_stats(data, sigma=SIGMA_CLIP)
        median = float(median)
        std = float(std)
        if not np.isfinite(std) or std <= 0:
            std = float(np.nanstd(data)) or 1.0

        data_sub = data - median
        kernel = make_2dgaussian_kernel(KERNEL_FWHM, size=KERNEL_SIZE)
        convolved = convolve(data_sub, kernel)
        segm = detect_sources(
            convolved, threshold=DETECT_NSIGMA * std, npixels=DETECT_NPIXELS
        )
        if segm is None or segm.nlabels == 0:
            return SubMetrics(
                name=sub_name, star_count=0, fwhm=None, hfr=None,
                eccentricity=None, snr=None, background=median,
                error="no stars detected",
            )

        error_map = np.full_like(data, std)
        cat = SourceCatalog(
            data_sub, segm, convolved_data=convolved, error=error_map
        )

        fwhm = np.asarray(cat.fwhm, dtype=float)
        ecc = np.asarray(cat.eccentricity, dtype=float)
        hfr = np.asarray(cat.fluxfrac_radius(0.5), dtype=float)
        seg_flux = np.asarray(cat.segment_flux, dtype=float)
        seg_err = np.asarray(cat.segment_fluxerr, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            snr = np.where(seg_err > 0, seg_flux / seg_err, np.nan)

        return SubMetrics(
            name=sub_name,
            star_count=int(segm.nlabels),
            fwhm=_median(fwhm),
            hfr=_median(hfr),
            eccentricity=_median(ecc),
            snr=_median(snr),
            background=median,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - never let a bad sub crash the run
        return SubMetrics(
            name=sub_name, star_count=0, fwhm=None, hfr=None,
            eccentricity=None, snr=None, background=None,
            error=f"analysis failed: {exc}",
        )


# --- session classification -----------------------------------------------


def _collect(values: list[float | None]) -> np.ndarray:
    """Return finite float values from a list, dropping None / NaN."""
    arr = np.array([v for v in values if v is not None], dtype=float)
    return arr[np.isfinite(arr)]


def _score_sub(
    m: SubMetrics,
    settings: Settings,
    *,
    fwhm_median: float | None,
    fwhm_sigma: float | None,
    snr_median: float | None,
    starcount_median: float | None,
) -> tuple[SubVerdict, list[str]]:
    """Apply the verdict rules to one sub, building reasons + a verdict.

    Returns the ``SubVerdict`` and the list of REJECT cause categories (for
    dominant-cause aggregation). Any single REJECT trigger => REJECT; else any
    MARGINAL => MARGINAL; else PASS. Every non-PASS reason names the metric +
    threshold + measured value.
    """
    reasons: list[str] = []
    reject_causes: list[str] = []
    marginal = False

    if m.error is not None:
        verdict = SubVerdict(m.name, "REJECT", [f"could not analyze: {m.error}"], m)
        return verdict, [CAUSE_ERROR]

    # --- Eccentricity: absolute override, else canonical 0.575 cutoff ---
    ecc_cut = (
        settings.qa_eccentricity_absolute
        if settings.qa_eccentricity_absolute is not None
        else settings.qa_eccentricity_reject
    )
    if m.eccentricity is not None:
        if m.eccentricity >= ecc_cut:
            reasons.append(
                f"REJECT: eccentricity {m.eccentricity:.2f} >= {ecc_cut:g} cutoff"
            )
            reject_causes.append(CAUSE_ECC)
        elif m.eccentricity >= settings.qa_eccentricity_marginal:
            reasons.append(
                f"MARGINAL: eccentricity {m.eccentricity:.2f} "
                f">= {settings.qa_eccentricity_marginal:g} marginal"
            )
            marginal = True

    # --- FWHM: absolute override, else session median + sigma*std ---
    if m.fwhm is not None:
        if settings.qa_fwhm_absolute is not None:
            if m.fwhm > settings.qa_fwhm_absolute:
                reasons.append(
                    f"REJECT: FWHM {m.fwhm:.2f} > {settings.qa_fwhm_absolute:.2f} "
                    "absolute limit"
                )
                reject_causes.append(CAUSE_FWHM)
        elif fwhm_median is not None and fwhm_sigma is not None:
            reject_thr = fwhm_median + settings.qa_fwhm_sigma * fwhm_sigma
            marginal_thr = fwhm_median + settings.qa_fwhm_marginal_sigma * fwhm_sigma
            if m.fwhm > reject_thr:
                reasons.append(
                    f"REJECT: FWHM {m.fwhm:.2f} > {reject_thr:.2f} "
                    f"(median {fwhm_median:.2f} + {settings.qa_fwhm_sigma:g}sigma)"
                )
                reject_causes.append(CAUSE_FWHM)
            elif m.fwhm > marginal_thr:
                reasons.append(
                    f"MARGINAL: FWHM {m.fwhm:.2f} > {marginal_thr:.2f} "
                    f"(median {fwhm_median:.2f} + "
                    f"{settings.qa_fwhm_marginal_sigma:g}sigma)"
                )
                marginal = True

    # --- SNR: session floor ---
    if m.snr is not None and snr_median is not None:
        snr_floor = snr_median * settings.qa_snr_floor_factor
        if m.snr < snr_floor:
            reasons.append(
                f"REJECT: SNR {m.snr:.2f} < {snr_floor:.2f} floor "
                f"({settings.qa_snr_floor_factor:g} x median {snr_median:.2f})"
            )
            reject_causes.append(CAUSE_SNR)

    # --- Star count: session floor ---
    if starcount_median is not None:
        star_floor = starcount_median * settings.qa_starcount_floor_factor
        if m.star_count < star_floor:
            reasons.append(
                f"REJECT: star_count {m.star_count} < {star_floor:.3g} floor "
                f"({settings.qa_starcount_floor_factor:g} x median "
                f"{starcount_median:.3g})"
            )
            reject_causes.append(CAUSE_STARS)

    if reject_causes:
        verdict = "REJECT"
    elif marginal:
        verdict = "MARGINAL"
    else:
        verdict = "PASS"
        reasons.append("PASS: all metrics within session norms")

    return SubVerdict(m.name, verdict, reasons, m), reject_causes


def classify(metrics: list[SubMetrics], settings: Settings) -> SessionReport:
    """Score a list of :class:`SubMetrics` into a :class:`SessionReport`.

    Session medians/sigma are computed over successfully-analyzed subs only;
    error subs are still emitted as REJECT verdicts but excluded from medians.
    """
    good = [m for m in metrics if m.error is None]

    fwhm_vals = _collect([m.fwhm for m in good])
    snr_vals = _collect([m.snr for m in good])
    star_vals = _collect([float(m.star_count) for m in good])
    hfr_vals = _collect([m.hfr for m in good])
    ecc_vals = _collect([m.eccentricity for m in good])

    fwhm_median = float(np.median(fwhm_vals)) if fwhm_vals.size else None
    # Population std (ddof=0); with a single sub sigma is 0 -> only absolute /
    # eccentricity / floor rules can fire, which is the safe conservative default.
    fwhm_sigma = float(np.std(fwhm_vals)) if fwhm_vals.size else None
    snr_median = float(np.median(snr_vals)) if snr_vals.size else None
    star_median = float(np.median(star_vals)) if star_vals.size else None

    medians = {
        "fwhm": fwhm_median,
        "fwhm_sigma": fwhm_sigma,
        "snr": snr_median,
        "star_count": star_median,
        "hfr": float(np.median(hfr_vals)) if hfr_vals.size else None,
        "eccentricity": float(np.median(ecc_vals)) if ecc_vals.size else None,
        "n_analyzed": len(good),
    }

    scored = [
        _score_sub(
            m, settings,
            fwhm_median=fwhm_median, fwhm_sigma=fwhm_sigma,
            snr_median=snr_median, starcount_median=star_median,
        )
        for m in metrics
    ]
    subs = [v for v, _causes in scored]

    keep_list = [v.name for v in subs if v.verdict != "REJECT"]

    # wFWHM = sum(fwhm_i * n_i) / sum(n_i) over successful subs.
    weight = sum(m.star_count for m in good if m.fwhm is not None)
    if weight > 0:
        weighted = sum(
            m.fwhm * m.star_count for m in good if m.fwhm is not None
        )
        wfwhm: float | None = weighted / weight
    else:
        wfwhm = None

    # Dominant reject cause = most common reject category across rejected subs.
    cause_counter: Counter[str] = Counter()
    for v, causes in scored:
        if v.verdict == "REJECT":
            cause_counter.update(causes or [CAUSE_ERROR])
    dominant = cause_counter.most_common(1)[0][0] if cause_counter else None

    return SessionReport(
        target=None,
        subs=subs,
        keep_list=keep_list,
        medians=medians,
        wfwhm=wfwhm,
        dominant_reject_cause=dominant,
        total=len(subs),
        kept=len(keep_list),
    )


# --- orchestration --------------------------------------------------------


def analyze_session(
    paths: list[str | Path],
    settings: Settings,
    *,
    target: str | None = None,
    provenance: ProvenanceLog | None = None,
    manifest: SessionManifest | None = None,
) -> SessionReport:
    """Analyze + classify every sub in ``paths``, wiring provenance + manifest.

    Returns a :class:`SessionReport`. If ``provenance`` is given, appends one
    ``qa_tier2.analyze_session`` audit record (counts + medians). If ``manifest``
    is given, records every sub verdict, the keep-list, and wFWHM/medians meta.
    """
    metrics = [analyze_sub(p) for p in paths]
    report = classify(metrics, settings)
    report.target = target

    if manifest is not None:
        for v in report.subs:
            manifest.add_verdict(
                v.name, v.verdict,
                {
                    "reasons": v.reasons,
                    **dataclasses.asdict(v.metrics),
                },
            )
        manifest.set_keep_list(report.keep_list)
        manifest.set_meta(wfwhm=report.wfwhm, medians=report.medians,
                          dominant_reject_cause=report.dominant_reject_cause)

    if provenance is not None:
        provenance.log_call(
            tool="qa_tier2.analyze_session",
            args={
                "target": target,
                "total": report.total,
                "kept": report.kept,
                "keep_list": report.keep_list,
                "wfwhm": report.wfwhm,
                "medians": report.medians,
                "dominant_reject_cause": report.dominant_reject_cause,
            },
        )

    return report


# --- rendering ------------------------------------------------------------


def render_json(report: SessionReport) -> str:
    """Return the full structured report as pretty JSON."""
    return json.dumps(dataclasses.asdict(report), indent=2, sort_keys=False)


def _fmt(value: float | None, spec: str = ".3g") -> str:
    """Format an optional float for a Markdown cell."""
    return "-" if value is None else format(value, spec)


def render_markdown(report: SessionReport) -> str:
    """Render a Markdown report: headline first, then a per-sub table.

    The headline is the auditable summary: kept N of M, median wFWHM, and the
    dominant reject cause. The table gives every metric + verdict per sub.
    """
    wfwhm = _fmt(report.wfwhm)
    cause = report.dominant_reject_cause or "none"
    target = report.target or "(unknown)"

    lines: list[str] = []
    lines.append(f"# Tier-2 QA report - {target}")
    lines.append("")
    lines.append(
        f"**Kept {report.kept} of {report.total}** | "
        f"median wFWHM {wfwhm} px | dominant reject cause: {cause}"
    )
    lines.append("")
    lines.append(f"Keep list: {', '.join(report.keep_list) or '(none)'}")
    lines.append("")
    lines.append(
        "| Sub | Verdict | Stars | FWHM | HFR | Ecc | SNR | Bkg | Reasons |"
    )
    lines.append(
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
    )
    for v in report.subs:
        m = v.metrics
        reasons = "; ".join(v.reasons).replace("|", "/")
        lines.append(
            f"| {v.name} | {v.verdict} | {m.star_count} | "
            f"{_fmt(m.fwhm)} | {_fmt(m.hfr)} | {_fmt(m.eccentricity)} | "
            f"{_fmt(m.snr, '.4g')} | {_fmt(m.background, '.4g')} | {reasons} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_report(
    report: SessionReport, out_dir: str | Path, *, stem: str = "qa_report"
) -> tuple[Path, Path]:
    """Write ``<stem>.md`` and ``<stem>.json`` into ``out_dir``; return paths."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    md_path = directory / f"{stem}.md"
    json_path = directory / f"{stem}.json"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(render_json(report), encoding="utf-8")
    return md_path, json_path

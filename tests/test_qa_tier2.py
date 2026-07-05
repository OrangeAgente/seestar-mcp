"""Tests for seestar_mcp.qa_tier2 (Tier-2 photutils FITS QA).

Tier-2 is the ONLY layer allowed to call data good/bad. These tests split into
two groups:

- FAST/PURE: ``classify`` + rendering are exercised with hand-built ``SubMetrics``
  (no FITS, no photutils) so verdict logic and reason strings are deterministic.
- FIXTURE: three ``analyze_sub`` / ``analyze_session`` tests touch photutils on the
  committed synthetic FITS fixtures (regenerated on demand by ``make_fixtures``).

Every REJECT/MARGINAL verdict must carry a human-readable reason naming the
metric + threshold + value; the reason assertions enforce that contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from seestar_mcp.config import Settings
from seestar_mcp.provenance import ProvenanceLog, SessionManifest
from seestar_mcp.qa_tier2 import (
    SessionReport,
    SubMetrics,
    SubVerdict,
    analyze_session,
    analyze_sub,
    classify,
    render_json,
    render_markdown,
    write_report,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIXTURE_DIR))


@pytest.fixture(scope="session", autouse=True)
def _ensure_fixtures():
    """(Re)create the FITS fixtures if any are missing."""
    from make_fixtures import HAZY_NAME, SPECS, make_all

    names = [s.name for s in SPECS] + [HAZY_NAME]
    missing = any(not (FIXTURE_DIR / f"{n}.fits").exists() for n in names)
    if missing:
        make_all(FIXTURE_DIR)
    yield


def _settings(**overrides) -> Settings:
    """Build Settings with defaults, applying any field overrides."""
    return Settings(**overrides)


def _strict_loads(js: str):
    """Parse JSON, raising if it contains any NaN/Infinity token (strict RFC-8259)."""

    def _reject(token: str):
        raise ValueError(f"non-strict JSON constant: {token}")

    return json.loads(js, parse_constant=_reject)


# --- analyze_sub on real fixtures (photutils) -----------------------------


def test_analyze_sub_good_fixture():
    m = analyze_sub(FIXTURE_DIR / "good.fits")
    assert m.error is None
    # ~40 injected round stars; allow a detection tolerance band.
    assert 30 <= m.star_count <= 50
    assert m.eccentricity is not None and m.eccentricity < 0.42
    assert m.fwhm is not None and 3.0 <= m.fwhm <= 7.0
    assert m.hfr is not None and m.hfr > 0
    assert m.snr is not None and m.snr > 0
    assert m.background is not None


def test_analyze_sub_bad_ecc_fixture():
    m = analyze_sub(FIXTURE_DIR / "bad_ecc.fits")
    assert m.error is None
    assert m.star_count > 0
    assert m.eccentricity is not None and m.eccentricity >= 0.575


def test_analyze_sub_garbage_file_returns_error_no_raise(tmp_path):
    # Nonexistent path.
    missing = analyze_sub(tmp_path / "does_not_exist.fits")
    assert missing.error is not None
    assert missing.star_count == 0
    assert missing.fwhm is None

    # Garbage (non-FITS) content.
    junk = tmp_path / "junk.fits"
    junk.write_bytes(b"not a fits file at all\x00\x01\x02")
    bad = analyze_sub(junk)
    assert bad.error is not None
    assert bad.star_count == 0


# --- scattered_light metric (photutils on fixtures) -----------------------


def test_scattered_light_low_on_good_fixture():
    m = analyze_sub(FIXTURE_DIR / "good.fits")
    # good.fits is a flat dark sky: measured ~0.0006, pinned well below hazy (~0.053).
    assert m.scattered_light is not None and m.scattered_light < 0.02


def test_scattered_light_high_on_hazy_fixture():
    good = analyze_sub(FIXTURE_DIR / "good.fits")
    hazy = analyze_sub(FIXTURE_DIR / "hazy.fits")
    assert hazy.scattered_light is not None and good.scattered_light is not None
    assert hazy.scattered_light > good.scattered_light * 1.5   # clearly elevated
    # ...while still PASSING the OLD floors (the whole point of the metric).
    assert hazy.star_count >= 20 and (hazy.fwhm or 99) < 7


def test_scattered_light_none_on_garbage(tmp_path):
    assert analyze_sub(tmp_path / "nope.fits").scattered_light is None  # no raise


# --- classify (pure, hand-built metrics) ----------------------------------


def _clean(name: str, **kw) -> SubMetrics:
    base = dict(star_count=40, fwhm=4.5, hfr=2.3, eccentricity=0.20,
                snr=200.0, background=100.0)
    base.update(kw)
    return SubMetrics(name=name, **base)


def test_classify_pass_and_each_reject_rule():
    settings = _settings()
    metrics = [
        _clean("pass"),
        _clean("hi_ecc", eccentricity=0.61),          # REJECT ecc >= 0.575
        _clean("hi_fwhm", fwhm=9.0),                   # REJECT fwhm via session sigma
        _clean("lo_snr", snr=10.0),                    # REJECT snr < median*0.5
        _clean("lo_stars", star_count=5),              # REJECT star_count floor
        _clean("marginal_ecc", eccentricity=0.50),     # MARGINAL 0.42<=ecc<0.575
    ]
    report = classify(metrics, settings)
    verdicts = {v.name: v for v in report.subs}

    assert verdicts["pass"].verdict == "PASS"

    # High eccentricity REJECT names the 0.575 cutoff and the value.
    ecc_v = verdicts["hi_ecc"]
    assert ecc_v.verdict == "REJECT"
    joined = " ".join(ecc_v.reasons).lower()
    assert "eccentric" in joined and "0.575" in joined and "0.61" in joined

    # High FWHM REJECT via session sigma names FWHM + threshold + value.
    fwhm_v = verdicts["hi_fwhm"]
    assert fwhm_v.verdict == "REJECT"
    assert any("fwhm" in r.lower() and "9.00" in r for r in fwhm_v.reasons)

    # Low SNR REJECT.
    snr_v = verdicts["lo_snr"]
    assert snr_v.verdict == "REJECT"
    assert any("snr" in r.lower() for r in snr_v.reasons)

    # Low star count REJECT.
    star_v = verdicts["lo_stars"]
    assert star_v.verdict == "REJECT"
    assert any("star" in r.lower() and "count" in r.lower() for r in star_v.reasons)

    # Marginal eccentricity.
    marg_v = verdicts["marginal_ecc"]
    assert marg_v.verdict == "MARGINAL"
    assert any("0.42" in r for r in marg_v.reasons)

    # keep_list excludes every REJECT, includes PASS and MARGINAL.
    assert "pass" in report.keep_list
    assert "marginal_ecc" in report.keep_list
    for rej in ("hi_ecc", "hi_fwhm", "lo_snr", "lo_stars"):
        assert rej not in report.keep_list
    assert report.total == 6
    assert report.kept == len(report.keep_list)
    assert report.dominant_reject_cause is not None


def test_classify_error_sub_is_rejected():
    settings = _settings()
    err = SubMetrics(name="broken", star_count=0, fwhm=None, hfr=None,
                     eccentricity=None, snr=None, background=None,
                     error="could not load")
    report = classify([_clean("ok"), err], settings)
    verdicts = {v.name: v for v in report.subs}
    assert verdicts["broken"].verdict == "REJECT"
    assert any("could not analyze" in r.lower() for r in verdicts["broken"].reasons)
    assert "broken" not in report.keep_list


def test_classify_reject_wins_over_marginal_and_keeps_both_reasons():
    """A sub with a MARGINAL trigger on one metric and a REJECT trigger on another
    ends REJECT, and BOTH reason strings are retained (audit completeness)."""
    settings = _settings()
    metrics = [
        _clean("a"),
        _clean("b"),
        # ecc 0.50 => MARGINAL; snr 10 vs session median 200 => REJECT (< 100 floor).
        _clean("mixed", eccentricity=0.50, snr=10.0),
    ]
    report = classify(metrics, settings)
    mixed = {v.name: v for v in report.subs}["mixed"]

    assert mixed.verdict == "REJECT"
    joined = " ".join(mixed.reasons)
    lower = joined.lower()
    # The MARGINAL eccentricity reason is retained alongside the SNR REJECT.
    assert "marginal" in lower and "eccentric" in lower
    assert "reject" in lower and "snr" in lower
    assert "mixed" not in report.keep_list


def test_classify_single_sub_sigma_zero_not_fwhm_rejected_against_itself():
    """With one successful sub, FWHM sigma is 0, so its FWHM equals the session
    median+0*sigma threshold and must NOT reject itself."""
    settings = _settings()
    report = classify([_clean("solo", fwhm=4.5)], settings)
    solo = {v.name: v for v in report.subs}["solo"]

    assert solo.verdict == "PASS"
    assert not any("fwhm" in r.lower() for r in solo.reasons)
    assert "solo" in report.keep_list


def test_classify_absolute_overrides_take_precedence():
    # Absolute ecc override tighter than session-relative: 0.50 now REJECTs.
    settings = _settings(qa_eccentricity_absolute=0.45)
    report = classify([_clean("a"), _clean("b", eccentricity=0.50)], settings)
    v = {x.name: x for x in report.subs}["b"]
    assert v.verdict == "REJECT"
    assert any("0.45" in r for r in v.reasons)

    # Absolute FWHM override: fwhm 6.0 exceeds absolute 5.0 -> REJECT even though
    # it would not exceed the session median+sigma here.
    settings2 = _settings(qa_fwhm_absolute=5.0)
    report2 = classify([_clean("c"), _clean("d", fwhm=6.0)], settings2)
    v2 = {x.name: x for x in report2.subs}["d"]
    assert v2.verdict == "REJECT"
    assert any("fwhm" in r.lower() and "5.0" in r for r in v2.reasons)


def test_classify_rejects_high_scatter():
    """A session of clean subs (low scattered_light) + one much higher outlier:
    the outlier is REJECT with a reason naming scattered light + threshold + value."""
    settings = _settings()
    metrics = [
        _clean("a", scattered_light=0.01),
        _clean("b", scattered_light=0.01),
        _clean("c", scattered_light=0.01),
        _clean("d", scattered_light=0.01),
        _clean("hazy", scattered_light=0.9),  # session outlier
    ]
    report = classify(metrics, settings)
    verdicts = {v.name: v for v in report.subs}

    hazy_v = verdicts["hazy"]
    assert hazy_v.verdict == "REJECT"
    reason = " ".join(hazy_v.reasons).lower()
    assert "scattered light" in reason and "0.900" in reason
    assert "hazy" not in report.keep_list

    # The clean subs stay PASS with no scatter reason.
    for name in ("a", "b", "c", "d"):
        assert verdicts[name].verdict == "PASS"
        assert not any("scattered" in r.lower() for r in verdicts[name].reasons)


def test_classify_scatter_none_is_noop():
    """Every scattered_light is None -> verdicts identical to the pre-feature path:
    no scatter reasons, and the (otherwise clean) subs all PASS."""
    settings = _settings()
    metrics = [
        _clean("a"),  # scattered_light defaults to None
        _clean("b"),
        _clean("c", scattered_light=None),
    ]
    report = classify(metrics, settings)
    verdicts = {v.name: v for v in report.subs}

    for name in ("a", "b", "c"):
        assert verdicts[name].verdict == "PASS"
        assert not any("scattered" in r.lower() for r in verdicts[name].reasons)
    assert set(report.keep_list) == {"a", "b", "c"}


def test_scatter_absolute_override():
    """An absolute scatter limit REJECTs a sub above it regardless of session median."""
    # All subs sit near 0.2 (so no session-relative reject would fire), but the
    # absolute limit is 0.1 -> every sub above it REJECTs.
    settings = _settings(qa_scatter_absolute=0.1)
    metrics = [
        _clean("a", scattered_light=0.2),
        _clean("b", scattered_light=0.2),
        _clean("c", scattered_light=0.2),
    ]
    report = classify(metrics, settings)
    v = {x.name: x for x in report.subs}["a"]
    assert v.verdict == "REJECT"
    assert any(
        "scattered light" in r.lower() and "absolute" in r.lower() for r in v.reasons
    )


def test_classify_wfwhm_weighted_formula():
    settings = _settings()
    metrics = [
        _clean("a", fwhm=4.0, star_count=100),
        _clean("b", fwhm=6.0, star_count=50),
    ]
    report = classify(metrics, settings)
    # wFWHM = (4*100 + 6*50) / (100+50) = 700/150
    assert report.wfwhm == pytest.approx(700.0 / 150.0)


# --- analyze_session end-to-end (photutils + provenance + manifest) -------


def test_analyze_session_end_to_end(tmp_path):
    paths = [FIXTURE_DIR / "good.fits",
             FIXTURE_DIR / "bad_ecc.fits",
             FIXTURE_DIR / "bad_snr.fits"]
    settings = _settings()
    prov_path = tmp_path / "provenance.jsonl"
    provenance = ProvenanceLog(prov_path)
    manifest = SessionManifest("sess-1", tmp_path / "manifests", target="M42")

    report = analyze_session(
        paths, settings, target="M42",
        provenance=provenance, manifest=manifest,
    )
    verdicts = {v.name: v for v in report.subs}

    assert verdicts["good"].verdict == "PASS"

    assert verdicts["bad_ecc"].verdict == "REJECT"
    assert any("eccentric" in r.lower() for r in verdicts["bad_ecc"].reasons)

    assert verdicts["bad_snr"].verdict == "REJECT"
    snr_reasons = " ".join(verdicts["bad_snr"].reasons).lower()
    assert "snr" in snr_reasons and "star" in snr_reasons

    assert "good" in report.keep_list
    assert "bad_ecc" not in report.keep_list
    assert "bad_snr" not in report.keep_list
    assert report.wfwhm is not None and report.wfwhm > 0

    # Manifest write contains all three verdicts + keep_list.
    manifest_path = manifest.write()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(data["verdicts"]) == {"good", "bad_ecc", "bad_snr"}
    assert data["keep_list"] == ["good"]
    assert "wfwhm" in data["meta"]

    # Provenance: at least one record written for the session analysis.
    lines = [line for line in prov_path.read_text(encoding="utf-8").splitlines() if line]
    assert lines
    records = [json.loads(line) for line in lines]
    assert any(r["tool"] == "qa_tier2.analyze_session" for r in records)


def test_render_json_on_real_fixtures_is_strict_valid():
    """render_json over real analyzer output must parse under STRICT JSON.

    Pins both the Quantity-leak guard (astropy Quantities can't serialize) and
    the NaN-token guard: a pathological sub could leave a non-finite metric that
    json.dumps would otherwise emit as a bare ``NaN``. Uses a parse_constant that
    raises on any NaN/Infinity token so a regression fails loudly.
    """
    paths = [FIXTURE_DIR / "good.fits",
             FIXTURE_DIR / "bad_ecc.fits",
             FIXTURE_DIR / "bad_snr.fits"]
    report = analyze_session(paths, _settings(), target="M42")
    js = render_json(report)
    data = _strict_loads(js)  # raises on NaN/Infinity or invalid JSON
    assert data["target"] == "M42"
    assert len(data["subs"]) == 3


# --- rendering & report writing -------------------------------------------


def _sample_report() -> SessionReport:
    subs = [
        SubVerdict("good", "PASS", ["all metrics within session norms"],
                   _clean("good")),
        SubVerdict("bad", "REJECT", ["REJECT: eccentricity 0.61 >= 0.575 cutoff"],
                   _clean("bad", eccentricity=0.61)),
    ]
    return SessionReport(
        target="M42", subs=subs, keep_list=["good"],
        medians={"fwhm": 4.5, "snr": 200.0, "star_count": 40},
        wfwhm=4.6, dominant_reject_cause="eccentricity", total=2, kept=1,
    )


def test_render_markdown_has_headline_and_table():
    md = render_markdown(_sample_report())
    assert "Kept 1 of 2" in md
    assert "wFWHM" in md or "wfwhm" in md.lower()
    assert "eccentricity" in md.lower()
    # A per-sub table row for each sub.
    assert "good" in md and "bad" in md


def test_render_json_round_trips():
    js = render_json(_sample_report())
    data = json.loads(js)
    assert data["target"] == "M42"
    assert data["keep_list"] == ["good"]
    assert data["dominant_reject_cause"] == "eccentricity"
    assert len(data["subs"]) == 2


def test_write_report_writes_both_files(tmp_path):
    md_path, json_path = write_report(_sample_report(), tmp_path)
    assert md_path.exists() and json_path.exists()
    assert md_path.suffix == ".md" and json_path.suffix == ".json"
    json.loads(json_path.read_text(encoding="utf-8"))  # valid JSON
    assert "wFWHM" in md_path.read_text(encoding="utf-8")

# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). `0.x` is pre-1.0 alpha: several
device paths are not yet hardware-validated (see the README "Status & limitations").

## [Unreleased]

### Added
- **AstroPipe — a pure-Python, DSS/PixInsight-free refinement pipeline** (`seestar-refine`):
  - `pystack` backend (`astroalign` + numpy): debayer → star-triangle registration →
    memmap-bounded sigma-clipped integration → `(3,H,W)` master; visually equivalent to
    DSS on 286 real M27 subs. Exposed as `stack_keep_list(engine="pystack")` and reported
    by `check_backends`.
  - Post-processing stages via `stretch_master` params: gradient removal
    (star-masked `Background2D`), star-based white balance, Richardson-Lucy
    deconvolution, saturation, and an opt-in resolution upscale (Lanczos default; the AI
    path is provenance-labeled "AI-generated detail, not captured signal").
  - Percentile white point in `auto_stretch` so faint/compact targets aren't crushed.
- Open-source release hygiene: `LICENSE` (MIT), `NOTICE` (trademark, §1201(f), and
  third-party attribution), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, this changelog, an
  `AUTHORS` file, GitHub Actions CI (Linux + Windows), and issue/PR templates.
- Field-rotation **autocrop** for stacked masters, with a coverage-oriented threshold
  (`coverage_frac`) tuned against real data.
- Packaging metadata in `pyproject.toml` (SPDX license, authors, keywords, classifiers,
  project URLs).

### Fixed
- Observing planner crashed whenever called with default `date=None` (offset-suffixed
  `datetime.now().isoformat()` rejected by astropy); normalized in `_to_time`.
- GPS parser now matches real firmware 7.75 (`result.location_lon_lat` `[lon, lat]`), so
  plans use the scope's actual location instead of a stale saved site.

### Changed
- `SECURITY.md`: corrected the tool count (33 + 5), reworded the `seestar_alp` supply-chain
  note (external, operator-installed — not vendored), added a real vulnerability-reporting
  contact, and documented the host-wide SMB insecure-guest caveat for the filesystem backend.
- README: added "Status & limitations", "Prerequisites", and "Legal & trademarks" sections.
- Scrubbed personal data (real LAN IP / username) from tracked source and docs.

## [0.1.0] - 2026-07-05

Initial build (pre-public): auditable `seestar-mcp` FastMCP server (33 tools) driving a ZWO
Seestar S50 via `seestar_alp`'s ASCOM Alpaca API; two-tier FITS QA; observing planner with
projects/history and a learned horizon mask; autonomous-night mode with hard guardrails; a
separate `seestar-refine` service (5 tools) for DeepSkyStacker / PixInsight stacking; and six
Claude Code skills. Append-only provenance logging, hash-locked dependencies, and a hardened
systemd unit. Validated end-to-end against real hardware (live M27 session; 766-sub M31 deep
stack through DeepSkyStacker).

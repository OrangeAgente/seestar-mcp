# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). `0.x` is pre-1.0 alpha: several
device paths are not yet hardware-validated (see the README "Status & limitations").

## [Unreleased]

### Added
- Open-source release hygiene: `LICENSE` (MIT), `NOTICE` (trademark, §1201(f), and
  third-party attribution), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, this changelog, an
  `AUTHORS` file, GitHub Actions CI (Linux + Windows), and issue/PR templates.
- Field-rotation **autocrop** for stacked masters, with a coverage-oriented threshold
  (`coverage_frac`) tuned against real data.
- Packaging metadata in `pyproject.toml` (SPDX license, authors, keywords, classifiers,
  project URLs).

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

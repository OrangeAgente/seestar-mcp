# SeeStar-AI — Production & Open-Source Readiness Review

**Date:** 2026-07-05
**Scope:** Both MCP services (`seestar_mcp` = 33 tools, `seestar_refine` = 5 tools) and the 6 Claude Code skills.
**Method:** Four parallel audit passes (security/legal, OSS/release, code quality, test quality) plus maintainer working-knowledge synthesis.
**Verdict:** **READY-WITH-FIXES.** The engineering is strong — no code-level release-blockers. Every blocker is legal / OSS-hygiene: paperwork the repo needs before it can be public, not code that needs rewriting.

Suite state at review: **287 tests passing, ruff clean.** No secret is in git history (all 59 commits scanned; the only key-shaped string is the dummy fixture in `tests/test_secrets.py`).

---

## P0 — Release blockers (all legal / hygiene, none are code)

1. **Add a `LICENSE` file.** `pyproject.toml` declares `license = { text = "MIT" }` but there is no license text in the repo → GitHub shows "no license," which legally means *all rights reserved*. Add top-level `LICENSE` (MIT text + `Copyright (c) 2026 <holder>`), and add `authors = [...]` to `[project]`.

2. **Make the §1201(f) interop-key framing user-facing.** The legal basis for the firmware-7.18+ RSA key lives only in `CLAUDE.md` (contributor guide). The repo is *clean on substance* — it ships no key and no extraction tool, and drives an external `seestar_alp` whose key the user supplies themselves — but a public reader can't see that. Add a `## Legal / interoperability` section to README (or `LEGAL.md`) stating: (a) this project ships no ZWO key or firmware and no extraction tool; (b) the user supplies their own `seestar_client_key.pem`, extracted from their own licensed copy under 17 U.S.C. §1201(f) interoperability; (c) users are responsible for their own jurisdiction.

3. **Add a trademark / non-affiliation disclaimer.** "Seestar"/"ZWO" appear throughout (including the package name). Add to README/NOTICE: *"Seestar and ZWO are trademarks of Suzhou ZWO Co., Ltd. This project is unofficial and not affiliated with or endorsed by ZWO."*

4. **Add a `NOTICE` file with third-party attribution — and verify each license first.** The repo *drives but does not vendor*: `seestar_alp`, the external `pixinsight-mcp` (github.com/aescaffre/pixinsight-mcp — **license unverified; confirm before recommending users install it**; macOS-tested/Windows-unverified), DeepSkyStacker (freeware, non-OSS — attribute, don't imply bundling), PixInsight (commercial). Runtime Python deps are all permissive (BSD/MIT: astropy, numpy, pillow, mcp, pydantic, httpx, smbprotocol) — **no copyleft** — so MIT distribution is clean; state that line and keep the CycloneDX `sbom.json` (`make sbom`) as the sign-off artifact.

5. **Fix `SECURITY.md` — three defects in one file:**
   - **False supply-chain claim:** line ~94 says `seestar_alp` is "vendored/pinned at a reviewed commit." It is **not** vendored, not a submodule, not a dependency. Reword to "external, operator-installed dependency (operator should pin it)."
   - **No real disclosure contact:** the reporting section says "raise concerns to the project owner" with no channel. Add a security email and/or "GitHub → Security → Report a vulnerability," plus response-time expectation and no-public-disclosure-until-fixed.
   - **Stale tool count:** says "23 tools"; the repo exposes **33 + 5**. Fix the number — a miscount in the security doc undercuts the "auditable" claim.

6. **Scrub personal data from tracked files** (privacy, not a secret leak):
   - `src/seestar_mcp/config.py:49` — comment hardcodes the real share `\\<seestar-ip>\EMMC Images\MyWorks` → replace with `\\<seestar-ip>\EMMC Images\MyWorks`.
   - `README.md` (~lines 58, 247) — hardcode `C:/Users/<user>/SeeStar-AI` (leaked a username) → `C:/path/to/SeeStar-AI`. Same path also appears in `CLAUDE.md` examples.

7. **Document the SMB `EnableInsecureGuestLogons` relaxation.** The filesystem/UNC backend reads the Seestar share as an unauthenticated guest, which on modern Windows requires the machine-wide `Set-SmbClientConfiguration -EnableInsecureGuestLogons $true` — a real security downgrade (unauthenticated, unsigned guest SMB; rogue-server/MITM exposure). It is documented *nowhere*. Add to README/SECURITY: that it's required for the fs backend, the risk, the revert (`... $false`), and the safer alternatives (isolated IoT VLAN, or prefer the HTTP transport which needs no such change).

## P1 — Expected OSS hygiene (do before or in week one)

8. `CONTRIBUTING.md` — `uv sync` / `uv run pytest` / `uv run ruff check`, the `core.autocrlf=false` CRLF gotcha, TDD expectation, "no new deps without justification." (Rules currently live only in `CLAUDE.md`.)
9. `CODE_OF_CONDUCT.md` — Contributor Covenant 2.1 with a maintainer contact.
10. `CHANGELOG.md` — Keep-a-Changelog, seed with `0.1.0`.
11. **CI:** `.github/workflows/ci.yml` — `setup-uv` → `uv sync --dev` → `ruff check` → `pytest`, on a **`ubuntu-latest` + `windows-latest` matrix** (behavior is Windows-specific: DSS/PixInsight paths, SMB, CRLF). Tests are fully offline (hardware/weather/GPS mocked) so CI needs no secrets. This is also what proves the "287 green tests" claim on every PR.
12. Issue + PR templates — bug template should ask firmware version, `seestar_alp` version, OS (so much is `# FIRMWARE-DEPENDENT`).
13. `AUTHORS` + flesh out `pyproject.toml` (`authors`, `keywords`, `classifiers` incl. `License :: OSI Approved :: MIT License`, `[project.urls]`).
14. **README "Status & limitations" section** — surface that this is alpha and several paths are **not yet hardware-validated**: the `# FIRMWARE-DEPENDENT` helpers (GPS key in `_parse_gps`, battery key in `_parse_device_health`, sub-listing `get_img_file_list`) and PixInsight-on-Windows unverified. A README-only reader currently assumes full validation. Also add an explicit **Prerequisites** list (seestar_alp on :5555, DSS, PixInsight+pixinsight-mcp, user-supplied interop key, the SMB caveat).

## P2 — Polish

15. Decide/state the install story: SemVer; document as **install-from-source via `uv`** (the whole README implies this) and *don't* publish to PyPI (it's an MCP/skills bundle, not a library) so nobody tries `pip install seestar-mcp`.
16. Optional `[project.scripts]` console entry points (`seestar-mcp`, `seestar-refine`).
17. `qa-policy` skill description omits `scattered_light`; README "Skills" lists 5 but there are 6 SKILL.md files — recount.
18. Note in README that `deploy/seestar-mcp.service` (well-hardened: non-root, `ProtectSystem=strict`, syscall filter, egress allowlist) is Linux/Jetson-only; the refine service is Windows-only and has no unit (fine).

---

## Code quality & correctness

**Strong.** Clean separation into two independent services; pure, NaN/inf-safe, never-raising cores in the refine pipeline; deterministic planning (timestamps injected). Verified-genuine controls: path-traversal containment in `download_subs` (`_safe_name` + `resolve()`/`is_relative_to` fail-closed with audit log); subprocess called with list-argv only (no `shell=True`, no `eval`/`exec`/`os.system` anywhere in `src/`); minimal SSRF surface (only the configured host + a keyless Open-Meteo URL); provenance redaction of any `password|secret|token|key|...` field; secrets store that never caches/logs and reports presence only.

**Nits:**
- `src/seestar_refine/dss.py` — `run_dss` docstring still describes the old `[dss_cli, "/L", file_list, ...]` command; the validated code uses positional `/r /S /FITS /O:<master> <listfile>` with **no `/L`**. Update the stale docstring.
- **Autocrop limitation (addressed this session, committed `742d28b`):** the field-rotation autocrop now uses a coverage-oriented threshold (`coverage_frac * median`, default 0.85, tunable) instead of the old percentile formula that only trimmed ~3% on real data. It now removes the black bands, but brightness thresholding **cannot pixel-exactly excise a sheared parallelogram on a smooth sky gradient** — the border ramp straddles any single cut. A perfect crop needs DSS's per-pixel coverage (frame-count) map, which we don't yet capture. This is documented in `crop.py` and is a good future enhancement, not a blocker.

## Test quality

287 tests, offline and deterministic, ruff-clean. Real-hardware validation has retired much of the original firmware-guess risk (DSS command + file-list header caught and fixed against 766 real M31 subs; `get_view_state` structure confirmed on a live M27 session; Alpaca device-number and RSA-handshake issues resolved). Remaining validation debt is the `# FIRMWARE-DEPENDENT` set in P1-14 above — real but confined to single, well-flagged update points.

---

## Fastest path to publishable

Do **P0 items 1–7** (LICENSE, §1201(f) notice, trademark disclaimer, NOTICE/attribution, the three SECURITY.md fixes, personal-data scrub, SMB documentation). That clears all legal and privacy exposure. Then **P1 8–14** (CONTRIBUTING/CoC/CHANGELOG/CI/templates/AUTHORS + Status section) in the first week. P2 is polish.

**Also pending:** two commits are unpushed on `main` — `742d28b` (coverage-oriented autocrop) and `48a584b` (autocrop feature).

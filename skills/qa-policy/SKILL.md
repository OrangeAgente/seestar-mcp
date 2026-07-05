---
name: qa-policy
description: >
  Scoring policy for Seestar S50 sub-frame quality — how to interpret the metrics
  returned by qa_tier2 (FWHM, HFR, eccentricity, SNR, background, star count, wFWHM)
  and decide PASS / MARGINAL / REJECT, plus how to read a qa_session_report. Use
  whenever judging whether collected data is "good", deciding which subs to keep for
  stacking, explaining why a sub was rejected, or setting/adjusting quality thresholds.
  This skill owns the numbers; the run-session skill calls it for interpretation.
---

# Seestar S50 Data-Quality Scoring Policy

This skill defines how to turn raw QA metrics into keep/reject decisions and how to
explain them. The goal is an auditable, defensible quality verdict — never a vibe.
Every verdict should be traceable to a metric and a threshold.

## Metrics and what they mean
- **FWHM** (full width at half maximum): star sharpness. Lower is better. The single
  most important sharpness metric. Rises with poor focus, seeing, dew, and tracking error.
- **HFR** (half-flux radius): alternative sharpness measure, robust on faint/elongated
  stars. Tracks FWHM but degrades more gracefully. Use as a cross-check on FWHM.
- **Eccentricity**: star roundness, 0 = perfect circle. Rises with tracking/guiding
  error and field rotation. (Roundness = FWHM_y / FWHM_x is the equivalent framing.)
- **SNR**: signal-to-noise of detected stars vs background. Falls with clouds, light
  pollution, moonlight, and thin cirrus.
- **Background level / gradient**: sky brightness and its slope across the frame. Step
  jumps signal moonrise or a passing light; gradients signal light pollution or twilight.
- **Star count**: number of detected stars. Collapses under clouds/poor transparency —
  often the earliest and clearest cloud signal.
- **wFWHM (weighted FWHM)**: FWHM weighted by star count. The best single session-quality
  scalar, because a bad frame shows BOTH higher FWHM and fewer stars, and wFWHM captures
  both at once. Prefer wFWHM over raw FWHM when ranking subs within a session.
- **scattered_light**: bright-star halo ratio + large-scale background non-uniformity — the
  signature of thin high cirrus / scattered light that raises the background and halos bright
  stars while barely moving FWHM. A raised local pedestal around the brightest stars plus
  large-scale background structure both push this up; a flat dark sky sits near zero. It
  catches the subtle veils that a uniform background-level or session-relative SNR/star-count
  floor can miss.

## Default thresholds (configurable; state them when you apply them)
Thresholds are relative to the session's own median, not absolute, because the S50's
pixel scale, target altitude, and sky vary. Compute the per-session median first, then:

- **FWHM**: REJECT if FWHM > median + 1.5σ (or > 1.5× median, whichever the config sets).
  MARGINAL between median + 1.0σ and median + 1.5σ.
- **Eccentricity**: REJECT if eccentricity ≥ 0.575 (the canonical PixInsight cutoff —
  distortion below ~0.42 is generally imperceptible, 0.575 is the standard reject line).
  MARGINAL between 0.42 and 0.575.
- **SNR**: REJECT if SNR falls below a session floor (default: median SNR × 0.5) — usually
  indicates clouds/transparency, not a fixable per-sub flaw.
- **Star count**: REJECT if star count < 50% of the session median — strong cloud signal.
- A sub is **PASS** only if it clears all of the above. Any single REJECT trigger rejects
  the sub. MARGINAL on any metric (with no REJECT) makes the sub MARGINAL overall.

Always state the threshold you used and the sub's value, e.g.:
"REJECT — eccentricity 0.61 ≥ 0.575 cutoff (tracking error)."

## Interpreting patterns (not just single subs)
- **Monotonic FWHM rise across the session** → focus drift (temperature) or dew forming.
  Recommend a mid-session refocus next time, or dew-heater use. Keep early subs, scrutinize
  late ones.
- **Eccentricity bursts on isolated subs** → momentary tracking/guiding error or wind gust.
  Reject the affected subs; the session is otherwise fine.
- **Star count + SNR dropping together, FWHM roughly stable** → clouds/transparency, not
  focus. No focus action will help; reject the cloud-affected window.
- **Halo ratio / background non-uniformity up, FWHM roughly stable, SNR & star-count only
  mildly down** → thin cirrus / scattered light. This is the subtle end of the
  clouds/transparency spectrum above: distinct from focus (which moves FWHM) and from thick
  cloud (which collapses star count). The `scattered_light` metric is session-relative and
  specifically catches the veils that would otherwise slip the SNR and star-count floors —
  a faint haze that halos bright stars and softens contrast without tripping the other
  gates. A REJECT on this axis is reason-tagged like the others (naming scattered light and
  the threshold it crossed).
- **Background step change mid-session** → moonrise or a light turned on. Subs may still be
  usable if SNR holds; flag for the user's judgment.
- **Late-session eccentricity/rejection rise in Alt-Az** → field rotation. Expected.
  Reject the trailed subs but do NOT report this as a fault or focus problem.

## Reading a qa_session_report
The report aggregates per-sub verdicts plus session trends. When summarizing it:
1. Lead with the headline: kept N of M subs, total kept integration time, median wFWHM.
2. State the dominant rejection cause (the most common REJECT trigger).
3. Note any session-level pattern from the list above and its likely cause.
4. Point to the keep-list path for re-stacking (the kept subs feed a Siril re-stack with
   OSC_Preprocessing_WithoutDBF.ssf, since Seestar subs are already calibrated).
Keep it to a few lines unless the user asks for the full per-sub breakdown.

## Hard rules
- Tier-1 firmware telemetry is a HEALTH signal, never a quality verdict. Only Tier-2
  FITS metrics justify calling data "good" or "bad."
- Never call a session's data solid without per-sub numbers behind it.
- Always attribute a rejection to a specific metric + threshold; no unexplained rejects.
- Thresholds are session-relative by default; if you apply an absolute threshold, say so
  and say why.
- If the user wants to loosen/tighten thresholds, change the config values and restate
  the new policy explicitly before re-scoring.

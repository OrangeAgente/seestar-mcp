# The MARGINAL tier is saturated by a fixed eccentricity threshold

**Status:** FIXED 2026-08-01, contract v1.1.0. Option 1 below was chosen, with the
0.42 constant retained as a floor rather than deleted. Re-scoring the same 970
subs: PASS 25→710, MARGINAL 831→146, **REJECT 114→114, all three keep-lists
byte-identical**. The measurement that motivated it is kept below as written.

## What was measured

The night of 2026-07-30/31 was offloaded (970 subs, 3.4 GB, byte-size verified
against the share: 970/970, zero mismatches) and scored with the production path
(`analyze_session` → `classify` → `write_report`). This is the first time the
two-tier QA has been run at full session scale rather than on a 25-sub sample.

| target | subs | kept | PASS | MARGINAL | REJECT | median FWHM | median ecc |
|---|---|---|---|---|---|---|---|
| NGC7380 | 206 | 183 (89%) | 1 | 182 | 23 | 2.42 | 0.491 |
| NGC7635 | 149 | 136 (91%) | 2 | 134 | 13 | 2.44 | 0.494 |
| M76 | 615 | 537 (87%) | 22 | 515 | 78 | 2.40 | 0.493 |
| **night** | **970** | **856 (88%)** | **25 (2.6%)** | **831 (86%)** | **114 (12%)** | 2.41 | 0.492 |

## The finding

**86% of the night graded MARGINAL, and 91% of those verdicts came from one
metric.** Reason-tag counts across all 970 subs:

```
MARGINAL  eccentricity   893      REJECT  FWHM            53
MARGINAL  FWHM            75      REJECT  eccentricity    43
MARGINAL  scattered       11      REJECT  scattered       40
                                  REJECT  star_count      19
```

Eccentricity distribution for the night:

```
min 0.326 | p1 0.402 | median 0.492 | p99 0.630 | max 0.785
>= 0.42 (marginal line): 96.5% of subs
>= 0.575 (reject line):   4.4% of subs
```

The marginal line sits at the **1st percentile** of the rig's own output. Two of
the three targets never produced a single sub below it (NGC7635's best was 0.418;
the line is 0.42). MARGINAL is therefore not a signal — it is a constant that
happens to be printed 831 times.

## Root cause

Eccentricity is the only metric whose thresholds are absolute. Every other one is
session-relative (`qa_tier2._effective_thresholds`):

| metric | marginal | reject |
|---|---|---|
| FWHM | `median + 1.0σ` | `median + 1.5σ` |
| scattered light | `median + 1.0σ` | `median + 2.0σ` |
| SNR | — | `median × 0.5` |
| star count | — | `median × 0.5` |
| **eccentricity** | **0.42 fixed** | **0.575 fixed** |

The constants are not arbitrary. `qa-policy/SKILL.md:47` states the reasoning:
0.575 is the canonical PixInsight reject cutoff, and "distortion below ~0.42 is
generally imperceptible" — so MARGINAL was meant to mean *perceptible but not
rejectable* elongation.

That intent is sound. It just does not survive contact with this hardware: an
alt-az Seestar S50 on 10 s subs has a **baseline** eccentricity of ~0.49 (axis
ratio 0.87, stars ~13% elongated). Perceptible elongation is the rig's permanent
normal state, not an anomaly. Field rotation adds a real but small drift on top
(+0.040 across the NGC7380 run, +0.021 across NGC7635) — the offset dominates.

Note the reject line is doing its job: 0.575 sits above p99 and fires on 4.4%.
Only the marginal line is miscalibrated.

## What is and is not broken

- **Keep-lists are unaffected.** Keep = PASS + MARGINAL, so all 856 kept subs are
  correct and all 114 rejects are correctly excluded. No stacking input is wrong.
- **The reporting signal is dead.** "182 of 206 marginal" reads as a poor night to
  a human or a skill; it was in fact a good night (median FWHM 2.41, zero dropped
  frames, 88% kept). Any triage rule that branches on the MARGINAL count is
  branching on noise.

## Options

1. **Make the marginal threshold session-relative** (`median + 1.0σ`), keeping the
   0.575 absolute reject. Consistent with every other metric, and
   `qa_eccentricity_absolute` already exists as the escape hatch. Changes verdicts
   for all users — needs a MAJOR note and regression tests pinning old behaviour.
2. **Re-calibrate the constant per rig** via `qa_eccentricity_marginal` in config,
   documented in `RIG-PROFILE.md`. No code change, no effect on other users, but
   every Seestar owner hits this and each must discover it independently.
3. **Do nothing, document it** in `qa-policy` so the skill stops treating a high
   MARGINAL count as meaningful on alt-az rigs.

Recommendation: **1**, with 2 as the immediate mitigation. Option 1 is what the
codebase already does everywhere else, and this repo is now public — shipping a
default that grades 86% of a good night as MARGINAL is a bad first impression for
every Seestar user who installs it.

## What shipped

`max(median + 1.0σ, 0.42)`, in `qa_tier2._ecc_marginal_threshold`. Option 1, but
keeping 0.42 as a **floor** rather than deleting it — the perceptibility argument
was always sound, it just needed to stop being the whole rule.

Because the floor only ever raises the line, this is a no-op for any rig sitting
below it. Sessions on a well-performing scope score exactly as before, single-sub
sessions included (σ is 0 there, so it collapses to `max(median, 0.42)`, which is
the old comparison). The only sessions whose verdicts move are the saturated ones
— which is the entire bug. Three pre-existing eccentricity tests passed unchanged
against the new code, which is the evidence for that claim, plus three new
regression pins.

`k = 1.0` matches `qa_fwhm_marginal_sigma`; no new tuning philosophy. It produced
12–15% marginal-on-eccentricity per target, stable across all three.

The reject line was left alone. At 0.575 it sits above p99 and fires on 4.4% —
measured as correct, and moving it risks emptying a keep-list on a genuinely poor
rig.

## Hardening after adversarial review (2026-08-02, contract v1.1.1)

An adversarial pass over the shipped commit found two medium defects. Both were
real; neither changed any verdict on the 970-sub reference night (re-scored
byte-identical), but both could bite another rig.

**1. A configured `qa_eccentricity_marginal` was silently demoted to a floor.**
Before the change that setting *was* the exact cutoff. Afterwards someone who had
raised it to 0.50 found their explicit policy widened — the derived line could
sit above it — and `qa_eccentricity_absolute` was no escape hatch because it only
overrides REJECT. Fixed by adding **`qa_eccentricity_marginal_absolute`**, which
bypasses the session-relative calculation entirely, mirroring the existing
`qa_fwhm_absolute` / `qa_scatter_absolute` convention. The default path is
unchanged, so the saturation fix stands.

**2. The derived line could invert past REJECT, or go NaN.**
On a session whose median sits near 0.575 the derived line exceeded the reject
cutoff, so every sub landed PASS or REJECT and MARGINAL became unreachable —
precisely on the poor night where the warning matters. Separately,
`max(nan, floor)` is `nan` and `x >= nan` is always False, so a single non-finite
setting silently disabled the rule *and* would have made the strict-RFC-8259
`render_json` raise, violating the never-raise convention.

The fix is not a simple cap. Capping at REJECT still leaves the band empty and
grades a 0.574 sub PASS. Instead: **when the derived line would reach the reject
cutoff, fall back to the absolute floor.** The reasoning is that "worse than
typical for this session" has stopped describing anything useful once the typical
sub is itself past the absolute reject line — relative grading has run out of
room, so revert to the absolute perceptibility rule. On that poor session
everything perceptibly elongated is now MARGINAL and nothing is silently PASS.
The discontinuity is deliberate and is the signal.

All threshold inputs are now finiteness-guarded, and `_ecc_reject_cut` is shared
so the cap tracks a configured `qa_eccentricity_absolute` rather than the 0.575
default.

**Also fixed: the drift guarantee was overstated.** The commit claimed sharing one
helper meant the reported cutoff could not diverge from the one that scored the
subs. Sharing removed drift *inside* the calculation, not at the call sites — two
callers could still pass different medians. `classify` now computes the cutoff
**once** and passes the value to both `_score_sub` and `_effective_thresholds`,
which is what actually makes the invariant structural.

## Also measured: the real Console payload

**Correction (2026-08-01, after Console review).** An earlier version of this
section claimed the 412 KB figure in
`docs/2026-07-31-response-to-console-handback.md` was "an arithmetic estimate,
high by ~50%". Both halves of that were wrong, and the Console team caught it:

- **412 KB was already a measurement**, not an estimate — that document says so
  in bold, gives its per-verdict basis (298 B PASS / 320 B REJECT), and records
  that it *replaced* an earlier ~140 KB estimate for being 3× off.
- **412 KB was for 1400 subs**, not for a night of 970. Comparing a 970-sub total
  against it and calling the difference an error was a scope mistake. 278 B/sub
  × 1400 = ~380 KB, which *corroborates* 412 KB rather than refuting it.

The sharpest evidence that the retraction was self-refuting: that document's table
says 200 subs → ~59 KB. This one measured 206 subs → 58.8 KB. It reproduced the
table it was retracting to within 0.3%.

Measured on the shipped `_compact_report` payload, 970 real subs:

| session | subs | payload | per sub |
|---|---|---|---|
| NGC7635 | 149 | 41.2 KB | 283 B |
| NGC7380 | 206 | 56.7 KB | 282 B |
| M76 | 615 | 165.6 KB | 276 B |

Per verdict — **cost rises with verdict severity, because reason strings dominate
and a PASS carries none**:

| verdict | n | B/sub |
|---|---|---|
| PASS | 710 | 264 |
| MARGINAL | 146 | 294 |
| REJECT | 114 | 323 |

REJECT at 323 B lands on the original document's measured 320 B. Two
independently written serializers (ours and the Console's) agree within 5–7% once
session mix is accounted for.

**Design against ~412 KB at 1400 subs; the Console's slice-4 decision to make the
analysis a cached background job stands.** Two consequences of the per-verdict
spread are worth stating explicitly:

- The eccentricity fix above *reduced* payload — converting MARGINAL to PASS
  removes reason strings (970 subs: 371.7 → 361.4 KB reconstructed).
- **Payload peaks on bad nights.** A cloud-hit, reject-heavy session is the
  largest *and* the one whose analysis matters most. Any sizing done on a good
  night's mix understates the worst case, which is the case the design must hold
  for. This makes the background-job decision more justified, not less.

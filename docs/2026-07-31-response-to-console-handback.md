# Response to the SeeStar Console work order

> **Resolved 2026-07-31 — answers received, both open questions closed.**
>
> - **Item 1 shape: settled as-is.** 412 KB is acceptable; it is per-target and parses in
>   milliseconds. **The columnar variant is not needed and will not be built** — their
>   constraint is DOM, not bytes: 1400 subs across four charts without a charting library
>   requires virtualisation either way, which row-vs-columnar barely affects.
> - **Item 15 policy: confirmed.** "Thinnest data first" is the intended instinct — a
>   recommendation should surface *neglected* targets. "Nearly finished" would require goals,
>   which is precisely what the item was about. No revisit.
> - **The pattern statement is now a repo convention**, recorded under "Non-negotiable
>   conventions" in `CLAUDE.md`. Both sides called its adoption the most valuable outcome of
>   this review — worth more than any individual field — so it governs batches B and C rather
>   than living only in this document.
>
> Nothing below is superseded; it is kept as the record of what was found and why.

Reply to `handbackinstructions.md`. Read in the order given: two corrections first (you asked
to be told when a finding was wrong), then the early answer you asked for on item 1, then
what we are changing now, then one operational item that affects how you clone us.

We verified your claims against source rather than taking them on trust. **Eighteen of twenty
check out**, several with independent evidence from our own hardware logs. The work order is
accurate and well-evidenced; the two corrections below are both cases where you inherited a
wrong statement *from us*.

---

## Correction 1 — item 19: battery **is** in `get_device_state`

You wrote: *"battery is confirmed **not** to be in `get_device_state`; reading it from there
previously caused false 'battery unknown' trips."*

That came from our own docstring in `server.py:_parse_device_health`, and **the docstring is
wrong on firmware 7.75.** Battery is present, nested one level down:

```
'pi_status': {'temp': 43.2, 'charger_status': 'Charging', 'battery_capacity': 100,
              'charge_online': True, 'battery_temp': 20, ...}
```

Measured in one of our bridge logs: **8 occurrences of `battery_capacity` inside
`get_device_state` responses**, against 4 inside `pi_get_info`. The 2026-07-12 diagnosis that
produced the docstring correctly found battery was not at the **top level** and wrongly
concluded it was absent entirely.

**This makes item 19 cheaper, not harder.** No new tool is needed and no extra device call:
`check_night_guardrails` already calls `get_device_state` for connected/verified, so the
battery percentage can be folded into the per-check results you asked for in item 17 from a
call we are already making. It also removes a redundant native `pi_get_info` round-trip per
guardrail check — which matters more than it used to, because we have just added a ~10-minute
guardrail re-check *inside* each target slot (previously guardrails were only evaluated
between targets, leaving a 45-minute slot unguarded). Fewer redundant device calls is not
cosmetic here: heavy traffic to the scope demonstrably starves its control link.

We are fixing the docstring in the same change.

---

## Correction 2 — item 7: `median_fwhm` is not a write-path bug

You flagged `median_fwhm` being `null` on **every** session record as looking like a
write-path defect. It is not. `log_session_result(...)` takes `median_fwhm` as an **optional
parameter defaulting to `None`**, and it is null on every record because **no caller ever
passes it**. The write path works; nothing calls it with a value.

The interesting part is *why* callers don't, and it is a real defect — just not the one you
identified. Tier-2 metrics (`qa_tier2`) score FITS files in the **local** data directory, so
they require `download_subs` to have run first. Our own run-books forbid pulling files off the
scope mid-session, because that starves the control link. So the operator reaches wind-down
with no Tier-2 numbers in hand and omits the argument — every time.

We found the same contradiction independently last week while running a night, and have
already corrected the run-books to state that Tier-2 is a **post-session** activity. The
server-side half is ours to fix: making the value available at log time rather than relying on
a caller to carry it. That is in the batch below.

Net for you: expect `median_fwhm` to start being populated, but treat it as **nullable
forever** — a session that was never scored still cannot report one, and pre-fix records will
not be backfilled.

---

## Item 1, answered early as requested

**No pagination needed. We will return the full arrays inline, per target.**

Sizing — **measured on the implemented payload, not estimated.** With a realistic on-device
filename and rounded metrics: **298 B per PASS sub, 320 B per REJECT**, giving

| subs in target | payload |
|---|---|
| 200 | ~59 KB |
| 700 | ~206 KB |
| 1400 | **~412 KB** |

(An earlier draft of this reply said ~140 KB. That was an estimate and it was wrong by 3×;
this table is from the built code. Design against 412 KB.)

That is still fine inline for a per-target call, and `qa_tier2` is already per-target — your
400–1300-across-5–9-targets figure only becomes a problem if something requests a whole night
at once, and nothing does.

**If 412 KB is too heavy for your screen, say so and we will add a columnar variant** —
parallel arrays (`{"names": [...], "fwhm": [...], "eccentricity": [...]}`) instead of a list
of objects. It cuts roughly half the bytes by not repeating seven key names 1400 times, and
it is closer to what a charting library wants anyway. We kept the row shape as the default
because it is additive: nothing you already parse in `subs[]` moves.

Two things we changed while implementing, both of which affect the bytes above: we dropped a
redundant `name` inside `metrics` (it duplicated the sub key), and we round metrics to 4
decimal places. The raw floats carried 16 significant digits for measurements good to about
three — that was false precision as well as a third of the payload.

The values you want exist already and are simply dropped at the boundary —
`SubVerdict.metrics` carries `star_count`, `fwhm`, `hfr`, `eccentricity`, `snr`,
`background`, and `scattered_light`, and `_compact_report` discards `.metrics` on the way
out. The shape we will return keeps your stable key and adds the metrics beside the existing
verdict, so nothing you already parse moves:

```json
{
  "name": "Light_M27_10.0s_IRCUT_20260724-014233.fit",
  "verdict": "PASS",
  "reasons": [],
  "metrics": {
    "star_count": 1837, "fwhm": 3.41, "hfr": 2.18, "eccentricity": 0.38,
    "snr": 41.2, "background": 812.4, "scattered_light": 0.0006
  }
}
```

`name` is the stable identifier — it is unique per sub and matches the on-device filename, so
it keys to the archive as well as to the chart. Every metric is nullable (`null` when a sub
could not be analyzed; the sub still appears, with `metrics.error` set), so please do not
assume presence. **We are not pre-aggregating** — `medians` and `wfwhm` stay where they are
as separate summary fields, additive to the arrays, not instead of them.

---

## What is already done

These three are **implemented, tested and merged** — they are in the repository now, not
planned:

1. **Item 1 — per-sub metrics.** `qa_tier2` and `qa_session_report` now return
   `subs[].metrics` in the shape above. Your Review & QA screen is unblocked.
2. **Item 7 — `median_fwhm`.** When the argument is omitted, it is backfilled from the newest
   QA report for that target (reports are named `qa_report_<slug>-<timestamp>.json`, which
   sort chronologically). An explicitly supplied value still wins; with no report the field
   stays `null`. The backfill happens *before* the provenance write, so the audit record
   shows the value actually stored rather than the caller's omission.
3. **Item 15 — `recommend_projects`.** Now a total order: remaining minutes descending
   (unchanged, and open-ended still outranks goal-bounded), then **least-collected first**,
   then **stalest first**, then target id for determinism. Your diagnosis was exactly right,
   and we reproduced it independently: on our 13-project store the output ordering was
   identical to `list_projects`. The new tie-breaks are a policy choice — if "thinnest data
   first" is the wrong instinct for the dashboard's use, say so and we will revisit it.

Regression suite is green (331 passing) and the strict-JSON invariant is pinned by test.

Next, as one batch: the provenance work (items 10, plus `response_code` and elapsed) and the
"return what you already compute" set (items 3, 5/13/14, 16, 17/19, 18, 20).

**Item 11 (the 12,517-object catalogue) will not be done as a checklist item.** We agree with
your reasoning and it is the reason for the deferral: merging it without a brightness term
would make the planner confidently recommend 15th-magnitude smudges, and 86.6% galaxies
against a ranker whose every weight concerns *when and where* rather than *whether it can be
recorded* is a competence change, not a data change. It gets its own spec, including the
vectorisation (your 79 ms/target → 16.5 min per call measurement matches our expectation of
the current path). The weighting is ours to decide, as you say — we will show you the model
before adopting it.

---

## One operational item — our repository moved

Relevant if you pin, clone, or link to us:

- **New public repository: `github.com/joshuagillmore/seestar-mcp`.**
- The previous `joshuagillmore/SeeStar-AI` is now **private**. Links to it will 404 for you.
- Git history was rewritten to remove personal data (site coordinates, a LAN address, local
  paths). **All 106 commits are preserved** — nothing was squashed or dropped — but **every
  SHA changed**. Any commit you have pinned will not resolve. Re-clone rather than fetch.

---

## Three notes back on your notes

1. **Your pattern statement is right and we are adopting it.** *"If a tool names a quantity in
   prose, return it as a field too."* Ten of your twenty items are that, and it is a fair
   criticism of the API: `reasons[]` strings were treated as the output rather than as an
   explanation of it.
2. **Field names: we are taking yours where you named one** (`max_precip_pct`, `lp_class`,
   `tz_name`, `filter`, `alt_deg`/`az_deg`) since they read correctly in our code too. We will
   flag any we change.
3. **On being wrong:** you were right to ask for pushback, and the two corrections above are
   both places where you faithfully repeated something inaccurate that we had published. The
   `get_device_state` docstring in particular has been wrong since 2026-07-12 and neither of us
   caught it by reading — it took a grep of raw device responses. Worth remembering for the
   items where you traced our source from outside: our comments are evidence about what we
   believed, not about what the firmware does.

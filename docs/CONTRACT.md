# seestar-mcp consumer contract — v1.0.0

The response shapes external consumers may rely on, and the rules for changing
them. Enforced by `tests/test_console_contract.py`, which fails **this** repo's
build rather than a consumer's runtime.

This document replaces the prose exchange that produced it. Diff it; don't read
it end to end.

## Status

| | |
|---|---|
| **Version** | 1.0.0 |
| **Covers** | 9 of 34 tools — the ones a consumer actually parses |
| **Validated against a real consumer** | yes — the SeeStar Console parsed a real 25-sub `qa_tier2` payload with an independently written schema, first try, no changes |
| **Enforced by** | `tests/test_console_contract.py` (16 tests) |

## The rules

1. **Required means present, not truthy.** A required key must exist; its value
   may be `null` unless stated otherwise. Consumers tolerate `null` and do not
   tolerate a key vanishing.
2. **Removing or renaming a required key is a MAJOR change.** So is changing a
   value's unit, sign, or reference frame — those are invisible to a schema and
   are the failures that render a plausible wrong picture.
3. **Adding a key is a MINOR change.** Consumers ignore unknown keys.
4. **Unknown ≠ empty.** A value we do not have is **omitted**, never `[]` or a
   fabricated default. `[]` and "not tracked" render identically and mean
   opposite things.
5. **If a tool names a quantity in prose, it returns it as a field too.**
   `reasons[]` explains a value; it does not replace it. (See `CLAUDE.md` — this
   is a repo convention, not just a contract rule.)

## Covered tools

`get_view_state` · `get_status` · `get_run_state` · `qa_tier1` · `qa_tier2` ·
`list_projects` / `recommend_projects` · `assess_conditions` · `plan_targets` ·
`get_target_observability` · `get_site_profile`

## Shapes that are load-bearing and easy to break by accident

- **`get_view_state` must stay valid with `result: {}` and no `View` key.** That
  is the most common real response — a connected, idle scope. Fields *on* `View`
  are deliberately not pinned: a mid-acquisition payload carries `Initialise` and
  `stage` but no `Stack` at all.
- **Plate-solve position lives at `Stack.Annotate.result.annotations[]`**, not
  flat on `Annotate`, and `image_size` is a two-element array.
- **`qa_tier2.summary.subs[].name` is the filename STEM** — no extension. A join
  key built as `f"{name}.fit"` matches nothing.
- **Unanalysable subs stay in `subs[]`** with `metrics.error` set. Filtering them
  out shortens a chart instead of reporting a gap.
- **`summary.thresholds` carries the cutoffs actually applied**, session-relative
  ones included — they move night to night, so only these can position a chart's
  cutoff line.
- **`qa_tier2.summary.target` echoes the `target` argument**, so it is `null`
  whenever a caller uses `paths=`. Do not build a header on it.
- **`list_projects` / `recommend_projects` default to `detail="summary"`**, which
  **omits** `sessions` rather than emptying it. Pass `detail="full"` for history.
- **`get_run_state.state` is tri-valued** — `active` / `idle` / `unknown`.
  `unknown` means a run was recorded but its stamp is stale; it must never be read
  as "the scope is free".

## Timestamps — two shapes, both deliberate

| Layer | Shape | Fields |
|---|---|---|
| Planning | **naive**, no offset | `dark_window_utc`, `best_window_utc` |
| Projects / provenance / run state | **offset-bearing** | `date_utc`, `ts`, `stamped_utc` |

Both are pinned. A normaliser must handle both; unifying them silently is a
breaking change.

## Units — the invariants a schema cannot see

| Field | Unit | Pinned as |
|---|---|---|
| `cloud_cover_pct` | percent | `0..100`, and `> 1` on a cloudy fixture |
| `moon_illum_frac` | fraction | `0..1` |
| `wind_kph` | km/h | `>= 0` |
| `dark_minutes_*` | minutes | `>= 0`, `<=` the dark window, sweet-band `<=` above-floor |
| `suitability` | 0–100 score | `0..100` |

`type` on a planned target must stay within the eight `TARGET_TYPES` values —
an unknown value is not a parse failure, it is a target that renders unlabelled.

## Changing the contract

A breaking change needs a MAJOR bump **and** a note to consumers before it ships,
not after. The contract tests are the gate: if a change makes one fail, that is
the contract objecting, and deleting the assertion is not the fix. Where a pin is
too strict — it fires on a legitimate payload — loosen the **field-presence**
check and keep the **nesting** and **unit** assertions, which are the ones that
catch silent breakage.

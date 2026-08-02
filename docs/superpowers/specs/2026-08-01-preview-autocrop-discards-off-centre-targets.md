# The auto-preview's inscribed crop can discard the target

**Status:** found, not fixed. The masters are correct; only the generated preview
PNG is affected, and `make_preview(..., params={"autocrop": False})` is a working
workaround today.

Found on 2026-08-01 during the first end-to-end run of `seestar_refine` on a real
session (M76, 537 subs, 89 minutes, alt-az).

## Symptom

`m76_master.png` contained no M76. Stars only. The PNG was also 592 px wide from
a 1080 px master.

## What is actually wrong

Nothing in the stack. `m76_master.fit` contains M76, correctly registered. The
target is discarded by the **preview**, not the stacker.

Two crops run in sequence, with opposite and incompatible philosophies:

| stage | function | strategy |
|---|---|---|
| stack | `pystack._coverage_crop` | blackout below `coverage_frac`, then **bounding box** |
| preview | `preview.autocrop` → `crop.largest_inscribed_rectangle` | **largest inscribed rectangle** |

`_coverage_crop`'s own docstring states the intent explicitly:

> This keeps a large diagonal object (e.g. M31) whole — unlike an
> inscribed-rectangle crop, which would slice its corners.

`autocrop` then applies exactly the inscribed-rectangle crop that sentence was
written to avoid. The stacker's deliberate choice is silently undone one step
later.

## Why a long alt-az session makes it bite

Field rotation turns the covered region into a *rotated* rectangle inside the
canvas. The largest axis-aligned rectangle inscribed in a rotated rectangle is
far smaller than its bounding box — and the loss grows with rotation angle, i.e.
with session length.

Measured on this master:

```
master            1920 x 1080
inscribed rect    rows 84-1846 x cols 245-837   = 50% of the canvas
M76 located at    row 1496, col 57              -> 188 px outside the left edge
inside crop?      False
```

Half the canvas was thrown away, and the target went with it. A centred target
survives; an off-centre one on a long session does not. M76 was framed near the
lower-left corner this night, so it was exactly the vulnerable case.

## Why it is not caught by tests

`autocrop`'s contract — "crop to the largest inscribed rectangle of valid data" —
is met perfectly. The function is not buggy against its own spec. The defect is
at the seam: no test stacks a long, heavily rotated session with an off-centre
target and then asserts the target survives into the preview. A fixture with a
rotated footprint and a bright off-centre source would fail today.

## Options

1. **Do not inscribe-crop a master that was already coverage-cropped.** The
   stacker has better information (the real per-pixel coverage array) than the
   preview can recover from luminance alone. Pass `autocrop=False` from
   `stack_keep_list`'s auto-preview path.
2. **Make `autocrop` bounding-box rather than inscribed**, matching
   `_coverage_crop`. Changes every preview, including ones that look fine now,
   and reintroduces black corners the inscribed crop existed to remove.
3. **Keep the inscribed crop but refuse to discard signal** — abort the crop if it
   would drop pixels above some percentile. Most complex; least predictable.

Recommendation: **1**. It removes the contradiction at its source, leaves
`autocrop` correct for the standalone `stretch_master` case where no coverage
array exists, and is a one-argument change. Add the rotated-footprint fixture
described above so the seam is covered either way.

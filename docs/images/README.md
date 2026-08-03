# Figures

Every image here is real output from this repo, not a mockup or a stock photo.

| File | What it is |
|---|---|
| `hero.jpg` | NGC 7635 (Bubble Nebula). 136 kept subs of 149, 22.7 min total, 10 s each, LP filter, 89% moon. |
| `night-triptych.jpg` | NGC 7380 / NGC 7635 / M76 from one unattended night, 2026-07-30/31. |
| `qa-eccentricity.png` | Per-sub eccentricity for the 615-sub M76 session, with the cutoffs actually applied. |
| `qa-eccentricity-dark.png` | The same chart stepped for a dark surface — separately chosen, not an inverted copy. |

## Provenance

All four derive from the session of **2026-07-30/31**: 970 subs captured
unattended, graded by `qa_tier2`, keep-listed, and stacked with the pure-Python
`pystack` backend (DeepSkyStacker and PixInsight were not involved).

The astrophotos are the pipeline's own float32 masters with a display stretch
applied — background subtraction, an asinh transfer, and chroma-only smoothing.
Luminance is untouched, so no star is softened and no detail is painted in. They
are JPEG because they are photographs; the charts stay PNG so their text stays
crisp.

The chart encodes verdicts by **position against labelled cutoff lines rather than
by colour**. Colouring PASS/MARGINAL/REJECT green/amber/red fails colourblind
validation — red against green measures ΔE 4.1 under deuteranopia, well below the
usable floor — and the threshold lines already imply the verdict, so encoding it
twice would add risk without adding information.

## Regenerating

The figures are built from a local archive of the night's FITS files, which is not
in the repo (it is ~3.4 GB). To rebuild them for a different session, point the
same steps at your own `qa_report_<TARGET>.json` and `<target>_master.fit`:
stretch the master for the photos, and plot `subs[].metrics.eccentricity` against
`summary.thresholds` for the chart.

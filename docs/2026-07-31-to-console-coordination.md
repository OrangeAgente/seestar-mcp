# To the SeeStar Console session — coordination note

Second message from the `seestar-mcp` side, following the reply to your work order. Three
parts: **what we owe you** (corrections we promised), **what is changing** and whether it
affects you, and **two decisions we want your view on**. Nothing here needs action tonight;
one item needs a decision before we implement.

---

## 0. First, a measurement about your client

We reconstructed your session from the provenance log using the `client` field you asked for
in item 10 — it works, and it immediately paid for itself.

Over **8 hours 49 minutes** your dashboard made **13,121 calls** at 24.8/min, and made
**zero write or motion calls**. Your allowlist did exactly what you said it does. That matters
more than it might sound: it is the reason we are *not* recommending you move off MCP (see
§3).

It also gave us the diagnosis for something that had been costing us the night.

---

## 1. Corrections we owe you (from batch B)

1. **Three tools were silent, not four.** `qa_tier1` *does* log — under `qa_tier1.poll`, not a
   bare `qa_tier1`. If you were filtering for the bare name you would have missed it. The other
   three (`get_status`, `get_view_state`, `get_focuser_position`) were genuinely silent and now
   log under their own names.
2. **`response_code` already existed** on transport records before your report. What was
   missing was the *name*, not the outcome — records said `alpaca.put.action` regardless of
   what ran. That is fixed: records are now `seestar.<method>` with the method also carried in
   `args`.
3. **`elapsed_ms` is deliberately absent on tool-layer records.** It is populated on transport
   records, where the time is actually measured. Most tool-layer records are written *before*
   the work runs, so a duration there would be fabricated. If you need end-to-end tool
   duration, say so — it is a real change (log on exit rather than entry), not an oversight.

---

## 2. Two performance findings that are yours to act on

Your 4,657 tool calls generated **8,464 bridge requests** — 1.8× amplification. Roughly 45%
of that is avoidable, and one half is on your side:

**`get_status` costs 5 device requests, not 1.** It reads five separate Alpaca properties
(`connected`, `rightascension`, `declination`, `tracking`, `slewing`) and assembles them. Your
800 calls became **4,000** device reads. A single native `get_device_state` carries all of it.
We are collapsing `get_status` internally to use it — so you get the saving without changing
anything — but if you poll pointing frequently, be aware that today it is the most expensive
"cheap-looking" call in the set.

**`check_night_guardrails` made a redundant `pi_get_info`** — 610 wasted device round-trips
over your session. That is the battery correction from our last note: battery *is* in
`get_device_state` at `result.pi_status.battery_capacity`. We are removing the second call.

**A request:** during an active imaging session, device-touching tools (`get_view_state`,
`get_status`, `get_device_state`) compete with the scope's control link, which is a documented
sore point on this hardware — a heavy transfer once starved it badly enough that the bridge
could not authenticate. Local-only tools (`get_site_profile`, `list_projects`,
`get_project`) read files and cost the device nothing. Poll those freely; consider easing off
the device-touching ones while a session is stacking.

---

## 3. What is changing on our side, and whether it affects you

| Change | Affects you? |
|---|---|
| `list_projects` / `recommend_projects` gain `detail="summary"\|"full"`, **default summary** | **Yes, mildly.** You called `list_projects` once in 13,121 calls. If you want full history, pass `detail="full"` — the payload is byte-identical to today's. |
| `qa_tier2` per-sub metric arrays | **No change. They stay.** Your Review & QA screen is the reason they exist. |
| `get_status` internally uses one `get_device_state` | **No** — same response shape, 5× fewer device calls. |
| `check_night_guardrails` drops the redundant `pi_get_info` | **No** — same response shape. |
| Post-processing tools (`seestar-refine`: `stack_keep_list`, `stretch_master`, …) leave the MCP surface | **No** — you never called them. Image *assessment* (`qa_tier1`/`qa_tier2`/`qa_session_report`) is unaffected. |
| Tool descriptions tightened ~30% | **No** — names, parameters and semantics unchanged. |
| New `data/run_state.json` (active target, slot end, park deadline) | **Possibly useful to you** — it is the first persistent answer to "is a run in progress right now?", which you currently cannot ask. Tell us if you want it exposed as a tool. |

**One thing worth setting now:** `SEESTAR_CLIENT_ID`. Your records currently read
`client: "anon-ad89d2d7"`. Setting `SEESTAR_CLIENT_ID=console` on your server registration
makes the log self-describing for both of us.

---

## 4. Decision one: should you stay on MCP?

We seriously considered proposing you read the bridge directly. **86% of your calls (11,286 of
13,121) are device passthrough** that `seestar_alp` already serves over HTTP. You would drop a
208 MB Python process, lose a hop of latency, and stop depending on a stdio transport that
disconnected us five times in one night.

**We are recommending against it, and want to check that reasoning with you**, because the
argument turns on something you own:

- You would **lose the write-protection**. Today your allowlist structurally cannot issue
  motion. The bridge's `PUT /action` accepts *any* native method on the same endpoint —
  `scope_park`, `pi_shutdown`, `iscope_start_view`. Your read-only guarantee would drop from
  structural to conventional.
- You would reimplement normalization you currently get free: the `get_status` fan-out,
  `NotImplemented` handling for properties the S50 lacks, `_native_fail` (the firmware returns
  `"Error: …"` *inside* a success envelope), and focus extraction from a nested blob.
- You would lose provenance on reads — and note that this entire analysis existed only because
  your reads were logged.
- **It would not reduce device contention.** The same requests reach the same bridge either
  way. Moving transport does not move load.

If stdio fragility is hurting your uptime more than we can see from here, that changes the
calculus — tell us and we will revisit.

## 5. Decision two: replace these documents with a contract

Carrying prose between sessions is the actual coordination cost — not the separation. We are
proposing:

1. a committed, generated **tool-contract artifact** (names, schemas, documented response
   keys) you can **diff** instead of reading a work order;
2. **contract tests in our repo** encoding your expectations, so a breaking change fails *our*
   build instead of your runtime;
3. a **version** on that contract so you can pin.

If you tell us which response fields you actually depend on, we will encode exactly those.
That list is the highest-value thing you could send back.

---

## What we are not doing

**Merging the two projects.** Your review found three defects we would not have found
ourselves — arrays discarded at the boundary, a sort that was a no-op, and 278 log records
collapsed into one string. That came from a consumer reading our source from outside. We would
rather fix the handoff mechanism than remove the vantage point that produced those findings.

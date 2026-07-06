---
name: Bug report
about: Report something not working
title: "[bug] "
labels: bug
---

**What happened / what you expected**

A clear description of the bug and the behavior you expected.

**Steps to reproduce**

1. …
2. …

**Environment (important — much of this project is firmware-dependent)**

- Seestar firmware version:
- `seestar_alp` version:
- SeeStar-AI commit / version:
- OS + version:
- Python version (`uv run python --version`):
- Which service/tool: `seestar-mcp` / `seestar-refine` (name the tool)

**Logs / provenance**

Relevant output, stack trace, or a redacted provenance excerpt. **Do not paste secrets, keys,
or your interop key.**

**Was a hardware / `# FIRMWARE-DEPENDENT` path involved?**

If the failing tool reads device telemetry (e.g. `list_subs`, GPS/battery, view state), note
your firmware version — the payload key/method names can differ across firmware.

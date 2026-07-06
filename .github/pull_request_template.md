## Summary

<!-- What does this change and why? -->

## Checklist

- [ ] `uv run pytest` passes (offline; no new secrets/hardware needed)
- [ ] `uv run ruff check src tests` is clean
- [ ] New/changed behavior is covered by tests (TDD)
- [ ] No new dependencies (or justified below and `uv.lock` regenerated, permissive license)
- [ ] No secrets, keys, real IPs, or usernames added to tracked files
- [ ] MCP tool descriptions are honest; side effects labelled `SIDE EFFECT`
- [ ] Provenance / redaction logic and the audit trail are unaffected (or reviewed against `SECURITY.md`)
- [ ] Any `# FIRMWARE-DEPENDENT` path is flagged, with hardware-validation status noted below

## Hardware validation

<!-- If you touched a device path, state what firmware/scope you tested against, or "not validated". -->

## Notes

<!-- New deps, follow-ups, or anything a reviewer should know. -->

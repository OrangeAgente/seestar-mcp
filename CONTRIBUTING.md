# Contributing to SeeStar-AI

Thanks for your interest in improving `seestar-mcp`. This project is an auditable MCP
server + Claude Code Skills for the ZWO Seestar S50, so contributions are held to a high bar
on correctness, honesty of tool descriptions, and the audit trail. Please read this before
opening a PR.

## Development setup

This repo uses [`uv`](https://docs.astral.sh/uv/) for everything. There is **no bare
`python`** on the reference machine — always go through `uv`.

```bash
uv sync                          # install from the hash-locked uv.lock
uv run pytest                    # run the full test suite (offline, deterministic)
uv run ruff check src tests      # lint
uv run python -m seestar_mcp.server      # launch the MCP server (stdio)
uv run python -m seestar_refine.server   # launch the refinement service (stdio)
```

`make test` / `make lint` / `make run` wrap the same commands.

## Ground rules

- **TDD.** Write a failing test first, make it pass, then refactor. Every behavior change
  ships with tests. The suite is fully offline — hardware, weather, and GPS are mocked; keep
  it that way so CI needs no secrets or devices.
- **Keep it green and clean.** `uv run pytest` and `uv run ruff check src tests` must both
  pass before you push. CI runs both on Linux and Windows.
- **No new dependencies without justification.** Deps are exact-pinned and hash-locked in the
  committed `uv.lock`. If you genuinely need one, call it out in the PR and run `uv lock`, and
  keep it permissively licensed (no GPL/LGPL/AGPL in the tree — see `NOTICE`).
- **Honest tools.** MCP tool descriptions must be accurate and non-obfuscated; label side
  effects `SIDE EFFECT`. Never add hidden instructions or dynamic/remote tool definitions.
- **Never weaken the audit trail.** Changes touching the auth path, tool descriptions, the
  dependency set, or the provenance/redaction logic must be reviewed against
  [`SECURITY.md`](SECURITY.md).
- **Never commit secrets or personal data.** `.env`, `secrets/`, `*.pem`, `*.key`, and `data/`
  are gitignored — keep it so. No real IPs, usernames, or key material in tracked files.
- **Firmware-dependent code.** Anything reading a device payload whose exact key/method name
  is unconfirmed must be flagged `# FIRMWARE-DEPENDENT` so it stays a single update point.

## Line endings (Windows gotcha)

Set `git config core.autocrlf false` locally so commits don't churn CRLF/LF. The repo content
is LF; ruff and pytest gate correctness regardless.

## Pull requests

- Branch from `main`; keep PRs focused on one change.
- Fill in the PR template (tests pass, ruff clean, provenance/redaction unaffected).
- Describe hardware validation status if you touched a `# FIRMWARE-DEPENDENT` path.

By contributing you agree your contributions are licensed under the project's
[MIT License](LICENSE).

.PHONY: run test lint lock sbom scan

# Launch the MCP server over stdio (no inbound network port is opened).
run:
	uv run python -m seestar_mcp.server

# Run the test suite.
test:
	uv run pytest

# Lint with ruff.
lint:
	uv run ruff check src tests

# Regenerate the hash-pinned lockfile (reproducible installs); commit the result.
lock:
	uv lock

# Write a CycloneDX SBOM (JSON) from the locked environment.
sbom:
	uv run cyclonedx-py environment --of JSON -o sbom.json

# NON-BLOCKING supply-chain / tool-poisoning check. The leading '-' and '|| true'
# mean a missing tool or any findings never fail the build.
scan:
	-uvx mcp-scan@latest scan || true

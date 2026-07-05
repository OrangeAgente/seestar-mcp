"""Append-only audit logging and per-session manifests for seestar-mcp.

Design principles:
- Append-only JSONL: every tool call is one line, never rewritten or deleted.
- Provenance: correlate client/server transaction IDs and hash any FITS touched.
- No secrets, ever: all args/requests/config summaries pass through ``redact``
  before being written. Secret-looking keys are masked, not stored.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REDACTED = "***REDACTED***"

# Keys (case-insensitive substring) whose values must never be persisted.
_SECRET_KEY_RE = re.compile(
    r"password|secret|token|key|rsa|private|credential|auth",
    re.IGNORECASE,
)


def redact(obj: Any) -> Any:
    """Recursively copy ``obj``, masking values of secret-looking keys.

    Works on nested dicts and lists. Non-container values are returned as-is.
    The input is never mutated.
    """
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                out[key] = REDACTED
            else:
                out[key] = redact(value)
        return out
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    if isinstance(obj, tuple):
        return [redact(item) for item in obj]
    return obj


def hash_fits(path_or_bytes: str | Path | bytes) -> str:
    """Return a ``sha256:``-prefixed hex digest of a file's or bytes' content.

    Used to bind downloaded FITS subs into the provenance chain of custody.
    """
    hasher = hashlib.sha256()
    if isinstance(path_or_bytes, (bytes, bytearray)):
        hasher.update(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


class ProvenanceLog:
    """Append-only JSONL audit log of MCP tool calls."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def log_call(
        self,
        *,
        tool: str,
        args: dict,
        request: str | None = None,
        client_txn_id: int | None = None,
        server_txn_id: int | None = None,
        response_code: int | None = None,
        fits_hash: str | None = None,
        note: str | None = None,
    ) -> dict:
        """Append one redacted audit record and return it.

        ``ts`` (UTC ISO 8601) and ``tool`` are always present. Other optional
        fields are omitted when None. Secret-looking values in ``args`` (and in
        ``request`` if it is a string) are redacted before writing.
        """
        redacted_args = redact(args)
        redacted_request = _redact_request(request)

        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool,
            "args": redacted_args,
        }
        # Optional fields: include only when not None.
        optional = {
            "request": redacted_request,
            "client_txn_id": client_txn_id,
            "server_txn_id": server_txn_id,
            "response_code": response_code,
            "fits_hash": fits_hash,
            "note": note,
        }
        for key, value in optional.items():
            if value is not None:
                record[key] = value

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
            handle.flush()
        return record


def _redact_request(request: str | None) -> str | None:
    """Redact secret-looking tokens embedded in a raw request string.

    Handles form-urlencoded / query-style ``key=value`` fragments where a
    secret-looking key appears, replacing the value with the redaction marker.
    """
    if request is None:
        return None

    def _replace(match: re.Match[str]) -> str:
        return f"{match.group('key')}{match.group('sep')}{REDACTED}"

    # Match key=value or key: value where key contains a secret-like word.
    pattern = re.compile(
        r"(?P<key>[\w.\-]*(?:password|secret|token|key|rsa|private|credential|auth)[\w.\-]*)"
        r"(?P<sep>\s*[=:]\s*)"
        r"(?P<val>[^&\s;,]+)",
        re.IGNORECASE,
    )
    return pattern.sub(_replace, request)


class SessionManifest:
    """Accumulates per-session QA verdicts and metadata into a manifest file."""

    def __init__(
        self,
        session_id: str,
        manifest_dir: str | Path,
        target: str | None = None,
        config_summary: dict | None = None,
    ) -> None:
        self.session_id = session_id
        self.manifest_dir = Path(manifest_dir)
        self.target = target
        # Redact on construction so secrets can never enter the manifest state.
        self.config_summary: dict = redact(config_summary or {})
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.meta: dict[str, Any] = {}
        self.verdicts: dict[str, dict] = {}
        self.keep_list: list[str] = []

    def add_verdict(self, sub_name: str, verdict: str, metrics: dict) -> None:
        """Record a per-sub QA verdict with its metrics."""
        self.verdicts[sub_name] = {"verdict": verdict, "metrics": metrics}

    def set_keep_list(self, keep: list[str]) -> None:
        """Set the final keep-list of subs to integrate."""
        self.keep_list = list(keep)

    def set_meta(self, **kwargs: Any) -> None:
        """Merge extra session metadata (start time, mount mode, filter, ...)."""
        self.meta.update(kwargs)

    def write(self) -> Path:
        """Write the manifest as pretty JSON and return its path."""
        manifest = {
            "session_id": self.session_id,
            "target": self.target,
            "created_at": self.created_at,
            "config_summary": redact(self.config_summary),
            "meta": self.meta,
            "verdicts": self.verdicts,
            "keep_list": self.keep_list,
        }
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        path = self.manifest_dir / f"{self.session_id}.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=False)
            handle.write("\n")
        return path

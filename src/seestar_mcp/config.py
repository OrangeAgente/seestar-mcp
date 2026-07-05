"""Application settings for seestar-mcp.

Security note: this module holds NO secrets. RSA keys (firmware 7.18+ auth),
tokens, and any other credentials live in a dedicated secrets module (built
later) and/or an external secrets store — never in this config class, never in
code, never in the provenance log. Only non-sensitive endpoint/threshold
configuration belongs here.

All fields are overridable via environment variables with the ``SEESTAR_``
prefix (e.g. ``SEESTAR_ALPACA_BASE_URL`` overrides ``alpaca_base_url``).
"""

from __future__ import annotations

import ipaddress
import warnings
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Non-secret runtime configuration for the seestar-mcp server."""

    model_config = SettingsConfigDict(
        env_prefix="SEESTAR_",
        env_file=".env",
        extra="ignore",
    )

    # --- seestar_alp ASCOM Alpaca server ---
    alpaca_base_url: str = "http://127.0.0.1:5555"
    # seestar_alp registers the scope at the Alpaca device number equal to its
    # config ``device_num``; the shipped example uses ``1``, so a standard
    # single-scope install is Alpaca device 1 (e.g. /api/v1/telescope/1/...).
    # Override via ``SEESTAR_ALPACA_DEVICE_NUM`` if your seestar_alp config uses
    # a different number.
    alpaca_device_num: int = 1

    # --- Seestar device on the LAN ---
    seestar_host: str = "127.0.0.1"  # Seestar LAN IP (station mode, DHCP reservation)
    jsonrpc_port: int = 4700  # native line-delimited JSON-RPC command/control
    http_port: int = 80  # device built-in HTTP server for sub downloads
    smb_port: int = 445  # SMB share for data pulls/deletes

    # --- MCP server bind ---
    # MUST default to localhost. Never expose beyond localhost/LAN. See the
    # bind-host validator below, which warns on public addresses.
    bind_host: str = "127.0.0.1"

    # --- Filesystem layout ---
    data_dir: Path = Path("./data")
    provenance_log: Path = Path("./data/provenance.jsonl")  # append-only audit log
    manifest_dir: Path = Path("./data/manifests")  # per-session manifests

    # --- HTTP ---
    http_timeout_s: float = 30.0

    # --- QA thresholds (session-relative by default; absolute overrides optional) ---
    qa_fwhm_sigma: float = 1.5  # REJECT if FWHM > median + this*sigma
    qa_fwhm_marginal_sigma: float = 1.0
    qa_eccentricity_reject: float = 0.575  # canonical PixInsight cutoff
    qa_eccentricity_marginal: float = 0.42
    qa_snr_floor_factor: float = 0.5  # REJECT if SNR < median*this
    qa_starcount_floor_factor: float = 0.5
    qa_fwhm_absolute: float | None = None  # absolute override; None = session-relative
    qa_eccentricity_absolute: float | None = None

    @field_validator("bind_host")
    @classmethod
    def _warn_if_public_bind_host(cls, value: str) -> str:
        """Warn (never raise) if bind_host is a public, routable address.

        Enforces the "localhost/LAN only, never public" rule. Loopback
        (127.0.0.0/8) and RFC1918 private ranges (10/8, 172.16/12, 192.168/16)
        are fine. Non-IP hostnames (e.g. "localhost", "seestar.local") are
        skipped since we cannot classify them here.
        """
        try:
            addr = ipaddress.ip_address(value)
        except ValueError:
            # Not a parseable IP (hostname) — cannot classify; skip the check.
            return value
        if not (addr.is_loopback or addr.is_private):
            warnings.warn(
                f"bind_host={value!r} is a public/non-private address. "
                "seestar-mcp must bind to localhost/LAN only and must never be "
                "exposed publicly.",
                UserWarning,
                stacklevel=2,
            )
        return value


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()

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

    # --- Weather ---
    # meteoblue multi-model API key (SEESTAR_METEOBLUE_API_KEY in .env, which is
    # gitignored). When set, the planner uses meteoblue as the primary weather
    # source with Open-Meteo as fallback; empty = Open-Meteo only. Not a device
    # secret, but keep it out of source: it lives only in the gitignored .env.
    meteoblue_api_key: str = ""
    # Weather forecasts do not change minute to minute, and check_night_guardrails
    # uses only the tri-state weather_go. An uncached poll loop burned ~8M meteoblue
    # credits on 2026-07-31 (951 fetches in one day). 15 min caps a once-a-minute
    # poller at ~4 fetches/hour instead of 60. Set 0 to disable caching.
    weather_cache_ttl_s: float = 900.0

    # --- Seestar device on the LAN ---
    seestar_host: str = "127.0.0.1"  # Seestar LAN IP (station mode, DHCP reservation)
    jsonrpc_port: int = 4700  # native line-delimited JSON-RPC command/control
    http_port: int = 80  # device built-in HTTP server for sub downloads
    smb_port: int = 445  # SMB share for data pulls/deletes

    # Filesystem path to the Seestar's MyWorks folder (e.g.
    # ``\\<seestar-ip>\EMMC Images\MyWorks``, a mapped drive, or a local
    # copy). When set, list_subs/download_subs read subs directly from the
    # filesystem (OS SMB redirector) instead of the JSON-RPC/HTTP path.
    seestar_image_root: str = ""

    # --- MCP server bind ---
    # MUST default to localhost. Never expose beyond localhost/LAN. See the
    # bind-host validator below, which warns on public addresses.
    bind_host: str = "127.0.0.1"

    # --- Filesystem layout ---
    data_dir: Path = Path("./data")
    provenance_log: Path = Path("./data/provenance.jsonl")  # append-only audit log
    # Identifies THIS process in the provenance log. Several clients can append to
    # one log (the agent's server, a dashboard running its own), and without an id
    # their traffic is indistinguishable. Empty = a generated per-process id; set
    # SEESTAR_CLIENT_ID to something recognisable (e.g. "console", "agent").
    client_id: str = ""
    manifest_dir: Path = Path("./data/manifests")  # per-session manifests

    # --- HTTP ---
    http_timeout_s: float = 30.0

    # --- QA thresholds (session-relative by default; absolute overrides optional) ---
    qa_fwhm_sigma: float = 1.5  # REJECT if FWHM > median + this*sigma
    qa_fwhm_marginal_sigma: float = 1.0
    qa_eccentricity_reject: float = 0.575  # canonical PixInsight cutoff
    # Perceptibility FLOOR, not the threshold itself: distortion below ~0.42 is
    # generally imperceptible, so MARGINAL never fires beneath this. The line
    # actually applied is max(median + marginal_sigma*sigma, this) -- an alt-az
    # rig baselines near 0.49, and a fixed 0.42 graded 96.5% of a good night
    # MARGINAL. See docs/superpowers/specs/
    # 2026-08-01-eccentricity-marginal-saturation.md
    qa_eccentricity_marginal: float = 0.42
    qa_eccentricity_marginal_sigma: float = 1.0  # matches qa_fwhm_marginal_sigma
    # Exact MARGINAL cutoff, bypassing the session-relative calculation entirely
    # — the escape hatch for anyone who wants a fixed line, mirroring
    # qa_fwhm_absolute / qa_scatter_absolute / qa_eccentricity_absolute. Added
    # 2026-08-02: before the session-relative change, qa_eccentricity_marginal
    # WAS the exact cutoff, so users who had raised it found their explicit
    # policy silently widened into a mere lower bound with no way back.
    qa_eccentricity_marginal_absolute: float | None = None
    qa_snr_floor_factor: float = 0.5  # REJECT if SNR < median*this
    qa_starcount_floor_factor: float = 0.5
    qa_fwhm_absolute: float | None = None  # absolute override; None = session-relative
    qa_eccentricity_absolute: float | None = None
    # Scattered-light / halo metric (session-relative by default; absolute optional).
    qa_scatter_reject_sigma: float = 2.0  # REJECT if scatter > median + this*sigma
    qa_scatter_marginal_sigma: float = 1.0
    qa_scatter_absolute: float | None = None  # absolute override; None = session-relative

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

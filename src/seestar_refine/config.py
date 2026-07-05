"""Settings for the seestar-refine service.

Holds NO secrets — only non-sensitive desktop-app paths and stacking/output
directories. All fields are overridable via environment variables with the
``SEESTAR_REFINE_`` prefix (e.g. ``SEESTAR_REFINE_DSS_CLI`` overrides
``dss_cli``). The external engines (DeepSkyStacker, PixInsight) are desktop apps
whose executable paths the user configures here; a tool never accepts an
arbitrary executable path from its own arguments.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class RefineSettings(BaseSettings):
    """Non-secret runtime configuration for the seestar-refine server."""

    model_config = SettingsConfigDict(
        env_prefix="SEESTAR_REFINE_",
        env_file=".env",
        extra="ignore",
    )

    # --- external desktop-app CLIs (user-configured; validated before exec) ---
    dss_cli: str = ""  # path to DeepSkyStackerCL(.exe)
    pixinsight_exe: str = ""  # path to PixInsight(.exe)

    # --- filesystem layout ---
    data_dir: Path = Path("./data")  # shared dir holding QA reports + subs
    output_dir: Path = Path("./data/refine")  # masters/previews/provenance

    # --- stacking params ---
    rejection: str = "kappa-sigma"  # pixel-rejection algorithm
    alignment: str = "auto"  # star-alignment mode


@lru_cache
def get_settings() -> RefineSettings:
    """Return a cached RefineSettings instance."""
    return RefineSettings()

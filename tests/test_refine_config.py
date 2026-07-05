"""Unit tests for seestar_refine.config (RefineSettings defaults + env prefix)."""

from __future__ import annotations

from pathlib import Path

from seestar_refine.config import RefineSettings, get_settings


def test_defaults():
    s = RefineSettings(_env_file=None)
    assert s.dss_cli == ""
    assert s.pixinsight_exe == ""
    assert s.data_dir == Path("./data")
    assert s.output_dir == Path("./data/refine")
    assert s.rejection == "kappa-sigma"
    assert s.alignment == "auto"


def test_env_prefix(monkeypatch):
    monkeypatch.setenv("SEESTAR_REFINE_DSS_CLI", "C:/DSS/DeepSkyStackerCL.exe")
    monkeypatch.setenv("SEESTAR_REFINE_REJECTION", "winsorized-sigma")
    s = RefineSettings(_env_file=None)
    assert s.dss_cli == "C:/DSS/DeepSkyStackerCL.exe"
    assert s.rejection == "winsorized-sigma"


def test_get_settings_cached():
    assert get_settings() is get_settings()

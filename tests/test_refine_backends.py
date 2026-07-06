"""Unit tests for seestar_refine.backends (filesystem capability detection)."""

from __future__ import annotations

from seestar_refine.backends import Backends, detect_backends
from seestar_refine.config import RefineSettings


def test_detect_backends(tmp_path):
    exe = tmp_path / "DeepSkyStackerCL.exe"
    exe.write_bytes(b"x")
    b = detect_backends(
        RefineSettings(_env_file=None, dss_cli=str(exe)),
        bridge_dir=tmp_path / "nope",
    )
    assert b.dss is True
    assert b.pixinsight_mcp is False


def test_no_cli_all_false_with_notes(tmp_path):
    b = detect_backends(
        RefineSettings(_env_file=None), bridge_dir=tmp_path / "nope"
    )
    assert isinstance(b, Backends)
    assert b.dss is False
    assert b.pixinsight is False
    assert b.pixinsight_mcp is False
    # A note for each unavailable backend.
    assert len(b.notes) == 3


def test_pixinsight_and_bridge(tmp_path):
    exe = tmp_path / "PixInsight.exe"
    exe.write_bytes(b"x")
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    b = detect_backends(
        RefineSettings(_env_file=None, pixinsight_exe=str(exe)),
        bridge_dir=bridge,
    )
    assert b.pixinsight is True
    assert b.pixinsight_mcp is True
    assert b.dss is False


def test_detect_backends_reports_pystack(tmp_path):
    # astroalign is a project dependency, so pystack is available in-env.
    from seestar_refine.backends import detect_backends
    from seestar_refine.config import RefineSettings

    b = detect_backends(
        RefineSettings(_env_file=None, dss_cli="", pixinsight_exe=""),
        bridge_dir=tmp_path / "nope",
    )
    assert b.pystack is True

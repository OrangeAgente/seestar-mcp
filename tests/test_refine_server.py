"""Unit tests for seestar_refine.server (controller + tool registration)."""

from __future__ import annotations

import asyncio

from seestar_refine.config import RefineSettings
from seestar_refine.server import RefineController, mcp


def test_check_backends_tool_registered():
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "check_backends" in names


def test_check_backends_controller(tmp_path):
    settings = RefineSettings(
        _env_file=None, data_dir=tmp_path, output_dir=tmp_path
    )
    controller = RefineController.from_settings(settings)
    result = asyncio.run(controller.check_backends())
    assert result["ok"] is True
    backends = result["backends"]
    assert backends["dss"] is False
    assert backends["pixinsight"] is False
    assert backends["pixinsight_mcp"] is False
    assert isinstance(backends["notes"], list)


def test_provenance_written(tmp_path):
    settings = RefineSettings(
        _env_file=None, data_dir=tmp_path, output_dir=tmp_path
    )
    controller = RefineController.from_settings(settings)
    asyncio.run(controller.check_backends())
    assert (tmp_path / "refine_provenance.jsonl").exists()

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


def test_stack_keep_list_tool_registered():
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "stack_keep_list" in names


def test_stack_keep_list_dss_success(tmp_path, monkeypatch):
    from seestar_refine import dss
    from seestar_refine.dss import StackResult

    # A resolvable keep-list: subs under <data_dir>/<target>.
    target = "M27"
    sub_dir = tmp_path / target
    sub_dir.mkdir()
    (sub_dir / "m27_sub1.fit").write_bytes(b"x")
    (sub_dir / "m27_sub2.fit").write_bytes(b"x")

    canned = StackResult(
        ok=True,
        engine="dss",
        target=target,
        n_subs=2,
        master_path=str(tmp_path / "Autosave.fit"),
        preview_path=None,
        stats={"min": 0.0, "median": 1.0, "max": 2.0, "shape": [4, 4]},
        log="stacked",
    )

    def fake_stack(keep_list, settings, *, runner=None):
        # The real keep-list was resolved before we got here.
        assert keep_list.target == target
        assert len(keep_list.sub_paths) == 2
        return canned

    monkeypatch.setattr(dss, "stack", fake_stack)

    settings = RefineSettings(
        _env_file=None,
        data_dir=tmp_path,
        output_dir=tmp_path,
        dss_cli="C:/DSS/DeepSkyStackerCL.exe",
    )
    controller = RefineController.from_settings(settings)
    result = asyncio.run(controller.stack_keep_list(target, engine="dss"))

    assert result["ok"] is True
    assert result["engine"] == "dss"
    assert result["target"] == target
    assert result["n_subs"] == 2
    assert result["master_path"].endswith("Autosave.fit")
    assert result["stats"]["shape"] == [4, 4]
    # The external invocation was provenance-logged.
    log = (tmp_path / "refine_provenance.jsonl").read_text(encoding="utf-8")
    assert "stack_keep_list" in log


def test_stack_keep_list_from_qa_report(tmp_path, monkeypatch):
    import json

    from seestar_refine import dss
    from seestar_refine.dss import StackResult

    target = "M27"
    (tmp_path / "m27_sub1.fit").write_bytes(b"x")
    (tmp_path / "m27_sub2.fit").write_bytes(b"x")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "qa_report_m27-20260705T000000Z.json").write_text(
        json.dumps(
            {"target": target, "keep_list": ["m27_sub1.fit", "m27_sub2.fit"]}
        ),
        encoding="utf-8",
    )

    seen = {}

    def fake_stack(keep_list, settings, *, runner=None):
        seen["n"] = len(keep_list.sub_paths)
        return StackResult(
            ok=True,
            engine="dss",
            target=target,
            n_subs=len(keep_list.sub_paths),
            master_path=str(tmp_path / "Autosave.fit"),
            preview_path=None,
            stats={},
            log="",
        )

    monkeypatch.setattr(dss, "stack", fake_stack)

    settings = RefineSettings(
        _env_file=None, data_dir=tmp_path, output_dir=tmp_path, dss_cli="x"
    )
    controller = RefineController.from_settings(settings)
    result = asyncio.run(controller.stack_keep_list(target))
    assert result["ok"] is True
    assert seen["n"] == 2  # resolved from the QA report's keep_list


def test_stack_keep_list_dss_not_configured(tmp_path):
    settings = RefineSettings(
        _env_file=None, data_dir=tmp_path, output_dir=tmp_path, dss_cli=""
    )
    target = "M27"
    sub_dir = tmp_path / target
    sub_dir.mkdir()
    (sub_dir / "m27_sub1.fit").write_bytes(b"x")

    controller = RefineController.from_settings(settings)
    result = asyncio.run(controller.stack_keep_list(target, engine="dss"))
    assert result["ok"] is False
    assert result["error"]


def test_stretch_master_tool_registered():
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "stretch_master" in names


def test_stretch_master_produces_png(tmp_path):
    import numpy as np
    from astropy.io import fits

    d = np.random.default_rng(0).normal(1000, 50, (32, 32)).astype("float32")
    master = tmp_path / "M27_master.fit"
    fits.writeto(master, d)

    settings = RefineSettings(
        _env_file=None, data_dir=tmp_path, output_dir=tmp_path
    )
    controller = RefineController.from_settings(settings)
    result = asyncio.run(controller.stretch_master(str(master)))
    assert result["ok"] is True
    assert result["preview_path"].endswith(".png")
    from pathlib import Path

    assert Path(result["preview_path"]).exists()
    # The invocation was provenance-logged.
    log = (tmp_path / "refine_provenance.jsonl").read_text(encoding="utf-8")
    assert "stretch_master" in log


def test_stack_keep_list_dss_success_auto_preview(tmp_path, monkeypatch):
    import numpy as np
    from astropy.io import fits

    from seestar_refine import dss
    from seestar_refine.dss import StackResult

    target = "M27"
    sub_dir = tmp_path / target
    sub_dir.mkdir()
    (sub_dir / "m27_sub1.fit").write_bytes(b"x")
    (sub_dir / "m27_sub2.fit").write_bytes(b"x")

    # A real FITS master so make_preview can actually load + stretch it.
    d = np.random.default_rng(0).normal(1000, 50, (32, 32)).astype("float32")
    master = tmp_path / "Autosave.fit"
    fits.writeto(master, d)

    canned = StackResult(
        ok=True,
        engine="dss",
        target=target,
        n_subs=2,
        master_path=str(master),
        preview_path=None,
        stats={"min": 0.0, "median": 1.0, "max": 2.0, "shape": [32, 32]},
        log="stacked",
    )

    def fake_stack(keep_list, settings, *, runner=None):
        return canned

    monkeypatch.setattr(dss, "stack", fake_stack)

    settings = RefineSettings(
        _env_file=None,
        data_dir=tmp_path,
        output_dir=tmp_path,
        dss_cli="C:/DSS/DeepSkyStackerCL.exe",
    )
    controller = RefineController.from_settings(settings)
    result = asyncio.run(controller.stack_keep_list(target, engine="dss"))

    assert result["ok"] is True
    assert result["preview_path"]
    from pathlib import Path

    assert Path(result["preview_path"]).exists()


def test_stack_keep_list_wbpp_pixinsight_not_configured(tmp_path):
    # engine="wbpp" now routes to wbpp.run_wbpp; with PixInsight unconfigured it
    # returns a structured not-configured error (never launches anything).
    settings = RefineSettings(
        _env_file=None,
        data_dir=tmp_path,
        output_dir=tmp_path,
        dss_cli="x",
        pixinsight_exe="",
    )
    target = "M27"
    sub_dir = tmp_path / target
    sub_dir.mkdir()
    (sub_dir / "m27_sub1.fit").write_bytes(b"x")

    controller = RefineController.from_settings(settings)
    result = asyncio.run(controller.stack_keep_list(target, engine="wbpp"))
    assert result["ok"] is False
    assert result["engine"] == "wbpp"
    assert "pixinsight" in (result["error"] or "").lower()


def test_prepare_pixinsight_handoff_tool_registered():
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "prepare_pixinsight_handoff" in names


def test_list_masters_tool_registered():
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "list_masters" in names


def test_prepare_pixinsight_handoff_writes_config(tmp_path):
    master = tmp_path / "M27_master.fit"
    master.write_bytes(b"x")
    settings = RefineSettings(
        _env_file=None, data_dir=tmp_path, output_dir=tmp_path
    )
    controller = RefineController.from_settings(settings)
    result = asyncio.run(
        controller.prepare_pixinsight_handoff(str(master), "M27")
    )
    assert result["ok"] is True
    assert result["config"]["target"] == "M27"
    assert result["config"]["channels"]["RGB"] == str(master)
    from pathlib import Path

    assert Path(result["config_path"]).exists()
    # xisf isn't installed → documented FITS fallback, but handoff still ok.
    assert result["xisf"]["ok"] is False
    assert result["xisf"]["fallback"] == "fits"
    # The invocation was provenance-logged.
    log = (tmp_path / "refine_provenance.jsonl").read_text(encoding="utf-8")
    assert "prepare_pixinsight_handoff" in log


def test_list_masters_returns_files(tmp_path):
    settings = RefineSettings(
        _env_file=None, data_dir=tmp_path, output_dir=tmp_path
    )
    (tmp_path / "M27_master.fit").write_bytes(b"x")
    (tmp_path / "M27_master.png").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")  # ignored (not a master pattern)

    controller = RefineController.from_settings(settings)
    result = asyncio.run(controller.list_masters())
    assert result["ok"] is True
    names = {m["name"] for m in result["masters"]}
    assert "M27_master.fit" in names
    assert "M27_master.png" in names
    assert "notes.txt" not in names
    for m in result["masters"]:
        assert "size" in m and "mtime" in m

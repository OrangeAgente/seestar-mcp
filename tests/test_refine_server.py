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


def test_stack_keep_list_wbpp_not_available_until_task4(tmp_path):
    settings = RefineSettings(
        _env_file=None, data_dir=tmp_path, output_dir=tmp_path, dss_cli="x"
    )
    target = "M27"
    sub_dir = tmp_path / target
    sub_dir.mkdir()
    (sub_dir / "m27_sub1.fit").write_bytes(b"x")

    controller = RefineController.from_settings(settings)
    result = asyncio.run(controller.stack_keep_list(target, engine="wbpp"))
    assert result["ok"] is False
    assert "wbpp" in (result["error"] or "").lower()

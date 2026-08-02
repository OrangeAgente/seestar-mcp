"""Unit tests for seestar_refine.server (controller + tool registration)."""

from __future__ import annotations

import asyncio

from seestar_refine.config import RefineSettings
from seestar_refine.server import RefineController, mcp


def test_check_backends_tool_registered():
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "check_backends" in names


def test_check_backends_controller(tmp_path):
    """All three backends absent → all three report False.

    ``pixinsight_mcp_bridge`` is pointed at a non-existent path deliberately: the
    default is ``~/.pixinsight-mcp/bridge``, so without it this asserted a fact
    about the developer's home directory and failed permanently on any machine
    with pixinsight-mcp installed.
    """
    settings = RefineSettings(
        _env_file=None,
        data_dir=tmp_path,
        output_dir=tmp_path,
        pixinsight_mcp_bridge=str(tmp_path / "no-bridge-here"),
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


def test_check_backends_is_independent_of_the_developer_home_dir(tmp_path):
    """The pixinsight-mcp bridge path must be configurable, like the other two.

    ``dss_cli`` and ``pixinsight_exe`` are settings; the bridge path was hardcoded
    to ``~/.pixinsight-mcp/bridge``. That made ``check_backends`` report whatever
    happened to be in the developer's home directory, so this suite failed
    permanently on any machine with pixinsight-mcp installed — and a permanently
    red test trains everyone to read red as normal.
    """
    settings = RefineSettings(
        _env_file=None,
        data_dir=tmp_path,
        output_dir=tmp_path,
        pixinsight_mcp_bridge=str(tmp_path / "no-bridge-here"),
    )
    controller = RefineController.from_settings(settings)
    result = asyncio.run(controller.check_backends())

    assert result["ok"] is True
    assert result["backends"]["pixinsight_mcp"] is False, (
        "an explicitly configured, non-existent bridge path must report False "
        "regardless of what exists under $HOME"
    )


# --- the auto-preview must not crop the target away --------------------------
# Found 2026-08-01 on the first real session: m76_master.png contained no M76.
# pystack._coverage_crop deliberately takes the BOUNDING BOX so an off-centre or
# diagonal object survives; preview.autocrop then applied a largest-INSCRIBED-
# rectangle crop and undid exactly that. Field rotation makes it bite -- the
# inscribed rectangle inside a rotated footprint kept 50% of the canvas and M76
# sat 188 px outside it. See docs/superpowers/specs/
# 2026-08-01-preview-autocrop-discards-off-centre-targets.md


def _rotated_master(path, *, h=240, w=160):
    """A master with a rotated valid footprint and a bright off-centre source.

    Mimics a long alt-az session: the covered region is a rotated rectangle, so
    the largest inscribed axis-aligned rectangle is much narrower than the
    bounding box, and the source sits in the part only the bounding box keeps.
    """
    import numpy as np
    from astropy.io import fits

    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = h / 2.0, w / 2.0
    t = np.deg2rad(20.0)
    # Rotated rectangle covering most of the frame.
    u = (xx - cx) * np.cos(t) + (yy - cy) * np.sin(t)
    v = -(xx - cx) * np.sin(t) + (yy - cy) * np.cos(t)
    covered = (np.abs(u) < w * 0.44) & (np.abs(v) < h * 0.44)

    data = np.where(covered, 100.0, 0.0).astype("float32")
    # Bright compact source near the left edge, inside the footprint but outside
    # the largest inscribed rectangle.
    sy, sx = int(h * 0.62), int(w * 0.12)
    data[sy - 3 : sy + 4, sx - 3 : sx + 4] = 9000.0
    assert covered[sy, sx], "fixture bug: source must be inside the footprint"

    cube = np.stack([data, data, data])  # (3, H, W), as pystack writes
    fits.writeto(path, cube, overwrite=True)
    return (sy, sx)


def test_autocrop_alone_would_discard_the_offcentre_source(tmp_path):
    """Characterisation: this is the behaviour that lost M76."""
    import numpy as np
    from astropy.io import fits

    from seestar_refine import crop as _crop

    master = tmp_path / "rot_master.fit"
    sy, sx = _rotated_master(master)
    rgb = np.moveaxis(fits.getdata(master).astype("float32"), 0, -1)

    _, (r0, r1, c0, c1) = _crop.autocrop(rgb)
    assert not (r0 <= sy < r1 and c0 <= sx < c1), (
        "fixture no longer reproduces the bug: the source is inside the "
        "inscribed rectangle, so this test would pass vacuously"
    )


def test_stack_autopreview_keeps_an_offcentre_target(tmp_path, monkeypatch):
    """The stack's auto-preview must keep what _coverage_crop deliberately kept."""
    import numpy as np
    from PIL import Image

    from seestar_refine import pystack
    from seestar_refine.dss import StackResult

    target = "M76"
    sub_dir = tmp_path / target
    sub_dir.mkdir()
    (sub_dir / "a.fit").write_bytes(b"x")

    master = tmp_path / "m76_master.fit"
    sy, sx = _rotated_master(master)

    def fake_stack(keep_list, settings, **kw):
        return StackResult(
            ok=True, engine="pystack", target=target, n_subs=1,
            master_path=str(master), preview_path=None, stats={}, log="",
        )

    monkeypatch.setattr(pystack, "stack", fake_stack)

    settings = RefineSettings(
        _env_file=None, data_dir=tmp_path, output_dir=tmp_path
    )
    controller = RefineController.from_settings(settings)
    result = asyncio.run(controller.stack_keep_list(target, engine="pystack"))

    assert result["ok"] is True
    assert result["preview_path"], "auto-preview did not run"

    from pathlib import Path as _P

    png = np.asarray(Image.open(_P(result["preview_path"])).convert("L"))
    full_h, full_w = 240, 160
    assert png.shape == (full_h, full_w), (
        f"preview was cropped to {png.shape}; the stacker's bounding-box crop "
        "must not be overridden by an inscribed-rectangle crop"
    )
    # The source must still be visible where it was.
    patch = png[sy - 3 : sy + 4, sx - 3 : sx + 4]
    assert patch.max() > png.mean(), "the off-centre target is missing from the preview"

"""Unit tests for seestar_refine.wbpp (PixInsight WBPP stacking).

The real PixInsight is an external desktop app and is NEVER invoked here — the
``runner`` is faked and the ``pixinsight_exe`` is a dummy file. Tests assert the
buildable logic only (the command/flags, the params file, master location, the
not-configured error, and that the bundled PJSR runner ships).
"""

from __future__ import annotations

from seestar_refine.config import RefineSettings
from seestar_refine.keeplist import KeepList
from seestar_refine.wbpp import (
    _wbpp_runner_path,
    build_wbpp_command,
    run_wbpp,
)


def test_wbpp_runner_script_bundled():
    # The bundled PJSR runner ships inside the package (hatch picks it up).
    path = _wbpp_runner_path()
    assert path.name == "wbpp_runner.js"
    assert path.is_file()


def test_build_wbpp_command_has_pixinsight_flags(tmp_path):
    cmd = build_wbpp_command(
        KeepList("M27", ["/a/s1.fit"]),
        tmp_path,
        pixinsight_exe="C:/PI/PixInsight.exe",
        runner_script="C:/pkg/pjsr/wbpp_runner.js",
        params_path=str(tmp_path / "params.json"),
    )
    assert cmd[0] == "C:/PI/PixInsight.exe"
    assert any(c.startswith("-r=") for c in cmd)
    assert "--automation-mode" in cmd
    assert "--force-exit" in cmd
    # The params path is passed to the runner somewhere on the command line.
    assert any("params.json" in c for c in cmd)


def test_run_wbpp_requires_pixinsight(tmp_path):
    r = run_wbpp(
        KeepList("M27", ["/a/s1.fit"]),
        RefineSettings(_env_file=None, pixinsight_exe="", output_dir=tmp_path),
    )
    assert r.ok is False
    assert r.engine == "wbpp"
    assert "pixinsight" in (r.error or "").lower()


def test_run_wbpp_missing_exe_is_error(tmp_path):
    # A configured-but-nonexistent path must NOT be executed.
    r = run_wbpp(
        KeepList("M27", ["/a/s1.fit"]),
        RefineSettings(
            _env_file=None,
            pixinsight_exe=str(tmp_path / "nope" / "PixInsight.exe"),
            output_dir=tmp_path,
        ),
    )
    assert r.ok is False
    assert "pixinsight" in (r.error or "").lower()


def test_run_wbpp_builds_command_and_locates_master(tmp_path):
    exe = tmp_path / "PixInsight.exe"
    exe.write_bytes(b"x")
    calls = {}

    def fake(cmd, **kw):
        calls["cmd"] = cmd
        calls["kw"] = kw
        (tmp_path / "masterLight.xisf").write_bytes(b"x")  # simulate WBPP output

        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return R()

    r = run_wbpp(
        KeepList("M27", ["/a/s1.fit"]),
        RefineSettings(
            _env_file=None, pixinsight_exe=str(exe), output_dir=tmp_path
        ),
        runner=fake,
    )
    assert any("PixInsight" in str(c) for c in calls["cmd"])
    # Subprocess isolation kwargs mirror the DSS path.
    assert calls["kw"].get("capture_output") is True
    assert calls["kw"].get("text") is True
    assert "timeout" in calls["kw"]
    # A params JSON was written for the runner to read.
    assert list(tmp_path.glob("*_wbpp_params.json"))
    assert r.ok is True
    assert r.engine == "wbpp"
    assert r.master_path.endswith("masterLight.xisf")


def test_run_wbpp_nonzero_exit_is_error(tmp_path):
    exe = tmp_path / "PixInsight.exe"
    exe.write_bytes(b"x")

    def fake(cmd, **kw):
        class R:
            returncode = 1
            stdout = ""
            stderr = "boom"

        return R()

    r = run_wbpp(
        KeepList("M27", ["/a/s1.fit"]),
        RefineSettings(
            _env_file=None, pixinsight_exe=str(exe), output_dir=tmp_path
        ),
        runner=fake,
    )
    assert r.ok is False
    assert r.error

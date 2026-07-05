"""Unit tests for seestar_refine.dss (file-list build + DSS run + stack).

Real DeepSkyStacker is a Windows desktop app and is NEVER invoked here: every
subprocess is a fake ``runner`` injected into the pure command-building /
output-locating logic. Only the buildable logic is asserted; the exact DSS
file-list / CLI-flag strings are ``# FORMAT-DEPENDENT`` and validated on-machine.
"""

from __future__ import annotations


def test_build_file_list_lists_all_subs():
    from seestar_refine.dss import build_file_list
    from seestar_refine.keeplist import KeepList

    txt = build_file_list(KeepList("M27", ["/a/s1.fit", "/a/s2.fit"]))
    assert "s1.fit" in txt and "s2.fit" in txt


def test_build_file_list_includes_darks_and_flats():
    from seestar_refine.dss import build_file_list
    from seestar_refine.keeplist import KeepList

    txt = build_file_list(
        KeepList("M27", ["/a/s1.fit"]),
        dark_paths=["/a/dark1.fit"],
        flat_paths=["/a/flat1.fit"],
    )
    assert "s1.fit" in txt
    assert "dark1.fit" in txt
    assert "flat1.fit" in txt


def test_run_dss_builds_command_and_locates_master(tmp_path):
    from seestar_refine.dss import run_dss

    calls = {}

    def fake_runner(cmd, **kw):
        calls["cmd"] = cmd
        calls["kw"] = kw
        (tmp_path / "Autosave.tif").write_bytes(b"x")  # simulate DSS output

        class R:
            returncode = 0
            stdout = "stacked"
            stderr = ""

        return R()

    r = run_dss(
        str(tmp_path / "list.txt"),
        tmp_path,
        dss_cli="C:/DSS/DeepSkyStackerCL.exe",
        runner=fake_runner,
    )
    assert r["ok"] is True
    assert r["master_path"].endswith("Autosave.tif")
    assert r["returncode"] == 0
    assert any("DeepSkyStackerCL" in str(c) for c in calls["cmd"])
    # The configured CLI + the /L file-list flag must be on the command line.
    assert calls["cmd"][0] == "C:/DSS/DeepSkyStackerCL.exe"
    assert "/L" in calls["cmd"]
    # subprocess.run kwargs the injectable runner is called with.
    assert calls["kw"].get("capture_output") is True
    assert calls["kw"].get("text") is True
    assert "timeout" in calls["kw"]


def test_run_dss_nonzero_exit_is_error(tmp_path):
    from seestar_refine.dss import run_dss

    def fake(cmd, **kw):
        class R:
            returncode = 1
            stdout = ""
            stderr = "boom"

        return R()

    r = run_dss(str(tmp_path / "l.txt"), tmp_path, dss_cli="x", runner=fake)
    assert r["ok"] is False
    assert "boom" in (r["log"] or "")
    assert r["returncode"] == 1


def test_run_dss_no_master_found_is_error(tmp_path):
    from seestar_refine.dss import run_dss

    def fake(cmd, **kw):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        return R()  # returncode 0 but writes no Autosave/master

    r = run_dss(str(tmp_path / "l.txt"), tmp_path, dss_cli="x", runner=fake)
    assert r["ok"] is False
    assert r["master_path"] is None


def test_run_dss_timeout_is_error(tmp_path):
    import subprocess

    from seestar_refine.dss import run_dss

    def fake(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))

    r = run_dss(str(tmp_path / "l.txt"), tmp_path, dss_cli="x", runner=fake)
    assert r["ok"] is False
    assert "timeout" in (r["error"] or "").lower()


def test_stack_writes_file_list_and_returns_stats(tmp_path):
    import numpy as np
    from astropy.io import fits

    from seestar_refine.config import RefineSettings
    from seestar_refine.dss import stack
    from seestar_refine.keeplist import KeepList

    out = tmp_path / "refine"

    def fake_runner(cmd, **kw):
        # DSS "produces" a small FITS master in the output dir.
        data = np.arange(16, dtype="float32").reshape(4, 4)
        fits.writeto(out / "Autosave.fit", data)

        class R:
            returncode = 0
            stdout = "stacked ok"
            stderr = ""

        return R()

    settings = RefineSettings(
        _env_file=None,
        data_dir=tmp_path,
        output_dir=out,
        dss_cli="C:/DSS/DeepSkyStackerCL.exe",
    )
    kl = KeepList("M27", ["/a/s1.fit", "/a/s2.fit"])
    result = stack(kl, settings, runner=fake_runner)

    assert result.ok is True
    assert result.engine == "dss"
    assert result.target == "M27"
    assert result.n_subs == 2
    assert result.master_path is not None and result.master_path.endswith(".fit")
    assert result.preview_path is None
    assert result.stats.get("shape") == [4, 4]
    assert result.stats.get("max") == 15.0
    # The DSS file list was written into the output dir.
    lists = list(out.glob("*.txt"))
    assert lists, "expected a DSS file list written to the output dir"
    text = lists[0].read_text(encoding="utf-8")
    assert "s1.fit" in text and "s2.fit" in text


def test_stack_dss_not_configured_returns_error():
    from seestar_refine.config import RefineSettings
    from seestar_refine.dss import stack
    from seestar_refine.keeplist import KeepList

    settings = RefineSettings(_env_file=None, dss_cli="")
    result = stack(KeepList("M27", ["/a/s1.fit"]), settings)
    assert result.ok is False
    assert result.engine == "dss"
    assert "SEESTAR_REFINE_DSS_CLI" in (result.error or "")


def test_stack_non_fits_master_has_empty_stats(tmp_path):
    from seestar_refine.config import RefineSettings
    from seestar_refine.dss import stack
    from seestar_refine.keeplist import KeepList

    out = tmp_path / "refine"

    def fake_runner(cmd, **kw):
        (out / "Autosave.tif").write_bytes(b"not a real tiff")

        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return R()

    settings = RefineSettings(
        _env_file=None, data_dir=tmp_path, output_dir=out, dss_cli="x"
    )
    result = stack(KeepList("M27", ["/a/s1.fit"]), settings, runner=fake_runner)
    assert result.ok is True
    assert result.master_path.endswith(".tif")
    assert result.stats == {}  # non-FITS master → guarded to empty stats

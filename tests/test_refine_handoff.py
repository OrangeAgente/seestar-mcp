"""Unit tests for seestar_refine.handoff (pixinsight-mcp config + XISF prep).

No real PixInsight or pixinsight-mcp; ``xisf`` is intentionally NOT a dependency,
so ``to_xisf`` must degrade to the documented FITS fallback.
"""

from __future__ import annotations

import json

from seestar_refine.handoff import to_xisf, write_pixinsight_config


def test_write_pixinsight_config(tmp_path):
    master = tmp_path / "M27_master.fit"
    master.write_bytes(b"x")

    r = write_pixinsight_config(master, "M27", tmp_path)
    assert r["ok"] is True
    cfg = r["config"]
    assert cfg["target"] == "M27"
    # A single OSC master fills the RGB/color slot with an absolute path.
    assert cfg["channels"]["RGB"] == str(master)
    assert cfg["output_dir"] == str(tmp_path)

    # The config was written to <output>/<target>_pixinsight.json and round-trips.
    config_path = r["config_path"]
    assert config_path.endswith("_pixinsight.json")
    on_disk = json.loads(open(config_path, encoding="utf-8").read())
    assert on_disk["target"] == "M27"
    assert on_disk["channels"]["RGB"] == str(master)


def test_write_pixinsight_config_never_raises():
    # A garbage output dir (None) must degrade to a structured error, not raise.
    r = write_pixinsight_config("/x/master.fit", "M27", None)
    assert r["ok"] is False
    assert r["error"]


def test_to_xisf_without_package_falls_back(tmp_path):
    # xisf is not a dependency in this task, so conversion degrades to FITS.
    master = tmp_path / "M27_master.fit"
    master.write_bytes(b"x")
    r = to_xisf(master, tmp_path / "M27_master.xisf")
    assert r["ok"] is False
    assert r["fallback"] == "fits"
    assert "xisf" in r["error"].lower()

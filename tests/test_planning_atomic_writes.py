"""The shared JSON stores must survive an interrupted write.

``projects.json`` accumulates every session's integration across months, and the
site profile and learned horizon mask are likewise irreplaceable local state. All
three were written by truncating the file and then serializing into it, so a crash
(or a second process writing concurrently) could leave a half-written file — and
the truncate happens *first*, meaning the old contents are already gone when the
failure hits.

These tests pin the durability property: a failure mid-write must leave the
previous file intact and parseable, and must not litter temp files.
"""

from __future__ import annotations

import json

import pytest

from seestar_mcp.planning.obstructions import SkyBin, load_sky_log, save_sky_log
from seestar_mcp.planning.projects import (
    Project,
    load_projects,
    save_projects,
)
from seestar_mcp.planning.site import SiteProfile, load_site, save_site

NOW = "2026-07-31T04:00:00Z"


def _project(target_id: str, collected: float) -> Project:
    return Project(
        target_id=target_id,
        target_name=target_id,
        goal_minutes=0.0,
        collected_minutes=collected,
        status="active",
        created_utc=NOW,
        updated_utc=NOW,
        sessions=[],
    )


def test_projects_survive_a_failed_write(tmp_path, monkeypatch):
    """A crash mid-write must not destroy the existing project history."""
    p = tmp_path / "projects.json"
    save_projects({"M31": _project("M31", 84.0)}, path=p)
    assert load_projects(p)["M31"].collected_minutes == 84.0

    # Simulate the process dying partway through serialization.
    def boom(*_args, **_kwargs):
        raise RuntimeError("process died mid-write")

    monkeypatch.setattr(json, "dump", boom)
    with pytest.raises(RuntimeError):
        save_projects({"M31": _project("M31", 999.0)}, path=p)
    monkeypatch.undo()

    # The old history must still be there and still parse.
    recovered = load_projects(p)
    assert recovered["M31"].collected_minutes == 84.0
    # And no temp file left behind.
    assert [f.name for f in tmp_path.iterdir()] == ["projects.json"]


def test_site_profile_survives_a_failed_write(tmp_path, monkeypatch):
    p = tmp_path / "site.json"
    save_site(SiteProfile(name="Yard", lat_deg=40.0, lon_deg=-74.0, bortle=6), path=p)

    def boom(*_args, **_kwargs):
        raise RuntimeError("process died mid-write")

    monkeypatch.setattr(json, "dump", boom)
    with pytest.raises(RuntimeError):
        save_site(SiteProfile(name="Wrong", lat_deg=0.0, lon_deg=0.0), path=p)
    monkeypatch.undo()

    assert load_site(p).name == "Yard"
    assert [f.name for f in tmp_path.iterdir()] == ["site.json"]


def test_sky_log_survives_a_failed_write(tmp_path, monkeypatch):
    p = tmp_path / "sky.json"
    save_sky_log({"90,20": SkyBin(az_bin=90, alt_bin=20, attempts=5, failures=4)}, path=p)

    def boom(*_args, **_kwargs):
        raise RuntimeError("process died mid-write")

    monkeypatch.setattr(json, "dump", boom)
    with pytest.raises(RuntimeError):
        save_sky_log({"0,0": SkyBin(az_bin=0, alt_bin=0, attempts=1, failures=1)}, path=p)
    monkeypatch.undo()

    recovered = load_sky_log(p)
    assert recovered["90,20"].failures == 4
    assert [f.name for f in tmp_path.iterdir()] == ["sky.json"]

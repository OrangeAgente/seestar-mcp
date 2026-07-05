"""Tests for the projects/history store (accumulation, ranking, recency)."""

from seestar_mcp.planning.projects import (
    Project,
    get_project,
    load_projects,
    log_session_result,
    recommend_projects,
    save_projects,
    upsert_project,
    was_recently_imaged,
)

NOW = "2026-07-05T04:00:00Z"


def test_missing_store_is_empty(tmp_path):
    assert load_projects(tmp_path / "p.json") == {}


def test_log_accumulates_and_completes(tmp_path):
    p = tmp_path / "p.json"
    upsert_project("M31", "Andromeda", goal_minutes=60, now_utc=NOW, path=p)
    log_session_result(
        "M31", "Andromeda", integration_minutes=25, subs_total=160, subs_kept=150,
        now_utc=NOW, path=p,
    )
    proj = get_project("M31", path=p)
    assert proj.collected_minutes == 25 and proj.status == "active" and len(proj.sessions) == 1
    log_session_result(
        "M31", "Andromeda", integration_minutes=40, subs_total=250, subs_kept=240,
        now_utc="2026-07-06T04:00:00Z", path=p,
    )
    assert get_project("M31", path=p).collected_minutes == 65
    assert get_project("M31", path=p).status == "complete"  # >= 60 goal


def test_recommend_orders_by_remaining(tmp_path):
    p = tmp_path / "p.json"
    upsert_project("M31", "Andromeda", goal_minutes=360, now_utc=NOW, path=p)
    log_session_result(
        "M31", "Andromeda", integration_minutes=60, subs_total=1, subs_kept=1,
        now_utc=NOW, path=p,
    )  # 300 remaining
    upsert_project("M42", "Orion", goal_minutes=120, now_utc=NOW, path=p)
    log_session_result(
        "M42", "Orion", integration_minutes=30, subs_total=1, subs_kept=1,
        now_utc=NOW, path=p,
    )  # 90 remaining
    recs = recommend_projects(path=p)
    assert [r.target_id for r in recs] == ["M31", "M42"]  # most-needed first


def test_recently_imaged_boundary(tmp_path):
    p = tmp_path / "p.json"
    log_session_result(
        "M13", "Hercules", integration_minutes=10, subs_total=1, subs_kept=1,
        now_utc="2026-07-04T04:00:00Z", path=p,
    )
    assert was_recently_imaged("M13", 2, now_utc="2026-07-05T04:00:00Z", path=p) is True
    assert was_recently_imaged("M13", 2, now_utc="2026-07-08T04:00:00Z", path=p) is False


def test_roundtrip_preserves_sessions(tmp_path):
    p = tmp_path / "p.json"
    log_session_result(
        "M31", "Andromeda", integration_minutes=25, subs_total=160, subs_kept=150,
        median_fwhm=2.4, notes="clear", now_utc=NOW, path=p,
    )
    projects = load_projects(p)
    save_projects(projects, p)
    proj = get_project("M31", path=p)
    assert isinstance(proj, Project)
    assert proj.sessions[0].median_fwhm == 2.4
    assert proj.sessions[0].notes == "clear"


def test_corrupt_store_is_empty(tmp_path):
    p = tmp_path / "p.json"
    p.write_text("{ not valid json", encoding="utf-8")
    assert load_projects(p) == {}

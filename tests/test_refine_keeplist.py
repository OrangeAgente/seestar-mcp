"""Unit tests for seestar_refine.keeplist (parse qa_session_report → KeepList)."""

from __future__ import annotations

import json

from seestar_refine.keeplist import KeepList, load_keep_list


def test_load_keep_list_from_report(tmp_path):
    (tmp_path / "m27_sub1.fit").write_bytes(b"x")
    (tmp_path / "m27_sub2.fit").write_bytes(b"x")
    rep = tmp_path / "qa.json"
    rep.write_text(
        json.dumps(
            {
                "target": "M27",
                "keep_list": ["m27_sub1.fit", "m27_sub2.fit", "missing.fit"],
            }
        )
    )
    kl = load_keep_list(rep, data_dir=tmp_path)
    assert kl.target == "M27"
    assert len(kl.sub_paths) == 2
    assert all(p.endswith(".fit") for p in kl.sub_paths)


def test_load_keep_list_from_dict(tmp_path):
    (tmp_path / "good.fit").write_bytes(b"x")
    kl = load_keep_list(
        {"target": "M31", "keep_list": ["good", "gone"]}, data_dir=tmp_path
    )
    assert kl.target == "M31"
    # "good" resolves via the .fit suffix fallback; "gone" drops.
    assert len(kl.sub_paths) == 1
    assert kl.sub_paths[0].endswith("good.fit")


def test_missing_file_source_is_empty(tmp_path):
    kl = load_keep_list(tmp_path / "nope.json", data_dir=tmp_path)
    assert isinstance(kl, KeepList)
    assert kl.target == ""
    assert kl.sub_paths == []


def test_garbage_source_is_empty(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    kl = load_keep_list(bad, data_dir=tmp_path)
    assert kl.target == "" and kl.sub_paths == []
    # A non-dict, non-path source never raises either.
    assert load_keep_list(12345, data_dir=tmp_path).sub_paths == []

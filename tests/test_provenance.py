"""Tests for seestar_mcp.provenance."""

from __future__ import annotations

import json

from seestar_mcp.provenance import (
    ProvenanceLog,
    SessionManifest,
    hash_fits,
    redact,
)

REDACTED = "***REDACTED***"


def test_redact_masks_secret_keys_at_any_depth():
    obj = {
        "user": "alice",
        "password": "hunter2",
        "nested": {"api_token": "abc", "ok": 1},
        "list": [{"rsa_private_key": "xxx"}, {"keep": "me"}],
    }
    out = redact(obj)
    assert out["user"] == "alice"
    assert out["password"] == REDACTED
    assert out["nested"]["api_token"] == REDACTED
    assert out["nested"]["ok"] == 1
    assert out["list"][0]["rsa_private_key"] == REDACTED
    assert out["list"][1]["keep"] == "me"


def test_redact_is_case_insensitive_and_substring():
    out = redact({"AUTH": "x", "MySecretValue": "y", "credential_id": "z"})
    assert out["AUTH"] == REDACTED
    assert out["MySecretValue"] == REDACTED
    assert out["credential_id"] == REDACTED


def test_redact_does_not_mutate_input():
    src = {"password": "p"}
    redact(src)
    assert src["password"] == "p"


def test_log_call_appends_parseable_line_with_ts_and_tool(tmp_path):
    log = ProvenanceLog(tmp_path / "prov.jsonl")
    rec = log.log_call(tool="get_status", args={"device": 0})
    log.log_call(tool="park", args={})
    lines = (tmp_path / "prov.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["tool"] == "get_status"
    assert "ts" in first
    assert first["args"] == {"device": 0}
    # None fields omitted
    assert "note" not in first
    assert "fits_hash" not in first
    assert rec == first


def test_log_call_redacts_secrets_in_written_line(tmp_path):
    log = ProvenanceLog(tmp_path / "prov.jsonl")
    log.log_call(tool="connect", args={"password": "hunter2", "host": "1.2.3.4"})
    line = (tmp_path / "prov.jsonl").read_text().strip()
    assert "hunter2" not in line
    rec = json.loads(line)
    assert rec["args"]["password"] == REDACTED
    assert rec["args"]["host"] == "1.2.3.4"


def test_log_call_keeps_optional_fields_when_present(tmp_path):
    log = ProvenanceLog(tmp_path / "prov.jsonl")
    rec = log.log_call(
        tool="action",
        args={},
        request="PUT /action",
        client_txn_id=1,
        server_txn_id=99,
        response_code=200,
        fits_hash="sha256:abc",
        note="hi",
    )
    assert rec["client_txn_id"] == 1
    assert rec["server_txn_id"] == 99
    assert rec["response_code"] == 200
    assert rec["fits_hash"] == "sha256:abc"
    assert rec["note"] == "hi"
    assert rec["request"] == "PUT /action"


def test_hash_fits_stable_for_identical_bytes(tmp_path):
    data = b"hello fits"
    h1 = hash_fits(data)
    h2 = hash_fits(data)
    assert h1 == h2
    assert h1.startswith("sha256:")
    p = tmp_path / "a.fit"
    p.write_bytes(data)
    assert hash_fits(p) == h1


def test_hash_fits_differs_for_different_bytes():
    assert hash_fits(b"a") != hash_fits(b"b")


def test_session_manifest_write(tmp_path):
    m = SessionManifest(
        session_id="sess1",
        manifest_dir=tmp_path / "manifests",
        target="M31",
        config_summary={"alpaca": "x", "rsa_key": "SECRET"},
    )
    m.set_meta(mount_mode="alt-az", filter="LP")
    m.add_verdict("sub1.fit", "PASS", {"fwhm": 2.1})
    m.add_verdict("sub2.fit", "REJECT", {"fwhm": 5.0})
    m.set_keep_list(["sub1.fit"])
    path = m.write()
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["session_id"] == "sess1"
    assert data["target"] == "M31"
    assert "created_at" in data
    assert data["config_summary"]["rsa_key"] == REDACTED
    assert data["config_summary"]["alpaca"] == "x"
    assert data["meta"]["mount_mode"] == "alt-az"
    assert data["verdicts"]["sub1.fit"]["verdict"] == "PASS"
    assert data["keep_list"] == ["sub1.fit"]

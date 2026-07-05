"""Tests for seestar_mcp.secrets (least-privilege secret loader).

The store must never persist a secret in config/logs, never expose a value via
``repr``/``str``/``status``, and resolve env-before-file with graceful misses.
"""

from __future__ import annotations

from seestar_mcp.secrets import SecretStore

SECRET_VALUE = "-----BEGIN RSA PRIVATE KEY-----\nSUPERSECRET\n-----END RSA PRIVATE KEY-----"


def test_get_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SEESTAR_SECRET_MY_TOKEN", "env-token-value")
    store = SecretStore(base_dir=tmp_path)
    assert store.get("my_token") == "env-token-value"
    assert store.has("my_token") is True


def test_get_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("SEESTAR_SECRET_MY_TOKEN", raising=False)
    (tmp_path / "my_token").write_text("  file-token-value  \n", encoding="utf-8")
    store = SecretStore(base_dir=tmp_path)
    # File value is stripped.
    assert store.get("my_token") == "file-token-value"


def test_env_precedence_over_file(tmp_path, monkeypatch):
    (tmp_path / "my_token").write_text("file-value", encoding="utf-8")
    monkeypatch.setenv("SEESTAR_SECRET_MY_TOKEN", "env-value")
    store = SecretStore(base_dir=tmp_path)
    assert store.get("my_token") == "env-value"


def test_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("SEESTAR_SECRET_NOPE", raising=False)
    store = SecretStore(base_dir=tmp_path)
    assert store.get("nope") is None
    assert store.has("nope") is False


def test_rsa_key_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEESTAR_SECRET_RSA_PRIVATE_KEY", SECRET_VALUE)
    store = SecretStore(base_dir=tmp_path)
    assert store.get_rsa_private_key() == SECRET_VALUE
    assert store.has_rsa_key() is True


def test_rsa_key_from_key_file_path(tmp_path, monkeypatch):
    monkeypatch.delenv("SEESTAR_SECRET_RSA_PRIVATE_KEY", raising=False)
    key_file = tmp_path / "somewhere" / "id_rsa.pem"
    key_file.parent.mkdir(parents=True)
    key_file.write_text(SECRET_VALUE + "\n", encoding="utf-8")
    monkeypatch.setenv("SEESTAR_SECRET_RSA_KEY_FILE", str(key_file))
    store = SecretStore(base_dir=tmp_path)
    assert store.get_rsa_private_key() == SECRET_VALUE


def test_rsa_key_from_default_pem(tmp_path, monkeypatch):
    monkeypatch.delenv("SEESTAR_SECRET_RSA_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("SEESTAR_SECRET_RSA_KEY_FILE", raising=False)
    (tmp_path / "rsa_private_key.pem").write_text(SECRET_VALUE, encoding="utf-8")
    store = SecretStore(base_dir=tmp_path)
    assert store.get_rsa_private_key() == SECRET_VALUE


def test_status_reports_presence_not_value(tmp_path, monkeypatch):
    monkeypatch.setenv("SEESTAR_SECRET_RSA_PRIVATE_KEY", SECRET_VALUE)
    store = SecretStore(base_dir=tmp_path)
    status = store.status()
    assert status == {"rsa_private_key": "present"}
    # The secret value must never appear in the status report.
    assert SECRET_VALUE not in str(status)
    assert "SUPERSECRET" not in str(status)


def test_status_absent_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("SEESTAR_SECRET_RSA_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("SEESTAR_SECRET_RSA_KEY_FILE", raising=False)
    store = SecretStore(base_dir=tmp_path)
    assert store.status() == {"rsa_private_key": "absent"}


def test_repr_and_str_never_leak_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("SEESTAR_SECRET_RSA_PRIVATE_KEY", SECRET_VALUE)
    store = SecretStore(base_dir=tmp_path)
    for rendered in (repr(store), str(store)):
        assert "SUPERSECRET" not in rendered
        assert SECRET_VALUE not in rendered
        assert "rsa_private_key=present" in rendered

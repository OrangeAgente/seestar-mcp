"""Tests for seestar_mcp.config.Settings."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from seestar_mcp.config import Settings, get_settings


def test_defaults_match_spec():
    # _env_file=None so a developer's local .env (e.g. pointing at a real scope)
    # cannot override the code defaults this test asserts.
    s = Settings(_env_file=None)
    assert s.alpaca_base_url == "http://127.0.0.1:5555"
    assert s.alpaca_device_num == 1
    assert s.seestar_host == "127.0.0.1"
    assert s.jsonrpc_port == 4700
    assert s.http_port == 80
    assert s.smb_port == 445
    assert s.bind_host == "127.0.0.1"
    assert s.data_dir == Path("./data")
    assert s.provenance_log == Path("./data/provenance.jsonl")
    assert s.manifest_dir == Path("./data/manifests")
    assert s.http_timeout_s == 30.0


def test_qa_threshold_defaults():
    s = Settings()
    assert s.qa_fwhm_sigma == 1.5
    assert s.qa_fwhm_marginal_sigma == 1.0
    assert s.qa_eccentricity_reject == 0.575
    assert s.qa_eccentricity_marginal == 0.42
    assert s.qa_snr_floor_factor == 0.5
    assert s.qa_starcount_floor_factor == 0.5
    assert s.qa_fwhm_absolute is None
    assert s.qa_eccentricity_absolute is None


def test_env_override(monkeypatch):
    monkeypatch.setenv("SEESTAR_ALPACA_BASE_URL", "http://10.0.0.5:5555")
    monkeypatch.setenv("SEESTAR_ALPACA_DEVICE_NUM", "3")
    s = Settings()
    assert s.alpaca_base_url == "http://10.0.0.5:5555"
    assert s.alpaca_device_num == 3


def test_no_secret_fields_present():
    # Guard: config must never carry secret material.
    field_names = set(Settings.model_fields)
    for forbidden in ("password", "secret", "token", "rsa", "private_key", "credential"):
        assert not any(forbidden in name for name in field_names), forbidden


@pytest.mark.parametrize("host", ["127.0.0.1", "127.5.5.5", "10.0.0.4", "172.16.3.9", "192.168.1.50", "localhost", "seestar.local"])
def test_private_or_loopback_bind_host_no_warning(host):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        Settings(bind_host=host)  # must not raise/warn


@pytest.mark.parametrize("host", ["8.8.8.8", "1.2.3.4", "93.184.216.34"])
def test_public_bind_host_warns(host):
    with pytest.warns(UserWarning):
        Settings(bind_host=host)


def test_public_bind_host_does_not_raise():
    # Warn, never raise.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s = Settings(bind_host="8.8.8.8")
    assert s.bind_host == "8.8.8.8"


def test_get_settings_is_cached():
    a = get_settings()
    b = get_settings()
    assert a is b

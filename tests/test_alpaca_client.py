"""Tests for seestar_mcp.alpaca_client.

Uses respx to mock the httpx AsyncClient talking to a fake seestar_alp
ASCOM Alpaca server. Because pytest is configured with ``asyncio_mode=auto``,
async tests need no decorator.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from seestar_mcp.alpaca_client import (
    AlpacaActionNotImplemented,
    AlpacaClient,
    AlpacaError,
    AlpacaNotImplemented,
    AlpacaTransportError,
)
from seestar_mcp.provenance import ProvenanceLog

BASE_URL = "http://testserver:5555"
API = "/api/v1/telescope/0"


def _envelope(value, *, error_number=0, error_message="", ctid=0, stid=99):
    return {
        "Value": value,
        "ClientTransactionID": ctid,
        "ServerTransactionID": stid,
        "ErrorNumber": error_number,
        "ErrorMessage": error_message,
    }


def _make_client(**kwargs):
    client = httpx.AsyncClient(base_url=BASE_URL)
    return AlpacaClient(BASE_URL, 0, client=client, **kwargs), client


@respx.mock
async def test_get_property_success_carries_client_and_txn_ids():
    route = respx.get(f"{BASE_URL}{API}/connected").mock(
        return_value=httpx.Response(200, json=_envelope(True))
    )
    ac, _ = _make_client()
    result = await ac.get_property("connected")
    assert result is True

    request = route.calls.last.request
    qs = parse_qs(request.url.query.decode())
    assert qs["ClientID"] == ["1"]
    assert qs["ClientTransactionID"] == ["1"]
    await ac.aclose()


@respx.mock
async def test_not_implemented_raises():
    respx.get(f"{BASE_URL}{API}/siteelevation").mock(
        return_value=httpx.Response(
            200, json=_envelope(None, error_number=1024, error_message="Not implemented")
        )
    )
    ac, _ = _make_client()
    with pytest.raises(AlpacaNotImplemented) as exc_info:
        await ac.get_property("siteelevation")
    assert exc_info.value.error_number == 1024
    assert exc_info.value.verb == "siteelevation"
    await ac.aclose()


@respx.mock
async def test_action_not_implemented_raises():
    respx.put(f"{BASE_URL}{API}/action").mock(
        return_value=httpx.Response(
            200,
            json=_envelope(None, error_number=1036, error_message="Action not implemented"),
        )
    )
    ac, _ = _make_client()
    with pytest.raises(AlpacaActionNotImplemented) as exc_info:
        await ac.invoke_action("bogus_action")
    assert exc_info.value.error_number == 1036
    await ac.aclose()


@respx.mock
async def test_generic_error_raises_with_number_and_message():
    respx.put(f"{BASE_URL}{API}/tracking").mock(
        return_value=httpx.Response(
            200, json=_envelope(None, error_number=1025, error_message="Invalid value")
        )
    )
    ac, _ = _make_client()
    with pytest.raises(AlpacaError) as exc_info:
        await ac.set_tracking(True)
    err = exc_info.value
    assert err.error_number == 1025
    assert err.error_message == "Invalid value"
    assert "1025" in str(err)
    assert "Invalid value" in str(err)
    # Not one of the specialized subclasses.
    assert not isinstance(err, AlpacaNotImplemented)
    assert not isinstance(err, AlpacaActionNotImplemented)
    await ac.aclose()


@respx.mock
async def test_method_sync_builds_correct_body():
    route = respx.put(f"{BASE_URL}{API}/action").mock(
        return_value=httpx.Response(200, json=_envelope({"state": "idle"}))
    )
    ac, _ = _make_client()
    result = await ac.method_sync("get_view_state")
    assert result == {"state": "idle"}

    body = parse_qs(route.calls.last.request.content.decode())
    assert body["Action"] == ["method_sync"]
    params = json.loads(body["Parameters"][0])
    assert params == {"method": "get_view_state", "params": []}
    assert body["ClientID"] == ["1"]
    assert "ClientTransactionID" in body
    await ac.aclose()


@respx.mock
async def test_client_transaction_id_increments():
    route = respx.get(f"{BASE_URL}{API}/connected").mock(
        return_value=httpx.Response(200, json=_envelope(True))
    )
    ac, _ = _make_client()
    await ac.get_property("connected")
    await ac.get_property("connected")
    await ac.get_property("connected")

    ctids = [
        parse_qs(call.request.url.query.decode())["ClientTransactionID"][0]
        for call in route.calls
    ]
    assert ctids == ["1", "2", "3"]
    await ac.aclose()


@respx.mock
async def test_provenance_record_written(tmp_path):
    respx.get(f"{BASE_URL}{API}/connected").mock(
        return_value=httpx.Response(200, json=_envelope(True, stid=4242))
    )
    prov_path = tmp_path / "prov.jsonl"
    prov = ProvenanceLog(prov_path)
    ac, _ = _make_client(provenance=prov)
    await ac.get_property("connected")
    await ac.aclose()

    lines = prov_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["tool"] == "alpaca.get.connected"
    assert record["server_txn_id"] == 4242
    assert record["response_code"] == 0
    assert record["client_txn_id"] == 1


@respx.mock
async def test_aclose_closes_owned_but_not_injected_client():
    # Injected client: must NOT be closed by AlpacaClient.aclose().
    injected = httpx.AsyncClient(base_url=BASE_URL)
    ac_injected = AlpacaClient(BASE_URL, 0, client=injected)
    await ac_injected.aclose()
    assert injected.is_closed is False
    await injected.aclose()

    # Owned client: aclose() closes it.
    ac_owned = AlpacaClient(BASE_URL, 0)
    owned = ac_owned._client
    await ac_owned.aclose()
    assert owned.is_closed is True


@respx.mock
async def test_context_manager_closes_owned_client():
    async with AlpacaClient(BASE_URL, 0) as ac:
        owned = ac._client
        assert owned.is_closed is False
    assert owned.is_closed is True


@respx.mock
async def test_from_settings_builds_client():
    from seestar_mcp.config import Settings

    settings = Settings(alpaca_base_url=BASE_URL, alpaca_device_num=0)
    ac = AlpacaClient.from_settings(settings)
    assert ac.base_url == BASE_URL
    assert ac.device_num == 0
    await ac.aclose()


@respx.mock
async def test_get_connected_and_boolean_wrappers():
    respx.get(f"{BASE_URL}{API}/connected").mock(
        return_value=httpx.Response(200, json=_envelope(True))
    )
    route_put = respx.put(f"{BASE_URL}{API}/connected").mock(
        return_value=httpx.Response(200, json=_envelope(None))
    )
    ac, _ = _make_client()
    assert await ac.get_connected() is True
    await ac.set_connected(True)
    body = parse_qs(route_put.calls.last.request.content.decode())
    # ASCOM convention: capitalized True/False strings.
    assert body["Connected"] == ["True"]
    await ac.aclose()


@respx.mock
async def test_bridge_down_raises_transport_error(tmp_path):
    # seestar_alp bridge unreachable: httpx raises a transport error, which we
    # must surface as AlpacaTransportError and still record in provenance.
    respx.get(f"{BASE_URL}{API}/connected").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    prov_path = tmp_path / "prov.jsonl"
    prov = ProvenanceLog(prov_path)
    ac, _ = _make_client(provenance=prov)
    with pytest.raises(AlpacaTransportError) as exc_info:
        await ac.get_property("connected")
    assert exc_info.value.error_number == -1
    await ac.aclose()

    lines = prov_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert "transport error" in record["note"]
    assert record.get("response_code") is None


@respx.mock
async def test_bridge_down_on_action_raises_transport_error():
    respx.put(f"{BASE_URL}{API}/action").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    ac, _ = _make_client()
    with pytest.raises(AlpacaTransportError):
        await ac.method_sync("get_view_state")
    await ac.aclose()


@respx.mock
async def test_500_response_raises_alpaca_error():
    # seestar_alp returns {"title":..., "description":...} with a 5xx status.
    respx.put(f"{BASE_URL}{API}/action").mock(
        return_value=httpx.Response(
            500, json={"title": "Internal Server Error", "description": "boom"}
        )
    )
    ac, _ = _make_client()
    with pytest.raises(AlpacaError) as exc_info:
        await ac.method_sync("get_view_state")
    err = exc_info.value
    assert err.error_number == 500
    assert "boom" in str(err)
    # Not a transport error (the bridge answered, just with a 5xx).
    assert not isinstance(err, AlpacaTransportError)
    await ac.aclose()


@respx.mock
async def test_method_async_uses_async_action():
    route = respx.put(f"{BASE_URL}{API}/action").mock(
        return_value=httpx.Response(200, json=_envelope("ok"))
    )
    ac, _ = _make_client()
    await ac.method_async("iscope_start_stack", {"restart": True})
    body = parse_qs(route.calls.last.request.content.decode())
    assert body["Action"] == ["method_async"]
    params = json.loads(body["Parameters"][0])
    assert params == {"method": "iscope_start_stack", "params": {"restart": True}}
    await ac.aclose()

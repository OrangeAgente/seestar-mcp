"""Unit tests for seestar_mcp.server (controller logic + MCP tool registration).

Covers: exactly-18-tool registration with the expected names, honest destructive
descriptions, that SecretStore is not wired into any tool signature, and that
controller methods convert an ``AlpacaError`` into ``{"ok": False, ...}`` rather
than raising.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import seestar_mcp.server as server_mod
from seestar_mcp.alpaca_client import AlpacaError
from seestar_mcp.server import SeestarController, mcp

EXPECTED_TOOLS = {
    "connect_telescope",
    "get_status",
    "get_view_state",
    "goto_target",
    "start_stack",
    "stop_view",
    "run_autofocus",
    "get_focuser_position",
    "plate_solve",
    "set_filter",
    "set_dew_heater",
    "park",
    "shutdown",
    "list_subs",
    "download_subs",
    "qa_tier1",
    "qa_tier2",
    "qa_session_report",
    "get_site_profile",
    "set_site_profile",
    "assess_conditions",
    "get_target_observability",
    "plan_targets",
}

# Destructive / motion / side-effecting tools that MUST state their effect.
DESTRUCTIVE = {
    "goto_target",
    "park",
    "shutdown",
    "set_dew_heater",
    "start_stack",
    "stop_view",
}


async def test_exactly_23_tools_with_expected_names():
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert len(tools) == 23
    assert names == EXPECTED_TOOLS


async def test_destructive_tools_describe_side_effects():
    tools = {t.name: t for t in await mcp.list_tools()}
    for name in DESTRUCTIVE:
        desc = (tools[name].description or "").lower()
        assert desc, f"{name} has no description"
        # Each destructive tool must plainly signal an effect.
        assert any(
            token in desc
            for token in ("side effect", "terminates", "invalidates", "motion", "halts")
        ), f"{name} description does not state its side effect: {desc!r}"
    # shutdown must specifically call out ending the control link.
    assert "terminates the seestar_alp control link" in (
        tools["shutdown"].description or ""
    ).lower()


async def test_all_tools_have_descriptions():
    tools = await mcp.list_tools()
    for t in tools:
        assert t.description and t.description.strip(), f"{t.name} lacks a description"


def test_secretstore_not_in_any_tool_signature():
    # No tool wrapper may take a SecretStore (or any 'secret'-named) parameter.
    for name in EXPECTED_TOOLS:
        func = getattr(server_mod, name)
        sig = inspect.signature(func)
        for pname, param in sig.parameters.items():
            assert "secret" not in pname.lower()
            ann = str(param.annotation).lower()
            assert "secretstore" not in ann
    # SecretStore is not even imported into the server module namespace.
    assert "SecretStore" not in vars(server_mod)


def _controller_with_mock_alpaca(alpaca):
    return SeestarController(
        settings=_dummy_settings(),
        provenance=AsyncMock(),
        alpaca=alpaca,
        data=AsyncMock(),
        tier1=AsyncMock(),
    )


def _dummy_settings():
    from seestar_mcp.config import Settings

    return Settings()


async def test_connect_telescope_maps_alpaca_error_to_ok_false():
    alpaca = AsyncMock()
    alpaca.set_connected.side_effect = AlpacaError(1031, "InvalidOperation", "connected")
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.connect_telescope()
    assert result["ok"] is False
    assert result["error_number"] == 1031
    assert "InvalidOperation" in result["error"]


async def test_goto_target_maps_alpaca_error_to_ok_false(tmp_path):
    from seestar_mcp.config import Settings

    alpaca = AsyncMock()
    alpaca.method_sync.side_effect = AlpacaError(1025, "ValueNotSet", "action")
    ctrl = SeestarController(
        settings=Settings(manifest_dir=tmp_path / "m"),
        provenance=AsyncMock(),
        alpaca=alpaca,
        data=AsyncMock(),
        tier1=AsyncMock(),
    )
    result = await ctrl.goto_target("M31", 10.68, 41.27, session_id="err-1")
    assert result["ok"] is False
    assert result["error_number"] == 1025


async def test_goto_target_native_error_maps_to_ok_false(tmp_path):
    # Live-test regression: the scope tunnels a native "Error: ..." result inside
    # an otherwise-ok Alpaca envelope. goto_target MUST NOT report ok:true (which
    # would let a run-session flow proceed to solve/stack on a phantom goto).
    from seestar_mcp.config import Settings

    alpaca = AsyncMock()
    alpaca.method_sync.return_value = {
        "method": "iscope_start_view",
        "params": {"mode": "star"},
        "result": "Error: Exceeded allotted wait time for result",
    }
    ctrl = SeestarController(
        settings=Settings(manifest_dir=tmp_path / "m"),
        provenance=AsyncMock(),
        alpaca=alpaca,
        data=AsyncMock(),
        tier1=AsyncMock(),
    )
    result = await ctrl.goto_target("M31", 10.68, 41.27, session_id="err-native")
    assert result["ok"] is False
    assert "Error" in result["error"]
    assert result["raw"]["result"].startswith("Error")
    # It must NOT signal a started/successful session.
    assert result.get("ok") is not True


async def test_goto_target_native_success_stays_ok_true(tmp_path):
    # A legit non-error native result must still read as ok:true (no over-trigger).
    from seestar_mcp.config import Settings

    alpaca = AsyncMock()
    alpaca.method_sync.return_value = {"status": "ok", "method": "iscope_start_view"}
    ctrl = SeestarController(
        settings=Settings(manifest_dir=tmp_path / "m"),
        provenance=AsyncMock(),
        alpaca=alpaca,
        data=AsyncMock(),
        tier1=AsyncMock(),
    )
    result = await ctrl.goto_target("M31", 10.68, 41.27, session_id="ok-native")
    assert result["ok"] is True
    assert result["session_id"] == "ok-native"


async def test_start_stack_native_error_maps_to_ok_false():
    alpaca = AsyncMock()
    alpaca.method_sync.return_value = "Error: cannot start stack"
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.start_stack()
    assert result["ok"] is False
    assert "Error" in result["error"]


async def test_run_autofocus_native_error_maps_to_ok_false():
    alpaca = AsyncMock()
    alpaca.method_sync.return_value = {"result": "Error: autofocus failed"}
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.run_autofocus()
    assert result["ok"] is False
    assert "Error" in result["error"]


async def test_park_native_error_maps_to_ok_false():
    alpaca = AsyncMock()
    alpaca.method_sync.return_value = "Error: park refused"
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.park()
    assert result["ok"] is False
    assert "Error" in result["error"]


async def test_stop_view_native_error_maps_to_ok_false():
    alpaca = AsyncMock()
    alpaca.method_sync.return_value = {"result": "Error: stop failed"}
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.stop_view("Stack")
    assert result["ok"] is False
    assert "Error" in result["error"]


async def test_set_filter_native_error_maps_to_ok_false():
    alpaca = AsyncMock()
    alpaca.method_sync.return_value = "Error: wheel jammed"
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.set_filter(1)
    assert result["ok"] is False
    assert "Error" in result["error"]


async def test_set_dew_heater_native_error_maps_to_ok_false():
    alpaca = AsyncMock()
    alpaca.method_sync.return_value = {"result": "Error: heater fault"}
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.set_dew_heater(True)
    assert result["ok"] is False
    assert "Error" in result["error"]


async def test_shutdown_native_error_maps_to_ok_false():
    alpaca = AsyncMock()
    alpaca.method_sync.return_value = "Error: shutdown refused"
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.shutdown()
    assert result["ok"] is False
    assert "Error" in result["error"]


async def test_get_status_never_raises_on_error():
    alpaca = AsyncMock()
    alpaca.get_connected.side_effect = AlpacaError(1099, "boom", "connected")
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.get_status()
    assert result["ok"] is False
    assert result["error_number"] == 1099


async def test_get_view_state_native_error_maps_to_ok_false():
    # When idle/slow, seestar_alp tunnels a native "Error: ..." result string
    # inside an otherwise-ok Alpaca envelope. Surface it as ok:false.
    alpaca = AsyncMock()
    alpaca.method_sync.return_value = {
        "method": "get_view_state",
        "params": [],
        "result": "Error: Exceeded allotted wait time for result",
    }
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.get_view_state()
    assert result["ok"] is False
    assert "Error" in result["error"]
    assert result["raw"]["result"].startswith("Error")


async def test_plate_solve_native_error_string_maps_to_ok_false():
    alpaca = AsyncMock()
    alpaca.method_sync.return_value = "Error: solve failed"
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.plate_solve()
    assert result["ok"] is False
    assert "Error" in result["error"]


async def test_get_focuser_position_native_error_maps_to_ok_false():
    alpaca = AsyncMock()
    alpaca.method_sync.return_value = {"result": "Error: no focuser"}
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.get_focuser_position()
    assert result["ok"] is False


async def test_view_state_transport_error_returns_ok_false():
    from seestar_mcp.alpaca_client import AlpacaTransportError

    alpaca = AsyncMock()
    alpaca.method_sync.side_effect = AlpacaTransportError(
        -1, "Connection refused", "action"
    )
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.get_view_state()  # must not raise
    assert result["ok"] is False
    assert result["error_number"] == -1


async def test_get_status_transport_error_returns_ok_false():
    from seestar_mcp.alpaca_client import AlpacaTransportError

    alpaca = AsyncMock()
    alpaca.get_connected.side_effect = AlpacaTransportError(
        -1, "Connection refused", "connected"
    )
    ctrl = _controller_with_mock_alpaca(alpaca)
    result = await ctrl.get_status()  # must not raise past method-level guard
    assert result["ok"] is False
    assert result["error_number"] == -1

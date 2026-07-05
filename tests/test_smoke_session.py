"""End-to-end smoke test: drive a full mocked observing session.

Builds a :class:`SeestarController` with an ``AsyncMock`` alpaca (a method-name
dispatcher covers the whole native-call flow), an ``AsyncMock`` data client, and
a REAL :class:`Tier1Monitor` wrapping the mock. It runs the run-session sequence
end-to-end, then does a real Tier-2 wind-down over the three committed FITS
fixtures and asserts the report + manifest artifacts were written.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

from seestar_mcp.alpaca_client import AlpacaNotImplemented
from seestar_mcp.config import Settings
from seestar_mcp.provenance import ProvenanceLog
from seestar_mcp.qa_tier1 import Tier1Monitor
from seestar_mcp.server import SeestarController

FIXTURES = Path(__file__).parent / "fixtures"


def _build_alpaca() -> AsyncMock:
    """AsyncMock alpaca whose method_sync dispatches on the native method name."""
    alpaca = AsyncMock()

    # Standard GET verbs. tracking is intentionally NotImplemented -> None.
    alpaca.get_connected.return_value = True
    alpaca.get_ra.return_value = 10.6847
    alpaca.get_dec.return_value = 41.2687
    alpaca.get_tracking.side_effect = AlpacaNotImplemented(
        1024, "NotImplemented", "tracking"
    )
    alpaca.is_slewing.return_value = False
    alpaca.set_connected.return_value = True

    view_counter = {"n": 0}

    def dispatch(method, params=None):
        if method == "get_view_state":
            view_counter["n"] += 1
            n = view_counter["n"]
            return {
                "stacked_frame": 10 * n,
                "dropped_frame": n,
                "plate_solve": True,
                "hfd": 2.0,
            }
        if method == "get_device_state":
            return {"focus_pos": 1500, "tracking": True}
        if method == "get_focuser_position":
            return {"step": 1500}
        if method == "get_solve_result":
            return {"ra": 10.6847, "dec": 41.2687, "rms": 0.42, "solved": True}
        # iscope_start_view / start_solve / start_auto_focus / iscope_start_stack
        # / iscope_stop_view all just acknowledge.
        return {"status": "ok", "method": method}

    alpaca.method_sync.side_effect = dispatch
    return alpaca


async def test_full_session_smoke(tmp_path):
    data_dir = tmp_path / "data"
    manifest_dir = tmp_path / "manifests"
    settings = Settings(
        data_dir=data_dir,
        manifest_dir=manifest_dir,
        provenance_log=data_dir / "provenance.jsonl",
    )
    data_dir.mkdir(parents=True)

    alpaca = _build_alpaca()
    provenance = ProvenanceLog(settings.provenance_log)
    tier1 = Tier1Monitor(alpaca, provenance=provenance)
    data = AsyncMock()

    ctrl = SeestarController(
        settings,
        provenance=provenance,
        alpaca=alpaca,
        data=data,
        tier1=tier1,
    )

    # 1. connect
    r = await ctrl.connect_telescope()
    assert r["ok"] is True and r["connected"] is True

    # 2. status — tracking is a mocked NotImplemented -> None, but call succeeds.
    r = await ctrl.get_status()
    assert r["ok"] is True
    assert r["rightascension"] == 10.6847
    assert r["tracking"] is None  # NotImplemented resolved to None
    assert r["slewing"] is False

    # 3. goto (deterministic session id)
    r = await ctrl.goto_target(
        "M31", 10.6847, 41.2687, use_lp_filter=True, session_id="smoke-1"
    )
    assert r["ok"] is True
    assert r["session_id"] == "smoke-1"
    assert ctrl.manifest is not None

    # 4. plate solve
    r = await ctrl.plate_solve()
    assert r["ok"] is True
    assert r["solve_result"]["solved"] is True

    # 5. autofocus (seeds the tier1 focus baseline)
    r = await ctrl.run_autofocus()
    assert r["ok"] is True
    assert r["focus_pos"] == 1500
    assert tier1.focus_baseline == 1500

    # 6. start stacking
    r = await ctrl.start_stack()
    assert r["ok"] is True

    # 7. Tier-1 polling a few times (each poll advances the mock view counter).
    for _ in range(3):
        r = await ctrl.qa_tier1()
        assert r["ok"] is True
        assert "raw" not in r["snapshot"]  # bulky telemetry trimmed from output
        assert isinstance(r["flags"], list)
    # Trends populated after multiple polls.
    assert r["trends"]["stacked_delta"] == 10

    # 8. stop the stack
    r = await ctrl.stop_view("Stack")
    assert r["ok"] is True
    assert r["mode"] == "Stack"

    # --- QA wind-down over the committed fixtures ---
    fixture_paths = []
    for stem in ("good", "bad_ecc", "bad_snr"):
        dst = data_dir / f"{stem}.fits"
        shutil.copy(FIXTURES / f"{stem}.fits", dst)
        fixture_paths.append(str(dst))

    r = await ctrl.qa_session_report(paths=fixture_paths)
    assert r["ok"] is True

    # keep_list keeps the good sub, drops the two bad ones.
    assert "good" in r["keep_list"]
    assert "bad_ecc" not in r["keep_list"]
    assert "bad_snr" not in r["keep_list"]

    # Report + manifest artifacts were written and exist.
    assert Path(r["report_json"]).exists()
    assert Path(r["report_md"]).exists()
    manifest_path = Path(r["manifest"])
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["session_id"] == "smoke-1"
    # All three subs have a recorded verdict in the manifest.
    assert set(manifest["verdicts"]) == {"good", "bad_ecc", "bad_snr"}
    assert manifest["keep_list"] == r["keep_list"]

    await ctrl.aclose()

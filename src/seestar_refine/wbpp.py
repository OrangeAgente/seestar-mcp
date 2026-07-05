"""PixInsight WeightedBatchPreprocessing (WBPP) stacking: keep-list -> master.

The best-effort *full-finish* stacking path. It drives a headless PixInsight to
run a bundled PJSR runner (:file:`pjsr/wbpp_runner.js`) over the keep-list's
light frames and writes an integrated master. PixInsight is a Windows/macOS
desktop app and is **required** for this path — when it is not configured the
call returns a structured error instead of doing anything.

Same testability contract as :mod:`seestar_refine.dss`:

- :func:`build_wbpp_command` — pure: render the PixInsight command line.
- :func:`run_wbpp` — write a params JSON the runner reads, build the command,
  invoke it through an **injectable** ``runner`` (default :func:`subprocess.run`),
  and locate the produced master. The real PixInsight is NEVER run in tests — the
  ``runner`` is faked. Never raises.

.. note:: PIXINSIGHT-DEPENDENT

   The PixInsight command *flags* below and the bundled PJSR runner are a
   best-effort encoding of the documented automation interface. They are flagged
   ``# PIXINSIGHT-DEPENDENT`` and MUST be validated against the installed
   PixInsight/WBPP during real use (a single, isolated update point). Tests only
   assert the buildable logic (flags, params file, subprocess kwargs, master
   location, not-configured error) — never a real WBPP run.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .dss import _master_stats, _slug, _tail
from .models import StackResult

if TYPE_CHECKING:
    from .config import RefineSettings
    from .keeplist import KeepList

# WBPP is a long integration; allow generously more headroom than a DSS stack.
_WBPP_TIMEOUT_S = 3600

# Master filename patterns searched (newest wins) under the output dir. WBPP
# writes a ``masterLight*.xisf``; FITS is accepted as a fallback export.
# PIXINSIGHT-DEPENDENT: confirm the real master name on the installed WBPP.
_MASTER_GLOBS = (
    "*masterLight*.xisf",
    "*master*.xisf",
    "*.xisf",
    "*master*.fit",
    "*master*.fits",
)


def _wbpp_runner_path() -> Path:
    """Absolute path to the bundled PJSR WBPP runner shipped in the package."""
    return Path(__file__).parent / "pjsr" / "wbpp_runner.js"


def build_wbpp_command(
    keep_list: KeepList,
    output_dir: str | Path,
    *,
    pixinsight_exe: str,
    runner_script: str,
    params_path: str,
) -> list[str]:
    """Render the headless PixInsight command line that runs the WBPP runner.

    Pure: builds a list, touches no disk, never raises. The keep-list light
    paths + output dir are NOT passed as flags — they live in the params JSON at
    ``params_path`` which the runner reads (see :func:`run_wbpp`).

    PIXINSIGHT-DEPENDENT: the flags are a best-effort encoding of PixInsight's
    automation interface and must be confirmed against the installed app.
    """
    return [
        str(pixinsight_exe),
        # PIXINSIGHT-DEPENDENT: run the bundled PJSR script.
        f"-r={runner_script}",
        # PIXINSIGHT-DEPENDENT: headless/no-GUI automation.
        "--automation-mode",
        # PIXINSIGHT-DEPENDENT: exit the app when the script finishes.
        "--force-exit",
        # PIXINSIGHT-DEPENDENT: hand the runner its params file (read as a
        # script argument, e.g. via ``jsArguments`` in PJSR).
        f"-a={params_path}",
    ]


def _find_master(output_dir: Path) -> str | None:
    """Return the newest WBPP master under ``output_dir``, else None."""
    candidates: list[Path] = []
    for pattern in _MASTER_GLOBS:
        candidates.extend(output_dir.glob(pattern))
    files = [p for p in candidates if p.is_file()]
    if not files:
        return None
    newest = max(files, key=lambda p: p.stat().st_mtime)
    return str(newest)


def run_wbpp(
    keep_list: KeepList,
    settings: RefineSettings,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> StackResult:
    """Stack a keep-list into a WBPP master, returning a :class:`StackResult`.

    Requires a configured, existing ``settings.pixinsight_exe`` — otherwise a
    structured not-configured error is returned WITHOUT touching disk or
    launching anything (the configured path is validated; an arbitrary path is
    never executed). On success, writes a params JSON into ``output_dir``, builds
    the command via :func:`build_wbpp_command`, invokes it through the injectable
    ``runner`` (``capture_output=True, text=True, timeout=...``), and locates the
    newest master. ``preview_path`` is always ``None`` (no auto-preview for WBPP).

    Never raises: a missing PixInsight, non-zero exit, timeout, missing master,
    or any exception all map to a structured ``ok=False`` result.
    """
    target = keep_list.target
    n_subs = len(keep_list.sub_paths)

    exe = settings.pixinsight_exe
    if not exe or not Path(exe).exists():
        return StackResult(
            ok=False,
            engine="wbpp",
            target=target,
            n_subs=n_subs,
            master_path=None,
            preview_path=None,
            stats={},
            log="",
            error="PixInsight not configured — set SEESTAR_REFINE_PIXINSIGHT_EXE",
        )

    try:
        output_dir = Path(settings.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        params_path = output_dir / f"{_slug(target)}_wbpp_params.json"
        params = {
            "target": target,
            "lights": list(keep_list.sub_paths),
            "output_dir": str(output_dir),
            # Seestar OSC subs are pre-calibrated: register + integrate only.
            "register": True,
            "integrate": True,
            "rejection": settings.rejection,
            "alignment": settings.alignment,
        }
        params_path.write_text(
            json.dumps(params, indent=2), encoding="utf-8"
        )

        cmd = build_wbpp_command(
            keep_list,
            output_dir,
            pixinsight_exe=exe,
            runner_script=str(_wbpp_runner_path()),
            params_path=str(params_path),
        )

        try:
            proc = runner(
                cmd,
                capture_output=True,
                text=True,
                timeout=_WBPP_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            return StackResult(
                ok=False,
                engine="wbpp",
                target=target,
                n_subs=n_subs,
                master_path=None,
                preview_path=None,
                stats={},
                log=_tail(getattr(exc, "stdout", None)),
                error=f"WBPP stacking timeout after {_WBPP_TIMEOUT_S}s",
            )
        except Exception as exc:  # noqa: BLE001 - never raise out of the run path
            return StackResult(
                ok=False,
                engine="wbpp",
                target=target,
                n_subs=n_subs,
                master_path=None,
                preview_path=None,
                stats={},
                log="",
                error=f"failed to launch PixInsight: {exc}",
            )

        returncode = getattr(proc, "returncode", None)
        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
        log = _tail((stdout + stderr) if stderr else stdout)

        if returncode != 0:
            return StackResult(
                ok=False,
                engine="wbpp",
                target=target,
                n_subs=n_subs,
                master_path=None,
                preview_path=None,
                stats={},
                log=log,
                error=f"PixInsight/WBPP exited with code {returncode}",
            )

        master_path = _find_master(output_dir)
        if master_path is None:
            return StackResult(
                ok=False,
                engine="wbpp",
                target=target,
                n_subs=n_subs,
                master_path=None,
                preview_path=None,
                stats={},
                log=log,
                error="WBPP reported success but no master was found",
            )

        return StackResult(
            ok=True,
            engine="wbpp",
            target=target,
            n_subs=n_subs,
            master_path=master_path,
            preview_path=None,
            stats=_master_stats(master_path),
            log=log,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
        return StackResult(
            ok=False,
            engine="wbpp",
            target=target,
            n_subs=n_subs,
            master_path=None,
            preview_path=None,
            stats={},
            log="",
            error=str(exc),
        )

"""DeepSkyStacker (DSS) stacking: keep-list -> file list -> master.

Three layers, all pure/testable without the real app:

- :func:`build_file_list` — render the DSS light-frame file list (plus optional
  dark/flat frames) from a :class:`~seestar_refine.keeplist.KeepList`. Pure text.
- :func:`run_dss` — build the ``DeepSkyStackerCL`` command line and invoke it via
  an **injectable** ``runner`` (default :func:`subprocess.run`), then locate the
  produced master. The real DSS is a Windows desktop app and is NEVER run in
  tests — the ``runner`` is faked. Never raises.
- :func:`stack` — the orchestration: write the file list into the output dir,
  call :func:`run_dss`, open the master with astropy for basic stats, and return
  a :class:`~seestar_refine.models.StackResult`. Never raises.

.. note:: FORMAT-DEPENDENT

   The exact DSS *file-list* text and the ``DeepSkyStackerCL`` *command flags*
   below are a best-effort encoding of the documented format. They are flagged
   ``# FORMAT-DEPENDENT`` and MUST be validated against the installed DSS during
   real use (single, isolated update point). Tests only assert the buildable
   logic (every sub is listed; the CLI + ``/L`` flag + subprocess kwargs; master
   location; error handling) — never a real stack.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .models import StackResult

if TYPE_CHECKING:
    from .config import RefineSettings
    from .keeplist import KeepList

# How much of the tool's own stdout/stderr to keep in the returned log.
_LOG_TAIL_CHARS = 4000

# FITS suffixes astropy can open for master statistics.
_FITS_SUFFIXES = (".fit", ".fits")

# Master/autosave filename patterns searched (newest wins) under the output dir.
# FORMAT-DEPENDENT: DSS's default integrated output is ``Autosave.tif`` written
# beside the file list; masters may also be exported as FITS/TIFF. Verify the
# real names on the installed DSS.
_MASTER_GLOBS = ("Autosave.*", "*master*.fit", "*master*.fits", "*master*.tif")


def build_file_list(
    keep_list: KeepList,
    *,
    dark_paths: list[str] | None = None,
    flat_paths: list[str] | None = None,
) -> str:
    """Render the DSS file-list text for a keep-list (+ optional cal frames).

    Every kept light sub is listed (checked, typed ``light``); optional dark /
    flat frames are appended with their own type markers. Pure: builds a string,
    touches no disk, never raises.

    FORMAT-DEPENDENT: the header, the per-line ``<checked>\\t<type>\\t<path>``
    column layout, and the type keywords are a best-effort encoding of the DSS
    ``.dssfilelist`` format and must be confirmed against the installed DSS.
    Seestar OSC subs are pre-calibrated, so darks/flats are OFF by default.
    """
    lines: list[str] = []
    # FORMAT-DEPENDENT header line DSS writes at the top of a saved file list.
    lines.append("DSS file list")
    # FORMAT-DEPENDENT column header: Checked, Type, Path (tab-separated). "1"
    # marks the frame as checked/included; the type keyword selects the group.
    lines.append("Checked\tType\tPath")

    def _emit(paths: list[str], frame_type: str) -> None:
        for path in paths:
            # "1" == checked/included. FORMAT-DEPENDENT type keyword.
            lines.append(f"1\t{frame_type}\t{path}")

    _emit(list(keep_list.sub_paths), "light")
    _emit(list(dark_paths or []), "dark")
    _emit(list(flat_paths or []), "flat")

    return "\n".join(lines) + "\n"


def _tail(text: str | None) -> str:
    """Last ``_LOG_TAIL_CHARS`` of a tool's output (empty string for None)."""
    if not text:
        return ""
    return text[-_LOG_TAIL_CHARS:]


def _find_master(output_dir: Path) -> str | None:
    """Return the newest master/autosave under ``output_dir``, else None."""
    candidates: list[Path] = []
    for pattern in _MASTER_GLOBS:
        candidates.extend(output_dir.glob(pattern))
    files = [p for p in candidates if p.is_file()]
    if not files:
        return None
    newest = max(files, key=lambda p: p.stat().st_mtime)
    return str(newest)


def run_dss(
    file_list_path: str,
    output_dir: str | Path,
    *,
    dss_cli: str,
    timeout_s: int = 1800,
    runner: Callable[..., object] = subprocess.run,
) -> dict:
    """Invoke ``DeepSkyStackerCL`` on a file list and locate the master.

    Builds ``[dss_cli, "/L", file_list_path, ...]`` and calls ``runner`` with
    ``capture_output=True, text=True, timeout=timeout_s``. ``runner`` defaults to
    :func:`subprocess.run` and is injected in tests (the real DSS never runs in
    CI). On return code 0, the newest master under ``output_dir`` is located.

    Returns ``{"ok", "master_path", "log", "returncode", "error"}``. Never raises:
    a non-zero exit, a timeout, a missing master, or any exception all map to a
    structured ``ok: False`` result.

    FORMAT-DEPENDENT: the ``/L`` file-list flag plus the register+integrate /
    autosave flags below are a best-effort encoding of the ``DeepSkyStackerCL``
    interface and must be confirmed against the installed DSS.
    """
    output_dir = Path(output_dir)
    # FORMAT-DEPENDENT command: "/L <list>" loads the file list; the remaining
    # flags request a register+integrate run that writes the autosave master. The
    # real flag spelling (e.g. "/S" to stack) must be verified on the installed
    # DeepSkyStackerCL — this is the single point to update.
    cmd = [
        dss_cli,
        "/L",
        str(file_list_path),
        "/S",  # FORMAT-DEPENDENT: register + integrate (stack) the loaded list.
    ]
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "master_path": None,
            "log": _tail(getattr(exc, "stdout", None)),
            "returncode": None,
            "error": f"DSS stacking timeout after {timeout_s}s",
        }
    except Exception as exc:  # noqa: BLE001 - never raise out of the run path
        return {
            "ok": False,
            "master_path": None,
            "log": "",
            "returncode": None,
            "error": f"failed to launch DeepSkyStackerCL: {exc}",
        }

    returncode = getattr(proc, "returncode", None)
    stdout = getattr(proc, "stdout", "") or ""
    stderr = getattr(proc, "stderr", "") or ""
    log = _tail((stdout + stderr) if stderr else stdout)

    if returncode != 0:
        return {
            "ok": False,
            "master_path": None,
            "log": log,
            "returncode": returncode,
            "error": f"DeepSkyStackerCL exited with code {returncode}",
        }

    master_path = _find_master(output_dir)
    if master_path is None:
        return {
            "ok": False,
            "master_path": None,
            "log": log,
            "returncode": returncode,
            "error": "DSS reported success but no master/autosave was found",
        }
    return {
        "ok": True,
        "master_path": master_path,
        "log": log,
        "returncode": returncode,
        "error": None,
    }


def _master_stats(master_path: str) -> dict:
    """Basic min/median/max/shape of a FITS master; ``{}`` if not computable.

    Only FITS masters are opened (astropy). Non-FITS masters (e.g. TIFF autosave)
    or any read error are guarded to an empty dict — never raises.
    """
    try:
        path = Path(master_path)
        if path.suffix.lower() not in _FITS_SUFFIXES:
            return {}
        import numpy as np
        from astropy.io import fits

        with fits.open(path) as hdul:
            data = None
            for hdu in hdul:
                if getattr(hdu, "data", None) is not None:
                    data = hdu.data
                    break
        if data is None:
            return {}
        arr = np.asarray(data, dtype="float64")
        if arr.size == 0:
            return {}
        return {
            "min": float(np.nanmin(arr)),
            "median": float(np.nanmedian(arr)),
            "max": float(np.nanmax(arr)),
            "shape": list(arr.shape),
        }
    except Exception:  # noqa: BLE001 - stats are best-effort; guard everything
        return {}


def stack(
    keep_list: KeepList,
    settings: RefineSettings,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> StackResult:
    """Stack a keep-list into a DSS master, returning a :class:`StackResult`.

    Writes the DSS file list into ``settings.output_dir`` (created if needed),
    invokes :func:`run_dss` with the configured ``settings.dss_cli``, and — on
    success — opens the master with astropy for basic stats. ``preview_path`` is
    always ``None`` here (the preview is produced by ``stretch_master``, Task 3).

    Never raises. When ``settings.dss_cli`` is empty, returns a structured
    not-configured error without touching disk.
    """
    target = keep_list.target
    n_subs = len(keep_list.sub_paths)

    if not settings.dss_cli:
        return StackResult(
            ok=False,
            engine="dss",
            target=target,
            n_subs=n_subs,
            master_path=None,
            preview_path=None,
            stats={},
            log="",
            error="DeepSkyStackerCL not configured — set SEESTAR_REFINE_DSS_CLI",
        )

    try:
        output_dir = Path(settings.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        file_list_path = output_dir / f"{_slug(target)}_dss_filelist.txt"
        file_list_path.write_text(build_file_list(keep_list), encoding="utf-8")

        run = run_dss(
            str(file_list_path),
            output_dir,
            dss_cli=settings.dss_cli,
            runner=runner,
        )
        if not run["ok"]:
            return StackResult(
                ok=False,
                engine="dss",
                target=target,
                n_subs=n_subs,
                master_path=run.get("master_path"),
                preview_path=None,
                stats={},
                log=run.get("log", ""),
                error=run.get("error"),
            )

        master_path = run["master_path"]
        return StackResult(
            ok=True,
            engine="dss",
            target=target,
            n_subs=n_subs,
            master_path=master_path,
            preview_path=None,
            stats=_master_stats(master_path),
            log=run.get("log", ""),
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
        return StackResult(
            ok=False,
            engine="dss",
            target=target,
            n_subs=n_subs,
            master_path=None,
            preview_path=None,
            stats={},
            log="",
            error=str(exc),
        )


def _slug(text: str) -> str:
    """Filesystem-safe slug for a target name (letters/digits/dashes)."""
    import re

    slug = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return slug or "session"

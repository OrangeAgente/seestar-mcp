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

.. note:: VALIDATED against DeepSkyStacker 6.2.1 (2026-07-05) on real Seestar M31
   subs. The file-list header (``CHECKED<TAB>TYPE<TAB>FILE``) and the command
   (``/r /S /FITS /O:<master> <listfile>`` — the list is POSITIONAL, no ``/L``)
   are confirmed. Tests still mock the subprocess (the real DSS is never run in
   CI) and assert the buildable logic: every sub listed; the command flags;
   master location; error handling.
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
    # VALIDATED (DSS 6.2.1, 2026-07-05, on real Seestar subs): the header row must
    # be exactly "CHECKED<TAB>TYPE<TAB>FILE". "1" marks the frame checked/included;
    # the type keyword (light/dark/flat/offset/...) selects the group. Spaces in
    # file paths are fine (tab-delimited).
    lines.append("CHECKED\tTYPE\tFILE")

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
    master_name: str = "master.fit",
    timeout_s: int = 1800,
    runner: Callable[..., object] = subprocess.run,
) -> dict:
    """Invoke ``DeepSkyStackerCL`` on a file list and locate the master.

    Builds ``[dss_cli, "/r", "/S", "/FITS", "/O:<master>", file_list_path]`` (the
    file list is the POSITIONAL final argument — there is no ``/L`` flag) and
    calls ``runner`` with ``capture_output=True, text=True, timeout=timeout_s``.
    ``runner`` defaults to :func:`subprocess.run` and is injected in tests (the
    real DSS never runs in CI). On return code 0, the master at the explicit
    ``/O:`` path is used, falling back to the newest master under ``output_dir``.

    Returns ``{"ok", "master_path", "log", "returncode", "error"}``. Never raises:
    a non-zero exit, a timeout, a missing master, or any exception all map to a
    structured ``ok: False`` result.

    The command below is VALIDATED against DeepSkyStacker 6.2.1 (see the inline
    note); confirm the flags if you target a different DSS build.
    """
    output_dir = Path(output_dir)
    master_out = output_dir / master_name
    # VALIDATED (DSS 6.2.1, 2026-07-05, on real Seestar M31 subs): register (/r)
    # then stack (/S), write a FITS master (/FITS) to an explicit path
    # (/O:<full path>); the file list is the POSITIONAL final argument — there is
    # NO "/L" flag. Without /O, DSS writes Autosave.tif beside the first light
    # frame (not our output dir), so /O is required.
    cmd = [
        dss_cli,
        "/r",
        "/S",
        "/FITS",
        f"/O:{master_out}",
        str(file_list_path),
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

    master_path = str(master_out) if master_out.is_file() else _find_master(output_dir)
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
            master_name=f"{_slug(target)}_master.fit",
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

"""Prepare a stacked master for the EXTERNAL ``pixinsight-mcp`` server.

This module does NOT do the creative PixInsight finish (that is the external
`pixinsight-mcp <https://github.com/aescaffre/pixinsight-mcp>`_ server's job). It
only prepares the hand-off:

- :func:`write_pixinsight_config` — write the JSON config ``pixinsight-mcp``
  consumes (target, absolute per-channel master paths, output dir, defaults).
- :func:`to_xisf` — optionally convert a FITS master to XISF via the optional
  ``xisf`` package; when it is not installed, degrade to a documented FITS
  fallback (``xisf`` is deliberately NOT a dependency of this service).

Both functions never raise — bad input maps to ``{"ok": False, "error": ...}``.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path


def _slug(text: str) -> str:
    """Filesystem-safe slug for a target name (letters/digits/dashes)."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return slug or "session"


def write_pixinsight_config(
    master_path: str | Path, target: str, output_dir: str | Path
) -> dict:
    """Write the JSON config the external ``pixinsight-mcp`` expects.

    A single Seestar OSC master is a color image, so it fills the ``RGB`` channel
    slot; the mono/narrowband slots are present but empty. Writes
    ``<output_dir>/<target>_pixinsight.json`` and returns
    ``{"ok", "config_path", "config"}``. Never raises.

    EXTERNAL-SCHEMA: the config shape below MUST match ``pixinsight-mcp``'s
    expected input (target, absolute channel paths R/G/B/L/Ha, output dir). This
    is the single point to update if that server's schema changes.
    """
    try:
        abs_master = str(Path(master_path).absolute())
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        abs_out = str(out_dir.absolute())

        # EXTERNAL-SCHEMA — pixinsight-mcp config contract.
        config = {
            "target": target,
            "channels": {
                # A single OSC master is the full-color input.
                "RGB": abs_master,
                # Mono/narrowband slots (unused for a Seestar OSC master).
                "R": None,
                "G": None,
                "B": None,
                "L": None,
                "Ha": None,
            },
            "output_dir": abs_out,
            # Sensible defaults for the external creative finish.
            "process": {
                "gradient_correction": True,
                "deconvolution": True,  # BlurXTerminator, if licensed
                "denoise": True,  # NoiseXTerminator, if licensed
                "star_reduction": False,
                "stretch": "auto",
            },
            "output_format": "xisf",
        }

        config_path = out_dir / f"{_slug(target)}_pixinsight.json"
        config_path.write_text(
            json.dumps(config, indent=2), encoding="utf-8"
        )
        return {
            "ok": True,
            "config_path": str(config_path),
            "config": config,
        }
    except Exception as exc:  # noqa: BLE001 - tool-facing never-raise contract
        return {"ok": False, "error": str(exc)}


def to_xisf(master_path: str | Path, out_path: str | Path) -> dict:
    """Convert a FITS master to XISF via the optional ``xisf`` package.

    Returns ``{"ok": True, "xisf_path": ...}`` when the ``xisf`` package is
    importable and the conversion succeeds. When it is not installed (the default
    — ``xisf`` is NOT a dependency of this service), degrades to the documented
    fallback ``{"ok": False, "error": ..., "fallback": "fits"}`` so callers pass
    the FITS master straight to PixInsight/WBPP. Never raises.
    """
    if importlib.util.find_spec("xisf") is None:
        return {
            "ok": False,
            "error": "xisf not installed — pass the FITS master to PixInsight/WBPP",
            "fallback": "fits",
        }
    try:
        import numpy as np
        from astropy.io import fits
        from xisf import XISF  # type: ignore[import-not-found]

        with fits.open(master_path) as hdul:
            data = None
            for hdu in hdul:
                if getattr(hdu, "data", None) is not None:
                    data = np.asarray(hdu.data)
                    break
        if data is None:
            return {
                "ok": False,
                "error": "no image data in FITS master",
                "fallback": "fits",
            }
        XISF.write(str(out_path), data)
        return {"ok": True, "xisf_path": str(out_path)}
    except Exception as exc:  # noqa: BLE001 - degrade to the FITS fallback
        return {"ok": False, "error": str(exc), "fallback": "fits"}

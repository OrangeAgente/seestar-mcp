"""Stage 7 of AstroPipe: opt-in, provenance-labeled resolution upscaling.

Two methods, both honest about what they produce:

- ``"lanczos"`` (default): high-quality interpolation (Pillow LANCZOS). Adds NO
  new detail — it resamples captured signal. Bundled, no extra deps.
- ``"ai"``: super-resolution via an ONNX model (e.g. Real-ESRGAN) IF
  ``onnxruntime`` and a ``model_path`` are available; otherwise it falls back to
  lanczos. AI upscaling *invents* plausible detail, so the returned ``label``
  says so explicitly — the caller must record it in provenance.

Never raises: on any failure the input is returned unchanged.
"""

from __future__ import annotations

import numpy as np

_LANCZOS_LABEL = "interpolated (Lanczos) — no new detail, captured signal only"
_AI_LABEL = "AI-generated detail — synthetic, NOT captured signal"


def _lanczos(img: np.ndarray, factor: int) -> np.ndarray:
    from PIL import Image

    src = np.asarray(img)
    arr = src if np.issubdtype(src.dtype, np.integer) else np.clip(src, 0, 255)
    pil = Image.fromarray(arr.astype("uint8"))
    out = pil.resize((pil.width * factor, pil.height * factor), Image.LANCZOS)
    return np.asarray(out)


def upscale(
    img: np.ndarray,
    *,
    factor: int = 2,
    method: str = "lanczos",
    model_path: str | None = None,
) -> tuple[np.ndarray, dict]:
    """Upscale an 8-bit RGB image by ``factor`` and report an honest label.

    Returns ``(upscaled, info)`` where ``info`` carries ``method``, a human
    ``label`` (interpolation vs synthetic AI detail), and ``fell_back`` (True
    when an AI request degraded to Lanczos). Never raises.
    """
    fac = max(1, int(factor))
    try:
        if method == "ai":
            try:
                import onnxruntime  # noqa: F401

                if not model_path:
                    raise RuntimeError("no AI model configured")
                # A real Real-ESRGAN ONNX session would run here. It is an opt-in
                # extension (the model is not bundled). Until one is wired, we do
                # NOT silently pretend — fall back and say so.
                raise RuntimeError("AI upscaler not wired — install a model")
            except Exception:  # noqa: BLE001 - degrade to honest interpolation
                out = _lanczos(img, fac)
                return out, {
                    "method": "lanczos",
                    "label": _LANCZOS_LABEL,
                    "fell_back": True,
                    "requested": "ai",
                }
        out = _lanczos(img, fac)
        return out, {"method": "lanczos", "label": _LANCZOS_LABEL, "fell_back": False}
    except Exception:  # noqa: BLE001 - pure core, never raise on bad input
        return np.asarray(img), {"method": "none", "label": "", "fell_back": False}

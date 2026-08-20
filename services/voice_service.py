"""Voice-note service (docs/05, Day-11 bonus). Transcribe + extract intent via
Gemini audio understanding (same Gemini 3.5 — natively multimodal). The
transcript is stored verbatim; raw audio is never discarded. Model call is
injectable for tests.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

# (audio_path, context) -> {"transcript": str, "extracted": {...}}
TranscribeFn = Callable[[str, str], Awaitable[dict[str, Any]]]
_transcribe_fn: TranscribeFn | None = None


def set_transcribe_fn(fn: TranscribeFn) -> None:
    global _transcribe_fn
    _transcribe_fn = fn


async def transcribe(audio_path: str, context: str) -> dict:
    if _transcribe_fn is None:
        return {"status": "error", "error": True,
                "message": "voice transcription requires Gemini credentials"}
    try:
        result = await _transcribe_fn(audio_path, context)
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"transcription failed: {exc}"}
    return {"status": "success", **result}

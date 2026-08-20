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


# ---------------------------------------------------------------------------
# Cloud TTS (docs/19 §P1.8): Chirp 3 HD voice for the async path — voice-note
# replies and read-aloud. The live path (Gemini Live) is unaffected.
# ---------------------------------------------------------------------------

def synthesize_speech(text: str) -> dict:
    """Text -> MP3 via Cloud Text-to-Speech (Chirp 3 HD, en-US). Errors as
    data; secrets/creds via ADC, never in code."""
    try:
        from google.cloud import texttospeech

        client = texttospeech.TextToSpeechClient()
        resp = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text[:4500]),
            voice=texttospeech.VoiceSelectionParams(
                language_code="en-US", name="en-US-Chirp3-HD-Charon"),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3))
        return {"status": "success", "audio": resp.audio_content}
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"tts failed: {exc}"[:200]}

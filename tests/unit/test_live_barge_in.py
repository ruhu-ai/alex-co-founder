"""Founder speech interrupts Live playback promptly and remains authoritative."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE = (ROOT / "app/live.py").read_text(encoding="utf-8")
UI = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
INSTRUCTIONS = (
    ROOT / "agents/co_founder/instructions.py").read_text(encoding="utf-8")


def test_live_config_explicitly_interrupts_on_sensitive_speech_onset():
    assert "RealtimeInputConfig(" in LIVE
    assert "START_SENSITIVITY_HIGH" in LIVE
    assert "START_OF_ACTIVITY_INTERRUPTS" in LIVE
    assert "prefix_padding_ms=20" in LIVE


def test_browser_stops_buffered_audio_on_local_speech_before_round_trip():
    assert "echoCancellation: true" in UI
    assert "noiseSuppression: true" in UI
    assert "current.speechFrames >= 6" in UI
    assert "stopLivePlayback(current);" in UI
    assert "voice.localBargeInUntil > Date.now()" in UI
    assert "voice.localBargeInUntil = 0" in UI


def test_voice_instruction_yields_after_a_short_answer_and_interruption():
    assert "one to three" in INSTRUCTIONS
    assert "Never continue a monologue after an" in INSTRUCTIONS

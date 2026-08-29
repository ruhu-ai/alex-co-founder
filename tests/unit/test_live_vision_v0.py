"""V0 contract tests for disabled-by-default Alex Live vision negotiation."""

from __future__ import annotations

from pathlib import Path

from services import live_media
from services import live_vision_protocol as protocol


def test_protocol_keeps_python_310_strenum_compatibility():
    source = (Path(protocol.__file__)).read_text()
    assert "except ImportError" in source
    assert "class StrEnum(str, Enum)" in source


def _hello(**capability_overrides):
    capabilities = {
        "audio_pcm16": True,
        "visual_sources": ["camera", "display"],
        "image_mime_types": ["image/jpeg"],
    }
    capabilities.update(capability_overrides)
    return {
        "type": "hello",
        "protocol_version": 2,
        "client_capabilities": capabilities,
    }


def test_v2_hello_is_safe_and_visual_disabled_by_default():
    result = protocol.negotiate_hello(_hello())

    assert result["type"] == "hello.ack"
    assert result["enabled"] == {
        "audio": True, "camera": False, "display": False}
    assert result["limits"]["max_frame_bytes"] == 250_000
    assert result["limits"]["max_wire_frame_bytes"] == 340_000
    assert result["limits"]["visual_admission_remaining_seconds"] == 0


def test_camera_and_display_can_be_enabled_independently():
    result = protocol.negotiate_hello(
        _hello(), visual_enabled={"camera": True, "display": False})
    assert result["enabled"]["camera"] is True
    assert result["enabled"]["display"] is False


def test_local_camera_flags_do_not_implicitly_enable_display(monkeypatch):
    monkeypatch.setenv("ALEX_LIVE_VISION_ENABLED", "true")
    monkeypatch.setenv("ALEX_LIVE_CAMERA_ENABLED", "true")
    monkeypatch.setenv("ALEX_LIVE_DISPLAY_ENABLED", "false")

    assert live_media.enabled("camera") is True
    assert live_media.enabled("display") is False


def test_client_cannot_enable_sources_or_raise_limits():
    frame = _hello()
    frame["enabled"] = {"camera": True}
    frame["limits"] = {"max_frame_bytes": 99_000_000}

    result = protocol.negotiate_hello(frame, visual_enabled=True)

    assert result["type"] == "error"
    assert result["code"] == "protocol_violation"


def test_unknown_capability_and_media_types_fail_as_data():
    unknown = protocol.negotiate_hello(_hello(experimental_eye_tracking=True))
    mime = protocol.negotiate_hello(
        _hello(image_mime_types=["image/png"]))

    assert unknown["type"] == "error"
    assert mime["type"] == "error"
    assert unknown["code"] == mime["code"] == "protocol_violation"


def test_v2_visual_frame_is_scoped_without_exposing_content():
    result = protocol.unavailable_v2_frame("media.frame")

    assert result == {
        "type": "error",
        "scope": "media",
        "code": "media_not_enabled",
        "recoverable": True,
        "message": (
            "Live vision is not enabled; voice and text are still available."),
    }


def test_closed_end_reason_matches_reviewed_contract():
    assert {reason.value for reason in protocol.MediaEndReason} == {
        "user_stop", "paused", "suspended", "track_ended",
        "permission_revoked", "source_switch", "session_switch", "logout",
        "page_teardown", "socket_lost", "auth_revoked",
        "provider_unavailable", "budget_exhausted", "protocol_error",
        "server_shutdown",
    }


def test_live_websocket_uses_server_feature_flag_for_media_enablement():
    source = (Path(__file__).resolve().parents[2] / "app/live.py").read_text()

    assert "negotiate_hello" in source
    assert '"camera": live_media.enabled("camera")' in source
    assert '"display": live_media.enabled("display")' in source
    assert "media.media_frame" in source
    assert 'if "audio" in frame:' in source
    assert 'elif "text" in frame:' in source
    assert 'elif frame.get("close"):' in source

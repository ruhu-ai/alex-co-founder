"""Closed V1 protocol contracts for gated Alex Live vision negotiation.

The client can negotiate only server-enabled sources and cannot raise limits.
Capture authority, consent, ephemeral forwarding, and lifecycle state remain in
the server modules defined by docs/35.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

PROTOCOL_VERSION = 2


class VisualSource(StrEnum):
    """Founder-controlled live visual source classes."""

    CAMERA = "camera"
    DISPLAY = "display"


class MediaEndReason(StrEnum):
    """Content-free terminal reasons from the reviewed wire contract."""

    USER_STOP = "user_stop"
    PAUSED = "paused"
    SUSPENDED = "suspended"
    TRACK_ENDED = "track_ended"
    PERMISSION_REVOKED = "permission_revoked"
    SOURCE_SWITCH = "source_switch"
    SESSION_SWITCH = "session_switch"
    LOGOUT = "logout"
    PAGE_TEARDOWN = "page_teardown"
    SOCKET_LOST = "socket_lost"
    AUTH_REVOKED = "auth_revoked"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROTOCOL_ERROR = "protocol_error"
    SERVER_SHUTDOWN = "server_shutdown"


_HELLO_FIELDS = frozenset({"type", "protocol_version", "client_capabilities"})
_CLIENT_CAPABILITY_FIELDS = frozenset({
    "audio_pcm16", "visual_sources", "image_mime_types",
})
_SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/jpeg"})
_SUPPORTED_VISUAL_SOURCES = frozenset(source.value for source in VisualSource)


def protocol_error(code: str, message: str, *, scope: str = "connection",
                   recoverable: bool = True) -> dict[str, Any]:
    """Return one content-free protocol error; never expose provider exceptions."""
    return {
        "type": "error",
        "scope": scope,
        "code": code,
        "recoverable": recoverable,
        "message": message,
    }


def negotiate_hello(
        frame: Any, *, visual_enabled: bool | dict[str, bool] = False
        ) -> dict[str, Any]:
    """Validate a v2 hello and return server-effective, disabled-by-default limits.

    The client cannot enable a modality or raise a server limit.  Visual media stays
    disabled until the separately gated implementation phases in docs/35 authorize it.
    Invalid input is returned as a closed error envelope rather than raised.
    """
    if not isinstance(frame, dict) or frame.get("type") != "hello":
        return protocol_error(
            "protocol_violation", "The first v2 frame must be hello.")
    if set(frame) - _HELLO_FIELDS:
        return protocol_error(
            "protocol_violation", "The hello frame has unsupported fields.")
    if frame.get("protocol_version") != PROTOCOL_VERSION:
        return protocol_error(
            "protocol_violation", "This Live protocol version is not supported.",
            recoverable=False)

    capabilities = frame.get("client_capabilities")
    if not isinstance(capabilities, dict):
        return protocol_error(
            "protocol_violation", "Client capabilities must be an object.")
    if set(capabilities) - _CLIENT_CAPABILITY_FIELDS:
        return protocol_error(
            "protocol_violation", "Client capabilities have unsupported fields.")
    if not isinstance(capabilities.get("audio_pcm16", False), bool):
        return protocol_error(
            "protocol_violation", "audio_pcm16 must be true or false.")

    requested_sources = capabilities.get("visual_sources", [])
    requested_mimes = capabilities.get("image_mime_types", [])
    if (not isinstance(requested_sources, list)
            or not all(isinstance(value, str) for value in requested_sources)
            or not set(requested_sources).issubset(_SUPPORTED_VISUAL_SOURCES)):
        return protocol_error(
            "protocol_violation", "Visual sources are not supported.")
    if (not isinstance(requested_mimes, list)
            or not all(isinstance(value, str) for value in requested_mimes)
            or not set(requested_mimes).issubset(_SUPPORTED_IMAGE_MIME_TYPES)):
        return protocol_error(
            "protocol_violation", "Image media types are not supported.")

    # The server passes independently gated source flags; the client can only
    # narrow this set through its declared capabilities.
    enabled_sources = (visual_enabled if isinstance(visual_enabled, dict)
                       else {source: bool(visual_enabled)
                             for source in _SUPPORTED_VISUAL_SOURCES})
    return {
        "type": "hello.ack",
        "protocol_version": PROTOCOL_VERSION,
        "enabled": {
            "audio": bool(capabilities.get("audio_pcm16", False)),
            "camera": bool(enabled_sources.get("camera"))
            and "camera" in requested_sources,
            "display": bool(enabled_sources.get("display"))
            and "display" in requested_sources,
        },
        "limits": {
            "max_visual_sources": 1,
            "max_fps": 1,
            "max_width": 1280,
            "max_height": 1280,
            "max_frame_bytes": 250_000,
            "max_wire_frame_bytes": 340_000,
            "visual_budget_mode": "conservative_total_session",
            "visual_admission_remaining_seconds": 0,
            "visual_stop_margin_seconds": 10,
        },
    }


def unavailable_v2_frame(frame_type: str) -> dict[str, Any]:
    """Reject an unimplemented v2 frame without disturbing healthy legacy voice."""
    if frame_type.startswith(("media.", "consent.")):
        return protocol_error(
            "media_not_enabled",
            "Live vision is not enabled; voice and text are still available.",
            scope="media")
    return protocol_error(
        "protocol_violation", "This Live frame type is not supported.")

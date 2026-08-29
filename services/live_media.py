"""Server-owned consent, start authorization, frame validation, and budgeting.

The connection object holds only non-content authority and counters. It never stores
live frame bytes after the forwarding callback returns and never creates attachments.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from PIL import Image, UnidentifiedImageError

from services import firestore
from services.live_vision_protocol import MediaEndReason, VisualSource, protocol_error

DISCLOSURE_VERSION = "vision-1"
DISCLOSURES = {
    "camera": (
        "Alex will receive up to one image per second from this camera through "
        "Google Gemini/Vertex AI. Co-Founder does not save live frames; finalized "
        "conversation transcripts are saved under the existing conversation policy."
    ),
    "display": (
        "Alex will receive up to one image per second from the browser-selected tab, "
        "window, or screen through Google Gemini/Vertex AI. Notifications and other "
        "people's information may be visible. Co-Founder does not save live frames; "
        "finalized conversation transcripts are saved under the existing policy."
    ),
}
MAX_FRAME_BYTES = 250_000
MAX_WIRE_FRAME_BYTES = 340_000
MAX_WIDTH = 1_280
MAX_HEIGHT = 1_280
MAX_PIXELS = 589_824
MAX_FPS = 1.0
SESSION_SECONDS = 120
STOP_MARGIN_SECONDS = 10
MIN_USEFUL_SECONDS = 30

ForwardFrame = Callable[[bytes], Awaitable[None]]


def enabled(source: str = "") -> bool:
    """Vision/source flags are server-owned and disabled unless explicitly set."""
    truthy = {"1", "true", "yes", "on"}
    if os.environ.get("ALEX_LIVE_VISION_ENABLED", "").lower() not in truthy:
        return False
    if source == "camera":
        return os.environ.get(
            "ALEX_LIVE_CAMERA_ENABLED", "").lower() in truthy
    if source == "display":
        return os.environ.get(
            "ALEX_LIVE_DISPLAY_ENABLED", "").lower() in truthy
    return True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _error(code: str, message: str, *, scope: str = "media",
           recoverable: bool = True) -> dict[str, Any]:
    return protocol_error(code, message, scope=scope, recoverable=recoverable)


class LiveMediaConnection:
    """One authenticated WebSocket's non-content visual authority state."""

    def __init__(self, *, workspace_id: str, actor_id: str, session_id: str,
                 connection_generation: int | None = None) -> None:
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.session_id = session_id
        self.connection_generation = connection_generation or secrets.randbits(31)
        self.started_monotonic = time.monotonic()
        self.challenges: dict[str, dict[str, Any]] = {}
        self.prepared: dict[str, dict[str, Any]] = {}
        self.active: dict[str, Any] | None = None
        self.generation = 0
        self.accepted_frames = 0
        self.forwarded_frames = 0
        self.dropped_frames = 0
        self.bytes_received = 0
        self.limit_warning_sent = False

    def remaining_seconds(self) -> int:
        """Conservative total provider-context budget, not per-share time."""
        elapsed = max(0.0, time.monotonic() - self.started_monotonic)
        return max(0, int(SESSION_SECONDS - elapsed - STOP_MARGIN_SECONDS))

    async def consent_request(self, frame: dict[str, Any]) -> dict[str, Any]:
        source = str(frame.get("source") or "")
        if source not in {item.value for item in VisualSource}:
            return _error("protocol_violation", "Choose camera or screen sharing.",
                          scope="consent")
        try:
            existing = await firestore.find_active_media_consent_grant(
                self.workspace_id, self.actor_id, self.session_id, source)
        except Exception:
            return _error(
                "media_authority_unavailable",
                "Sharing consent could not be checked. Voice may continue.",
                scope="consent")
        if (existing
                and existing.get("disclosure_version") == DISCLOSURE_VERSION
                and existing.get("disclosure_hash") == _digest(DISCLOSURES[source])
                and str(existing.get("expires_at") or "") > _iso(_now())):
            return {
                "type": "consent.granted",
                "consent_grant_id": str(existing.get("grant_id")
                                        or existing.get("id") or ""),
                "source": source, "disclosure_version": DISCLOSURE_VERSION,
                "disclosure_hash": existing["disclosure_hash"],
                "expires_at": existing["expires_at"], "reused": True,
            }
        challenge_id = secrets.token_hex(16)
        nonce = secrets.token_urlsafe(32)
        expires = _now() + timedelta(minutes=2)
        disclosure = DISCLOSURES[source]
        self.challenges[challenge_id] = {
            "nonce": nonce, "source": source, "expires_at": expires,
            "used": False, "disclosure_hash": _digest(disclosure),
        }
        return {
            "type": "consent.challenge", "challenge_id": challenge_id,
            "challenge_nonce": nonce, "source": source,
            "disclosure": {"version": DISCLOSURE_VERSION,
                           "sha256": _digest(disclosure), "locale": "en",
                           "text": disclosure},
            "provider_summary": (
                "Google Gemini/Vertex AI processes sampled frames; Co-Founder "
                "does not retain live frames."),
            "expires_at": _iso(expires),
        }

    async def consent_accept(self, frame: dict[str, Any]) -> dict[str, Any]:
        challenge_id = str(frame.get("challenge_id") or "")
        supplied_nonce = str(frame.get("challenge_nonce") or "")
        source = str(frame.get("source") or "")
        challenge = self.challenges.get(challenge_id)
        if (not challenge or challenge.get("used")
                or challenge["expires_at"] <= _now()
                or challenge.get("source") != source
                or not hmac.compare_digest(str(challenge.get("nonce")), supplied_nonce)):
            return _error(
                "consent_replay" if challenge and challenge.get("used")
                else "consent_challenge_invalid",
                "That sharing disclosure expired or changed. Try sharing again.",
                scope="consent")
        challenge["used"] = True
        grant_id = secrets.token_hex(16)
        expires = _now() + timedelta(hours=24)
        try:
            await firestore.create_media_consent_grant({
                "grant_id": grant_id, "workspace_id": self.workspace_id,
                "founder_id": self.workspace_id, "actor_id": self.actor_id,
                "session_id": self.session_id, "source": source,
                "disclosure_version": DISCLOSURE_VERSION,
                "disclosure_hash": challenge["disclosure_hash"],
                "challenge_id": challenge_id, "accepted_at": _iso(_now()),
                "status": "ACTIVE", "expires_at": _iso(expires),
                "retention_expires_at": _iso(expires),
            })
        except Exception:
            return _error(
                "media_authority_unavailable",
                "Sharing consent could not be recorded. Voice may continue.",
                scope="consent")
        return {"type": "consent.granted", "consent_grant_id": grant_id,
                "source": source, "disclosure_version": DISCLOSURE_VERSION,
                "disclosure_hash": challenge["disclosure_hash"],
                "expires_at": _iso(expires)}

    async def media_prepare(self, frame: dict[str, Any]) -> dict[str, Any]:
        request_id = str(frame.get("client_request_id") or "")
        share_id = str(frame.get("share_id") or "")
        source = str(frame.get("source") or "")
        grant_id = str(frame.get("consent_grant_id") or "")
        if (not request_id or len(request_id) > 128 or not share_id
                or len(share_id) > 128 or source not in DISCLOSURES or not grant_id):
            return _error("protocol_violation", "Sharing preparation is invalid.")
        previous = self.prepared.get(request_id)
        identity = (share_id, source, grant_id)
        if previous:
            if previous["identity"] != identity:
                return _error("protocol_violation", "Sharing preparation conflicts.")
            if previous["expires_at"] > _now() and not previous["used"]:
                return previous["response"]
        try:
            grant = await firestore.get_media_consent_grant(grant_id)
        except Exception:
            return _error(
                "media_authority_unavailable",
                "Sharing authority could not be checked. Voice may continue.")
        if (not grant or grant.get("workspace_id") != self.workspace_id
                or grant.get("actor_id") != self.actor_id
                or grant.get("session_id") != self.session_id
                or grant.get("source") != source or grant.get("status") != "ACTIVE"
                or grant.get("disclosure_version") != DISCLOSURE_VERSION
                or str(grant.get("expires_at") or "") <= _iso(_now())):
            return _error("consent_challenge_invalid",
                          "Sharing consent is no longer valid.", scope="consent")
        remaining = self.remaining_seconds()
        if source != VisualSource.CAMERA.value and remaining < MIN_USEFUL_SECONDS:
            return _error(
                "visual_session_budget_low",
                "This live conversation is near its vision limit. Add a still image "
                "or start a fresh conversation; voice may continue.")
        nonce = secrets.token_urlsafe(32)
        expires = _now() + timedelta(seconds=30)
        response = {"type": "media.prepared", "share_id": share_id,
                    "source": source, "start_nonce": nonce,
                    "expires_at": _iso(expires),
                    "visual_admission_remaining_seconds": remaining}
        self.prepared[request_id] = {
            "identity": identity, "nonce": nonce, "expires_at": expires,
            "used": False, "response": response,
        }
        return response

    async def media_start(self, frame: dict[str, Any]) -> dict[str, Any]:
        request_id = str(frame.get("client_request_id") or "")
        share_id = str(frame.get("share_id") or "")
        source = str(frame.get("source") or "")
        grant_id = str(frame.get("consent_grant_id") or "")
        nonce = str(frame.get("start_nonce") or "")
        if self.active:
            return _error("source_conflict", "Stop the current visual share first.")
        prepared = next((item for item in self.prepared.values()
                         if item["identity"] == (share_id, source, grant_id)
                         and hmac.compare_digest(str(item["nonce"]), nonce)), None)
        if (not request_id or not prepared or prepared["used"]
                or prepared["expires_at"] <= _now()):
            return _error("start_nonce_invalid",
                          "Sharing authorization expired. Choose Share again.")
        capture = frame.get("capture")
        if not isinstance(capture, dict) or capture.get("mime_type") != "image/jpeg":
            return _error("protocol_violation", "Only bounded JPEG frames are supported.")
        try:
            grant = await firestore.get_media_consent_grant(grant_id)
        except Exception:
            return _error(
                "media_authority_unavailable",
                "Sharing authority could not be checked. Voice may continue.")
        if (not grant or grant.get("workspace_id") != self.workspace_id
                or grant.get("actor_id") != self.actor_id
                or grant.get("session_id") != self.session_id
                or grant.get("source") != source or grant.get("status") != "ACTIVE"
                or grant.get("disclosure_version") != DISCLOSURE_VERSION
                or str(grant.get("expires_at") or "") <= _iso(_now())):
            return _error("consent_challenge_invalid",
                          "Sharing consent is no longer valid.", scope="consent")
        prepared["used"] = True
        generation = self.generation + 1
        started = _now()
        try:
            await firestore.create_live_media_share({
                "share_id": share_id, "schema_version": 1,
                "workspace_id": self.workspace_id, "founder_id": self.workspace_id,
                "actor_id": self.actor_id, "session_id": self.session_id,
                "source": source.upper(), "consent_grant_id": grant_id,
                "disclosure_version": DISCLOSURE_VERSION,
                "disclosure_hash": _digest(DISCLOSURES[source]),
                "status": "ACTIVE", "generation": generation,
                "effective_profile": {"mime_type": "image/jpeg", "max_width": 768,
                                      "max_height": 768, "max_pixels": MAX_PIXELS,
                                      "fps": 1, "max_frame_bytes": MAX_FRAME_BYTES},
                "started_at": _iso(started), "ended_at": None, "end_reason": None,
                "frames_received": 0, "frames_forwarded": 0, "frames_dropped": 0,
                "bytes_received": 0, "throttle_count": 0,
                "retention_expires_at": _iso(started + timedelta(days=30)),
            })
        except Exception:
            return _error(
                "media_authority_unavailable",
                "Sharing could not be started safely. Voice may continue.")
        self.generation = generation
        self.accepted_frames = 0
        self.forwarded_frames = 0
        self.dropped_frames = 0
        self.bytes_received = 0
        self.active = {
            "share_id": share_id, "source": source, "grant_id": grant_id,
            "generation": generation, "last_seq": -1,
            "last_frame_at": 0.0, "started_at": started,
        }
        return {"type": "media.started", "share_id": share_id,
                "generation": generation, "source": source,
                "effective": {"bounding_box": {"max_width": 768,
                                                 "max_height": 768,
                                                 "max_pixels": MAX_PIXELS},
                              "fit": "preserve_aspect_no_upscale", "fps": 1},
                "started_at": _iso(started)}

    async def media_frame(self, frame: dict[str, Any],
                          forward: ForwardFrame) -> dict[str, Any] | None:
        active = self.active
        if (not active or frame.get("share_id") != active["share_id"]
                or frame.get("generation") != active["generation"]):
            self.dropped_frames += 1
            return _error("stale_generation", "That visual frame is no longer active.")
        try:
            seq = int(frame.get("seq"))
        except (TypeError, ValueError):
            return _error("invalid_frame", "A visual frame was invalid.")
        if seq <= active["last_seq"]:
            return _error("stale_generation", "That visual frame arrived out of order.")
        encoded = frame.get("data")
        if not isinstance(encoded, str) or len(encoded) > 333_336:
            return _error("frame_too_large", "A visual frame exceeded the size limit.")
        now_mono = time.monotonic()
        if active["last_frame_at"] and now_mono - active["last_frame_at"] < 0.75:
            self.dropped_frames += 1
            return {"type": "media.throttle", "share_id": active["share_id"],
                    "generation": active["generation"], "effective_fps": 1,
                    "reason": "server_backpressure", "retry_after_ms": 750}
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception:
            return _error("invalid_frame", "A visual frame was invalid.")
        if not data or len(data) > MAX_FRAME_BYTES:
            return _error("frame_too_large", "A visual frame exceeded the size limit.")
        try:
            image = Image.open(io.BytesIO(data))
            if image.format != "JPEG":
                raise ValueError("mime")
            width, height = image.size
            if (width < 1 or height < 1 or width > 768 or height > 768
                    or width * height > MAX_PIXELS):
                raise ValueError("dimensions")
            image.verify()
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
            return _error("invalid_frame", "A visual frame was invalid.")
        # An explicitly consented camera share has no separate short visual
        # timeout. Its lifetime is bounded by the visible client share state,
        # the voice socket/session, and the existing stop/fail-safe events.
        # Display remains separately gated and retains its rollout budget.
        if (active["source"] != VisualSource.CAMERA.value
                and self.remaining_seconds() <= 0):
            stopped = await self.stop(MediaEndReason.BUDGET_EXHAUSTED.value)
            if stopped.get("type") == "media.stopped":
                stopped["message"] = (
                    "Visual sharing reached its configured session limit and ended. "
                    "Voice remains connected; choose the source and Share again for "
                    "a fresh visual share.")
                return stopped
            return _error(
                "visual_context_limit",
                "Live vision reached its session limit; voice may continue.")
        active["last_seq"] = seq
        active["last_frame_at"] = now_mono
        self.accepted_frames += 1
        self.bytes_received += len(data)
        await forward(data)
        remaining = self.remaining_seconds()
        if (active["source"] != VisualSource.CAMERA.value
                and remaining <= 30 and not self.limit_warning_sent):
            self.limit_warning_sent = True
            return {
                "type": "media.limit_warning", "scope": "media",
                "remaining_seconds": remaining,
                "message": "Live vision is nearing its session limit; voice may continue.",
            }
        return None

    async def record_forwarded(self, generation: int) -> dict[str, Any] | None:
        """Record a frame only after the provider request queue accepted it."""
        active = self.active
        if not active or active.get("generation") != generation:
            return _error("stale_generation", "That visual frame is no longer active.")
        self.forwarded_frames += 1
        try:
            await firestore.update_live_media_share_counters(
                self.workspace_id, self.session_id, active["share_id"],
                frames_received=self.accepted_frames,
                frames_forwarded=self.forwarded_frames,
                frames_dropped=self.dropped_frames,
                bytes_received=self.bytes_received)
        except Exception:
            self.active = None
            return _error(
                "media_authority_unavailable",
                "Live sharing stopped because its audit state was unavailable. "
                "Voice may continue.")
        return None

    async def stop(self, end_reason: str, *, share_id: str = "",
                   generation: int | None = None) -> dict[str, Any]:
        if end_reason not in {item.value for item in MediaEndReason}:
            return _error("protocol_violation", "Sharing stop reason is invalid.")
        active = self.active
        if not active:
            return {"type": "media.stopped", "share_id": "", "generation": 0,
                    "status": "STOPPED", "end_reason": end_reason,
                    "stopped_at": _iso(_now()), "already_stopped": True}
        if ((share_id and share_id != active["share_id"])
                or (generation is not None
                    and generation != active["generation"])):
            return _error(
                "stale_generation",
                "That stop belongs to an older visual share generation.")
        self.active = None
        terminal = "PAUSED" if end_reason == MediaEndReason.PAUSED.value else "STOPPED"
        try:
            await firestore.stop_live_media_share(
                self.workspace_id, self.session_id, active["share_id"],
                status=terminal, end_reason=end_reason,
                frames_received=self.accepted_frames,
                frames_forwarded=self.forwarded_frames,
                frames_dropped=self.dropped_frames,
                bytes_received=self.bytes_received)
        except Exception:
            return _error(
                "media_authority_unavailable",
                "Sharing stopped locally; its final audit status is unavailable. "
                "Voice may continue.")
        return {"type": "media.stopped", "share_id": active["share_id"],
                "generation": active["generation"], "status": terminal,
                "end_reason": end_reason, "stopped_at": _iso(_now()),
                "already_stopped": False}

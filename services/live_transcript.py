"""Incremental idempotent final-turn authority for Gemini Live captions."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from google.adk.events import Event
from google.adk.sessions import BaseSessionService
from google.genai import types


def _normalize_caption(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def _merge_caption_text(existing: str, incoming: str) -> str:
    """Merge cumulative or segmented provider captions without repetition."""
    existing = _normalize_caption(existing)
    incoming = _normalize_caption(incoming)
    if not existing:
        return incoming
    if not incoming or incoming == existing:
        return existing
    if incoming.startswith(existing):
        return incoming
    if existing.startswith(incoming) or existing.endswith(incoming):
        return existing
    left = existing.split()
    right = incoming.split()
    for width in range(min(len(left), len(right)), 0, -1):
        if left[-width:] == right[:width]:
            return " ".join(left + right[width:])
    return f"{existing} {incoming}"


class LiveTranscriptAccumulator:
    """Collect provider caption segments until one durable Live turn boundary.

    Gemini may mark multiple output-transcription chunks finished within one
    response. A finished chunk is therefore presentation evidence, not a
    durable turn boundary. Only ``turn_complete``/interruption flushes the
    accumulated speaker text to ``LiveTranscriptCommitter``.
    """

    def __init__(self) -> None:
        self._final = {"user": "", "agent": ""}
        self._partial = {"user": "", "agent": ""}

    def add(self, *, speaker: str, text: str, finished: bool) -> str:
        if speaker not in self._final:
            return ""
        normalized = _normalize_caption(text)
        if not normalized:
            return self.preview(speaker)
        if finished:
            self._final[speaker] = _merge_caption_text(
                self._final[speaker], normalized)
            self._partial[speaker] = ""
        else:
            self._partial[speaker] = normalized
        return self.preview(speaker)

    def preview(self, speaker: str) -> str:
        if speaker not in self._final:
            return ""
        return _merge_caption_text(
            self._final[speaker], self._partial[speaker])

    def final_text(self, speaker: str) -> str:
        return self._final.get(speaker, "")

    def clear(self) -> None:
        for speaker in self._final:
            self._final[speaker] = ""
            self._partial[speaker] = ""


class CanonicalLiveSessionService(BaseSessionService):
    """Delegate ADK session operations but suppress raw Live transcript writes.

    ADK normally appends non-partial provider transcription events before the
    WebSocket consumer sees them. The app commits the same final text with a
    server turn id below, so retaining both would create duplicate conversation
    turns. Function/tool/control events continue through unchanged.
    """

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Any:
        """Create a session through the canonical durable delegate."""
        return await self._delegate.create_session(
            app_name=app_name,
            user_id=user_id,
            state=state,
            session_id=session_id,
        )

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Any = None,
    ) -> Any:
        """Read a session through the canonical durable delegate."""
        return await self._delegate.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            config=config,
        )

    async def list_sessions(
        self,
        *,
        app_name: str,
        user_id: str | None = None,
    ) -> Any:
        """List sessions through the canonical durable delegate."""
        return await self._delegate.list_sessions(
            app_name=app_name,
            user_id=user_id,
        )

    async def delete_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> None:
        """Delete a session through the canonical durable delegate."""
        await self._delegate.delete_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )

    async def get_user_state(
        self,
        *,
        app_name: str,
        user_id: str,
    ) -> dict[str, Any]:
        """Read user state without bypassing the canonical delegate."""
        return await self._delegate.get_user_state(
            app_name=app_name,
            user_id=user_id,
        )

    async def flush(self) -> None:
        """Flush buffered events through the canonical durable delegate."""
        await self._delegate.flush()

    async def append_event(self, session: Any, event: Event) -> Any:
        metadata = dict(getattr(event, "custom_metadata", None) or {})
        raw_transcription = (
            getattr(event, "input_transcription", None) is not None
            or getattr(event, "output_transcription", None) is not None)
        content = getattr(event, "content", None)
        parts = list(getattr(content, "parts", None) or [])
        pure_model_text = bool(parts) and all(
            bool(getattr(part, "text", None))
            and not getattr(part, "function_call", None)
            and not getattr(part, "function_response", None)
            and not getattr(part, "inline_data", None)
            and not getattr(part, "file_data", None)
            for part in parts)
        if ((raw_transcription or pure_model_text)
                and not metadata.get("canonical_live_transcript")):
            return event
        return await self._delegate.append_event(session, event)


class LiveTranscriptCommitter:
    """Write one canonical ADK event per server-owned Live turn and speaker."""

    def __init__(self, *, session_service: Any, app_name: str,
                 user_id: str, session_id: str,
                 connection_generation: int,
                 session: Any | None = None) -> None:
        self.session_service = session_service
        self.app_name = app_name
        self.user_id = user_id
        self.session_id = session_id
        self.connection_generation = connection_generation
        self._session = session
        self.ordinal = 0
        self.current_turn_id = ""
        secret = (os.environ.get("APP_SESSION_SECRET")
                  or os.environ.get("APP_AUTH_TOKEN") or "local-live-turn-secret")
        self._secret = secret.encode("utf-8")

    def new_turn_id(self) -> str:
        """Create the next opaque server turn identity for this connection."""
        self.ordinal += 1
        material = (f"{self.app_name}:{self.user_id}:{self.session_id}:"
                    f"{self.connection_generation}:{self.ordinal}")
        self.current_turn_id = "lt_" + hmac.new(
            self._secret, material.encode(), hashlib.sha256).hexdigest()[:32]
        return self.current_turn_id

    def ensure_turn_id(self) -> str:
        return self.current_turn_id or self.new_turn_id()

    def _event_id(self, turn_id: str, speaker: str) -> str:
        material = f"live-event:{self.app_name}:{self.user_id}:{self.session_id}:{turn_id}:{speaker}"
        return "le_" + hmac.new(
            self._secret, material.encode(), hashlib.sha256).hexdigest()[:32]

    def _adopt_exact_revision(self, fresh: Any) -> None:
        """Advance the shared Runner session after exact lost-ack recovery."""
        if self._session is None:
            self._session = fresh
            return
        self._session.events = list(getattr(fresh, "events", None) or [])
        self._session.state = dict(getattr(fresh, "state", None) or {})
        self._session.last_update_time = getattr(
            fresh, "last_update_time", None)
        if hasattr(fresh, "_storage_update_marker"):
            self._session._storage_update_marker = (
                fresh._storage_update_marker)

    async def commit(self, *, speaker: str, turn_id: str,
                     text: str, source: str) -> dict[str, Any]:
        """Commit a finalized turn before acknowledging its final caption."""
        normalized = " ".join(str(text or "").split()).strip()
        if speaker not in {"user", "agent"} or not normalized or not turn_id:
            return {"status": "error", "error": True,
                    "error_code": "transcript_contract_invalid",
                    "message": "Final transcript was not saved yet."}
        event_id = self._event_id(turn_id, speaker)
        content_hash = hashlib.sha256(normalized.encode()).hexdigest()
        event = Event(
            id=event_id,
            author="user" if speaker == "user" else "co_founder",
            invocation_id=f"live:{turn_id}",
            content=types.Content(
                role="user" if speaker == "user" else "model",
                parts=[types.Part.from_text(text=normalized)]),
            custom_metadata={
                "live_turn_id": turn_id, "speaker": speaker,
                "source": source, "content_sha256": content_hash,
                "connection_generation": self.connection_generation,
                "canonical_live_transcript": True,
            },
        )
        session = self._session
        if session is None:
            session = await self.session_service.get_session(
                app_name=self.app_name, user_id=self.user_id,
                session_id=self.session_id)
            self._session = session
        if session is None:
            return {"status": "error", "error": True,
                    "error_code": "transcript_commit_failed",
                    "message": "Final transcript was not saved yet."}
        try:
            await self.session_service.append_event(session, event)
            return {"status": "success", "event_id": event_id,
                    "turn_id": turn_id, "text": normalized, "created": True}
        except Exception:
            fresh = await self.session_service.get_session(
                app_name=self.app_name, user_id=self.user_id,
                session_id=self.session_id)
            existing = next((item for item in (fresh.events or [])
                             if getattr(item, "id", "") == event_id), None) \
                if fresh else None
            metadata = dict(getattr(existing, "custom_metadata", None) or {})
            if (existing is not None
                    and hmac.compare_digest(str(metadata.get("content_sha256") or ""),
                                            content_hash)
                    and metadata.get("speaker") == speaker
                    and metadata.get("source") == source):
                self._adopt_exact_revision(fresh)
                return {"status": "success", "event_id": event_id,
                        "turn_id": turn_id, "text": normalized,
                        "created": False, "idempotent": True}
            return {"status": "error", "error": True,
                    "error_code": "transcript_commit_failed",
                    "message": "Final transcript was not saved yet."}

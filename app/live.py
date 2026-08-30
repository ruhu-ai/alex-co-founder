"""Real-time voice surface (docs/04, 10): browser <-> WebSocket <-> ADK
run_live <-> Gemini Live native-audio model.

The SAME orchestrator agent, state machine, tools, and guards as the text
pipeline — only the modality changes. Voice turns land in the founder's
existing chat session (same app name, same session id), so transcripts appear
in the chat log and the state machine stays the single source of truth.

Protocol (JSON text frames):
  client -> server: {"audio": "<base64 pcm16 16kHz>"} | {"text": "..."} | {"close": true}
  server -> client: {"audio": "<base64 pcm16 24kHz>"}
                  | {"transcript": {"who": "you"|"agent", "text": str, "finished": bool}}
                  | {"turn_complete": true} | {"interrupted": true} | {"error": str}
"""

import asyncio
import base64
import json
import logging
import os
from urllib.parse import urlsplit

from fastapi import WebSocket, WebSocketDisconnect
from google.adk.agents.live_request_queue import LiveRequest, LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.runners import Runner
from google.adk.sessions.base_session_service import GetSessionConfig
from google.genai import types

from agents.co_founder.agent import build_root_agent
from agents.co_founder.config import LIVE_MODEL_ID
from services import (
    conversation_history,
    live_attention,
    live_media,
    live_resumption,
    live_transcript,
    live_vision_protocol,
)

logger = logging.getLogger(__name__)
# ADK 2.8 logs provider resumption updates (including the opaque handle) at
# INFO/DEBUG before yielding them to application code. Keep those two SDK
# modules at WARNING so provider credentials never enter application logs.
logging.getLogger(
    "google_adk.google.adk.flows.llm_flows.base_llm_flow").setLevel(logging.WARNING)
logging.getLogger(
    "google_adk.google.adk.models.gemini_llm_connection").setLevel(logging.WARNING)


def _bounded_seconds(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(os.environ.get(name, default)), high))
    except (TypeError, ValueError):
        return default


# Context compression and provider session resumption allow a conversation to
# outlive one model connection.  The application still applies a hard bounded
# call budget and a short idle timeout; neither can be disabled by a client.
LIVE_MAX_SESSION_SECONDS = _bounded_seconds(
    "ALEX_LIVE_MAX_SESSION_SECONDS", 60 * 60, 5 * 60, 4 * 60 * 60)
LIVE_IDLE_SECONDS = _bounded_seconds(
    "ALEX_LIVE_IDLE_SECONDS", 90, 15, 5 * 60)
LIVE_CONTEXT_TRIGGER_TOKENS = _bounded_seconds(
    "ALEX_LIVE_CONTEXT_TRIGGER_TOKENS", 64_000, 5_000, 128_000)
LIVE_CONTEXT_TARGET_TOKENS = min(
    _bounded_seconds(
        "ALEX_LIVE_CONTEXT_TARGET_TOKENS", 32_000, 1_000, 120_000),
    LIVE_CONTEXT_TRIGGER_TOKENS - 1_000,
)


def _pcm16_has_activity(data: bytes, *, threshold: int = 350) -> bool:
    """Cheap bounded speech-energy check; provider VAD remains authoritative."""
    if len(data) < 2:
        return False
    for offset in range(0, len(data) - 1, 16):
        sample = int.from_bytes(data[offset:offset + 2], "little", signed=True)
        if abs(sample) >= threshold:
            return True
    return False


class LatestVisualLiveRequestQueue(LiveRequestQueue):
    """Keep at most one unsent visual request while preserving Live compatibility."""

    def __init__(self) -> None:
        super().__init__()
        self._visual_signal = LiveRequest()
        self._visual_pending: tuple[LiveRequest, tuple[str, int]] | None = None
        self._visual_signal_enqueued = False
        self._audio_paused = False
        self.on_visual_dequeued = None

    def pause_audio(self) -> None:
        """Fence both new and already queued microphone frames."""
        self._audio_paused = True

    def resume_audio(self) -> None:
        """Allow new microphone frames after an explicit resume."""
        self._audio_paused = False

    def send_realtime(self, blob: types.Blob) -> None:
        if self._audio_paused and str(blob.mime_type or "").startswith("audio/"):
            return
        super().send_realtime(blob)

    def send_visual_latest(self, blob: types.Blob, *, source: str,
                           generation: int) -> bool:
        """Schedule the latest image and return whether an older one was dropped."""
        replaced = self._visual_pending is not None
        self._visual_pending = (LiveRequest(blob=blob), (source, generation))
        if not self._visual_signal_enqueued:
            self._visual_signal_enqueued = True
            self._queue.put_nowait(self._visual_signal)
        return replaced

    def clear_visual(self) -> None:
        self._visual_pending = None

    async def get(self) -> LiveRequest:
        while True:
            request = await super().get()
            if (self._audio_paused and request.blob is not None
                    and str(request.blob.mime_type or "").startswith("audio/")):
                continue
            if request is not self._visual_signal:
                return request
            pending = self._visual_pending
            self._visual_pending = None
            self._visual_signal_enqueued = False
            if pending is None:
                continue
            visual_request, metadata = pending
            if self.on_visual_dequeued is not None:
                accepted = await self.on_visual_dequeued(*metadata)
                if not accepted:
                    continue
            return visual_request

# Same app name as the text pipeline -> voice turns share the founder's chat
# session and its state. live=True builds the root AND the sub-agents on the
# Live native-audio model, so a transfer mid-conversation (scout, drafter,
# form_filler, ...) continues the bidi session on a model that supports it
# instead of erroring on a text-only tier.
live_app = App(name="co_founder",
               root_agent=build_root_agent(Gemini(model=LIVE_MODEL_ID), live=True))


def register_live(app, session_service, founder_id: str) -> None:
    """Mount the bidi voice websocket on the FastAPI app."""
    # One runner owns this surface for the process lifetime. Sessions remain
    # isolated by user/session ids passed to run_live.
    runner = Runner(
        app=live_app,
        session_service=live_transcript.CanonicalLiveSessionService(
            session_service),
        # M2 recall is text-conversation-only and server-assembled. Live/voice
        # performs zero optional-memory I/O, including in private sessions.
        memory_service=None)

    @app.websocket("/live/{session_id}")
    async def live_ws(websocket: WebSocket, session_id: str) -> None:
        # HTTP middleware never sees websocket handshakes — gate here. The
        # app_auth cookie from the ?key= bootstrap rides the handshake.
        from app import auth

        origin = str(websocket.headers.get("origin") or "")
        host = str(websocket.headers.get("host") or "")
        if origin or os.environ.get("K_SERVICE"):
            parsed = urlsplit(origin)
            if (parsed.scheme not in {"http", "https"} or parsed.netloc != host
                    or parsed.path not in {"", "/"}):
                await websocket.close(code=4403)
                return

        workspace_id = founder_id
        actor_id = founder_id
        live_claims = None
        selected_workspace = ""
        if os.environ.get("K_SERVICE"):
            from services.actor_identity import resolve_actor_from_claims

            live_claims = auth.session_claims(websocket)
            selected_workspace = str(
                websocket.query_params.get("workspace_id") or "")
            if not live_claims:
                await websocket.close(code=4401)
                return
            principal = await resolve_actor_from_claims(
                live_claims, workspace_id=selected_workspace)
            if isinstance(principal, dict):
                await websocket.close(code=4403)
                return
            workspace_id = principal.workspace_id
            actor_id = principal.actor_id
        elif not auth.websocket_is_founder(websocket):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        session = await session_service.get_session(
            app_name=live_app.name, user_id=workspace_id, session_id=session_id,
            config=GetSessionConfig(num_recent_events=100))
        if session is None:
            session = await session_service.create_session(
                app_name=live_app.name, user_id=workspace_id, session_id=session_id)
        # Live receives the same server-derived actor/workspace projection as
        # text. Tools still re-read durable membership at their own boundary.
        from agents.co_founder import state_schema as ss
        session.state[ss.K_ACTOR_ID] = actor_id
        session.state[ss.K_USER_PROFILE_ID] = workspace_id
        session_mode = str(session.state.get(ss.K_MEMORY_MODE) or "PRIVATE")
        session.state[ss.K_CONTINUITY_CONTEXT] = conversation_history.build_envelope(
            session_id=session_id,
            session_mode=session_mode,
            saved_context_available=None,
        ).instruction()

        queue = LatestVisualLiveRequestQueue()
        resumption_handle = await live_resumption.load(
            workspace_id=workspace_id, session_id=session_id)
        run_config = RunConfig(
            response_modalities=["AUDIO"],
            streaming_mode=StreamingMode.BIDI,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=types.SessionResumptionConfig(
                handle=resumption_handle or None),
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=LIVE_CONTEXT_TRIGGER_TOKENS,
                sliding_window=types.SlidingWindow(
                    target_tokens=LIVE_CONTEXT_TARGET_TOKENS),
            ),
            get_session_config=GetSessionConfig(num_recent_events=100),
        )
        media = live_media.LiveMediaConnection(
            workspace_id=workspace_id, actor_id=actor_id,
            session_id=session_id)
        transcript = live_transcript.LiveTranscriptCommitter(
            session_service=session_service, app_name=live_app.name,
            user_id=workspace_id, session_id=session_id,
            connection_generation=media.connection_generation,
            session=session)
        transcript_buffer = live_transcript.LiveTranscriptAccumulator()
        caption_revisions = {"user": 0, "agent": 0}
        activity_revision = 0
        voice_paused = False
        explicit_close = False
        attention_held = False
        attention_hold_enabled = False
        downstream_armed = True
        pause_revision = 0
        control_receipts: dict[str, dict] = {}
        loop = asyncio.get_running_loop()
        connection_started = loop.time()
        last_meaningful_activity = connection_started

        send_lock = asyncio.Lock()

        async def send(payload: dict) -> None:
            async with send_lock:
                await websocket.send_text(json.dumps(payload))

        async def emit_activity(state: str, reason_code: str,
                                *, turn_id: str = "") -> None:
            nonlocal activity_revision
            activity_revision += 1
            await send({
                "type": "agent.activity",
                "connection_generation": media.connection_generation,
                "activity_revision": activity_revision,
                "live_turn_id": turn_id or transcript.ensure_turn_id(),
                "state": state,
                "reason_code": reason_code,
            })

        async def flush_transcript_turn(*, interrupted: bool = False) -> None:
            """Commit at most one chronological event per speaker/Live turn."""
            turn_id = transcript.current_turn_id
            if not turn_id:
                transcript_buffer.clear()
                return
            for speaker, source in (
                    ("user", "input_transcription"),
                    ("agent", "output_transcription")):
                # The provider's turn boundary is authoritative even if its
                # last transcription update was still marked partial.
                text = transcript_buffer.preview(speaker)
                if not text:
                    continue
                committed = await transcript.commit(
                    speaker=speaker, turn_id=turn_id,
                    text=text, source=source)
                if committed.get("status") != "success":
                    await send(live_vision_protocol.protocol_error(
                        "transcript_commit_failed",
                        "Final transcript was not saved yet.",
                        scope="transcript"))
                    continue
                caption_revisions[speaker] += 1
                await send({
                    "type": "caption", "speaker": speaker,
                    "turn_id": turn_id,
                    "revision": caption_revisions[speaker],
                    "text": text, "final": True,
                    "interrupted": interrupted,
                    "final_event_id": committed["event_id"],
                    "source": source,
                })
                if speaker == "user":
                    await emit_activity(
                        "USER_TURN_FINAL", "input_transcription_final",
                        turn_id=turn_id)
            transcript_buffer.clear()
            transcript.current_turn_id = ""

        def touch_activity() -> None:
            nonlocal last_meaningful_activity
            last_meaningful_activity = loop.time()

        async def visual_access_current() -> bool:
            """Re-resolve membership and session authority at every share start."""
            current = await session_service.get_session(
                app_name=live_app.name, user_id=workspace_id,
                session_id=session_id,
                config=GetSessionConfig(num_recent_events=1))
            if current is None:
                return False
            if not os.environ.get("K_SERVICE"):
                return True
            from services.actor_identity import resolve_actor_from_claims

            refreshed = await resolve_actor_from_claims(
                live_claims, workspace_id=selected_workspace)
            return (not isinstance(refreshed, dict)
                    and refreshed.workspace_id == workspace_id
                    and refreshed.actor_id == actor_id)

        def clear_visual_frames() -> None:
            queue.clear_visual()

        async def on_visual_dequeued(source: str, generation: int) -> bool:
            """Fence and audit a visual frame immediately before provider ingress."""
            from services import live_visual_context

            active = media.active or {}
            if (active.get("generation") != generation
                    or active.get("source") != source):
                return False
            result = await media.record_forwarded(generation)
            if result:
                clear_visual_frames()
                await send(result)
                return False
            live_visual_context.mark(
                workspace_id=workspace_id, session_id=session_id,
                source=source,
                connection_generation=media.connection_generation)
            return True

        queue.on_visual_dequeued = on_visual_dequeued

        async def upstream() -> None:
            """Browser frames -> LiveRequestQueue."""
            nonlocal voice_paused, attention_held, attention_hold_enabled
            nonlocal downstream_armed, pause_revision, explicit_close
            application_frames = 0
            negotiated_v2 = False
            enabled_visual_sources: set[str] = set()
            try:
                while True:
                    raw_frame = await websocket.receive_text()
                    if len(raw_frame.encode("utf-8")) > live_media.MAX_WIRE_FRAME_BYTES:
                        await send(live_vision_protocol.protocol_error(
                            "frame_too_large", "A Live frame exceeded the size limit.",
                            scope="media"))
                        continue
                    frame = json.loads(raw_frame)
                    if not isinstance(frame, dict):
                        await send(live_vision_protocol.protocol_error(
                            "protocol_violation", "Live frames must be objects."))
                        continue
                    application_frames += 1
                    frame_type = str(frame.get("type") or "")
                    if frame_type == "hello":
                        if application_frames != 1:
                            await send(live_vision_protocol.protocol_error(
                                "protocol_violation",
                                "The v2 hello must be the first application frame."))
                            continue
                        response = live_vision_protocol.negotiate_hello(
                            frame, visual_enabled={
                                "camera": live_media.enabled("camera"),
                                "display": live_media.enabled("display"),
                            })
                        await send(response)
                        negotiated_v2 = response.get("type") == "hello.ack"
                        attention_hold_enabled = bool(
                            (response.get("enabled") or {}).get("attention_hold"))
                        enabled_visual_sources = {
                            source for source in ("camera", "display")
                            if bool((response.get("enabled") or {}).get(source))}
                        continue
                    if negotiated_v2 and frame_type in {
                            "voice.pause", "voice.resume",
                            "voice.attention.command"}:
                        request_id = str(frame.get("client_request_id") or "")
                        expected_fields = ({"type", "client_request_id", "command_text"}
                                           if frame_type == "voice.attention.command"
                                           else {"type", "client_request_id"})
                        if (not request_id or len(request_id) > 128
                                or set(frame) != expected_fields):
                            await send(live_vision_protocol.protocol_error(
                                "protocol_violation",
                                "Live voice control is invalid.",
                                scope="voice"))
                            continue
                        previous = control_receipts.get(request_id)
                        if previous:
                            await send(previous)
                            continue
                        pause_revision += 1
                        if frame_type == "voice.pause":
                            voice_paused = True
                            attention_held = False
                            downstream_armed = False
                            queue.pause_audio()
                            clear_visual_frames()
                            stopped = await media.stop("paused")
                            if stopped.get("type") == "media.stopped":
                                await send(stopped)
                            transcript_buffer.clear()
                            transcript.current_turn_id = ""
                            response = {
                                "type": "voice.pause.ack",
                                "client_request_id": request_id,
                                "pause_revision": pause_revision,
                                "state": "PAUSED",
                            }
                        elif frame_type == "voice.resume":
                            voice_paused = False
                            attention_held = False
                            downstream_armed = False
                            queue.resume_audio()
                            response = {
                                "type": "voice.resume.ack",
                                "client_request_id": request_id,
                                "pause_revision": pause_revision,
                                "state": "AWAITING_FRESH_AUDIO",
                            }
                        elif voice_paused:
                            await send(live_vision_protocol.protocol_error(
                                "voice_paused",
                                "Resume microphone capture before resuming attention.",
                                scope="voice"))
                            continue
                        elif (not attention_hold_enabled or not attention_held
                              or live_attention.classify_addressed_attention_intent(
                                  str(frame.get("command_text") or ""))
                              != live_attention.RESUME):
                            await send(live_vision_protocol.protocol_error(
                                "attention_command_invalid",
                                "Only an on-device, addressed resume command can end Hold.",
                                scope="voice"))
                            continue
                        else:
                            attention_held = False
                            downstream_armed = False
                            queue.resume_audio()
                            response = {
                                "type": "voice.attention.resume.ack",
                                "client_request_id": request_id,
                                "pause_revision": pause_revision,
                                "state": "AWAITING_FRESH_AUDIO",
                            }
                        control_receipts[request_id] = response
                        if len(control_receipts) > 32:
                            control_receipts.pop(next(iter(control_receipts)))
                        await send(response)
                        continue
                    if negotiated_v2 and frame_type == "consent.request":
                        if str(frame.get("source") or "") not in enabled_visual_sources:
                            await send(live_vision_protocol.unavailable_v2_frame(
                                frame_type))
                        else:
                            await send(await media.consent_request(frame))
                    elif negotiated_v2 and frame_type == "consent.accept":
                        if str(frame.get("source") or "") not in enabled_visual_sources:
                            await send(live_vision_protocol.unavailable_v2_frame(
                                frame_type))
                        else:
                            await send(await media.consent_accept(frame))
                    elif negotiated_v2 and frame_type == "media.prepare":
                        if str(frame.get("source") or "") not in enabled_visual_sources:
                            await send(live_vision_protocol.unavailable_v2_frame(
                                frame_type))
                        else:
                            await send(await media.media_prepare(frame))
                    elif negotiated_v2 and frame_type == "media.start":
                        if str(frame.get("source") or "") not in enabled_visual_sources:
                            await send(live_vision_protocol.unavailable_v2_frame(
                                frame_type))
                        elif not await visual_access_current():
                            clear_visual_frames()
                            await media.stop("auth_revoked")
                            await send(live_vision_protocol.protocol_error(
                                "visual_authority_revoked",
                                "Sharing access changed. Voice may continue.",
                                scope="media"))
                        else:
                            touch_activity()
                            await send(await media.media_start(frame))
                    elif negotiated_v2 and frame_type == "media.frame":
                        async def _forward_visual(data: bytes) -> None:
                            source = str((media.active or {}).get("source") or "")
                            generation = int(
                                (media.active or {}).get("generation") or 0)
                            replaced = queue.send_visual_latest(
                                types.Blob(mime_type="image/jpeg", data=data),
                                source=source, generation=generation)
                            if replaced:
                                media.dropped_frames += 1

                        if not await visual_access_current():
                            clear_visual_frames()
                            await media.stop("auth_revoked")
                            await send(live_vision_protocol.protocol_error(
                                "visual_authority_revoked",
                                "Sharing access changed. Voice may continue.",
                                scope="media"))
                        else:
                            touch_activity()
                            response = await media.media_frame(frame, _forward_visual)
                            if response:
                                await send(response)
                    elif negotiated_v2 and frame_type == "media.stop":
                        try:
                            stop_generation = int(frame.get("generation"))
                        except (TypeError, ValueError):
                            stop_generation = None
                        stopped = await media.stop(
                            str(frame.get("end_reason") or "user_stop"),
                            share_id=str(frame.get("share_id") or ""),
                            generation=stop_generation)
                        if stopped.get("type") == "media.stopped":
                            clear_visual_frames()
                        await send(stopped)
                    elif "audio" in frame:
                        if voice_paused or attention_held:
                            continue
                        audio = base64.b64decode(frame["audio"], validate=True)
                        if _pcm16_has_activity(audio):
                            downstream_armed = True
                            touch_activity()
                        queue.send_realtime(types.Blob(
                            mime_type="audio/pcm;rate=16000",
                            data=audio))
                    elif "text" in frame:
                        if voice_paused or attention_held:
                            await send(live_vision_protocol.protocol_error(
                                "voice_paused" if voice_paused else "attention_held",
                                "Resume Alex before sending Live text." if voice_paused
                                else "Resume Alex's attention before sending Live text.",
                                scope="voice"))
                            continue
                        downstream_armed = True
                        typed_text = str(frame.get("text") or "").strip()[:20_000]
                        if not typed_text:
                            await send(live_vision_protocol.protocol_error(
                                "protocol_violation", "Typed Live text is empty."))
                            continue
                        touch_activity()
                        turn_id = transcript.ensure_turn_id()
                        committed = await transcript.commit(
                            speaker="user", turn_id=turn_id, text=typed_text,
                            source="typed_live")
                        if committed.get("status") != "success":
                            await send(live_vision_protocol.protocol_error(
                                "transcript_commit_failed",
                                "Typed text was not saved, so it was not sent.",
                                scope="transcript"))
                            continue
                        queue.send_content(types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=typed_text)]))
                        caption_revisions["user"] += 1
                        await send({
                            "type": "caption", "speaker": "user",
                            "turn_id": turn_id,
                            "revision": caption_revisions["user"],
                            "text": typed_text, "final": True,
                            "interrupted": False,
                            "final_event_id": committed["event_id"],
                            "source": "typed_live",
                        })
                        await emit_activity(
                            "USER_TURN_FINAL", "typed_turn_received",
                            turn_id=turn_id)
                    elif frame.get("close"):
                        explicit_close = True
                        queue.close()
                        return
                    elif negotiated_v2 and frame_type:
                        await send(live_vision_protocol.unavailable_v2_frame(
                            frame_type))
            except WebSocketDisconnect:
                await media.stop("socket_lost")
                queue.close()
            except Exception as exc:  # errors as data, even on the wire
                logger.warning("live upstream error: %s", exc)
                await media.stop("protocol_error")
                queue.close()

        last_resumption_handle = resumption_handle

        async def downstream() -> None:
            """ADK live events -> browser frames."""
            nonlocal activity_revision, attention_held, downstream_armed
            nonlocal pause_revision, last_resumption_handle
            try:
                async for event in runner.run_live(
                        session=session,
                        live_request_queue=queue, run_config=run_config):
                    resumption = getattr(
                        event, "live_session_resumption_update", None)
                    new_handle = str(getattr(resumption, "new_handle", "") or "")
                    if new_handle and new_handle != last_resumption_handle:
                        await live_resumption.save(
                            workspace_id=workspace_id,
                            session_id=session_id,
                            handle=new_handle,
                        )
                        last_resumption_handle = new_handle
                    if getattr(event, "go_away", None):
                        clear_visual_frames()
                        stopped = await media.stop("provider_unavailable")
                        await send(stopped)
                        await send(live_vision_protocol.protocol_error(
                            "provider_go_away",
                            "The provider is rotating this Live connection. Voice context "
                            "will resume when available; screen sharing stays stopped until "
                            "you explicitly start it again.",
                            scope="media"))
                    hold_transcription = getattr(event, "input_transcription", None)
                    hold_text = str(getattr(hold_transcription, "text", "") or "")
                    hold_finished = bool(getattr(
                        hold_transcription, "finished", False))
                    if (attention_hold_enabled and hold_finished and not voice_paused
                            and not attention_held
                            and live_attention.classify_addressed_attention_intent(
                                hold_text) == live_attention.HOLD):
                        # The provider supplies the active-call transcript used
                        # to recognize Hold. From this point, both ingress and
                        # egress are deterministically fenced. The command is
                        # deliberately omitted from captions and durable chat.
                        attention_held = True
                        downstream_armed = False
                        pause_revision += 1
                        queue.pause_audio()
                        clear_visual_frames()
                        stopped = await media.stop("paused")
                        if stopped.get("type") == "media.stopped":
                            await send(stopped)
                        transcript_buffer.clear()
                        transcript.current_turn_id = ""
                        await send({
                            "type": "voice.attention.held",
                            "pause_revision": pause_revision,
                            "state": "HELD",
                            "reason_code": "addressed_hold_intent",
                        })
                        touch_activity()
                        continue
                    if voice_paused or attention_held or not downstream_armed:
                        # Pause is a transcript/output fence as well as a local
                        # microphone teardown. Provider events already in flight
                        # are consumed but never played, captioned, or committed.
                        continue
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.inline_data and part.inline_data.mime_type.startswith("audio/"):
                                touch_activity()
                                await send({"audio": base64.b64encode(
                                    part.inline_data.data).decode()})
                            elif getattr(part, "function_call", None):
                                await emit_activity(
                                    "TOOL_PROCESSING", "function_call_started")
                            elif getattr(part, "function_response", None):
                                await emit_activity(
                                    "TOOL_FINISHED", "function_response_received")
                    for attr in ("input_transcription", "output_transcription"):
                        tr = getattr(event, attr, None)
                        if tr is not None and getattr(tr, "text", None):
                            speaker = "user" if attr == "input_transcription" else "agent"
                            if speaker == "user" and not transcript.current_turn_id:
                                transcript.new_turn_id()
                            turn_id = transcript.ensure_turn_id()
                            finished = bool(getattr(tr, "finished", False))
                            caption_revisions[speaker] += 1
                            preview = transcript_buffer.add(
                                speaker=speaker, text=tr.text,
                                finished=finished)
                            await send({
                                "type": "caption", "speaker": speaker,
                                "turn_id": turn_id,
                                "revision": caption_revisions[speaker],
                                "text": preview, "final": False,
                                "interrupted": False,
                                "final_event_id": None,
                                "source": ("input_transcription" if speaker == "user"
                                           else "output_transcription"),
                            })
                    if getattr(event, "interrupted", False):
                        completed_turn_id = transcript.current_turn_id
                        await flush_transcript_turn(interrupted=True)
                        await emit_activity(
                            "TURN_INTERRUPTED", "provider_interrupted",
                            turn_id=completed_turn_id)
                        await send({"interrupted": True})
                        session.state[ss.K_CONVERSATION_RECALL_ACTIVE] = False
                    if getattr(event, "turn_complete", False):
                        completed_turn_id = transcript.current_turn_id
                        await flush_transcript_turn()
                        await emit_activity(
                            "TURN_COMPLETE", "turn_complete",
                            turn_id=completed_turn_id)
                        await send({"turn_complete": True})
                        session.state[ss.K_CONVERSATION_RECALL_ACTIVE] = False
            except Exception as exc:
                logger.warning("live downstream error: %s", exc)
                try:
                    await send({"error": f"voice session ended: {exc}"[:300]})
                except Exception:
                    pass
            finally:
                queue.close()

        async def enforce_session_budget() -> None:
            """Close silent or overlong provider sessions regardless of client state."""
            nonlocal explicit_close
            while True:
                now = loop.time()
                max_remaining = LIVE_MAX_SESSION_SECONDS - (now - connection_started)
                # Conversational Hold deliberately sends no background audio,
                # so ordinary silence timeout must not masquerade as a failed
                # hold. The hard Live session ceiling still applies.
                idle_remaining = (max_remaining if attention_held else
                                  LIVE_IDLE_SECONDS - (now - last_meaningful_activity))
                remaining = min(max_remaining, idle_remaining)
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue
                reason = "max_duration" if max_remaining <= 0 else "idle_timeout"
                explicit_close = True
                message = (
                    "This live conversation reached its time limit. Start a new call "
                    "when you're ready."
                    if reason == "max_duration" else
                    "The live conversation closed after a quiet period. Start it again "
                    "when you're ready."
                )
                clear_visual_frames()
                await media.stop(reason)
                try:
                    await send({"type": "session.ending", "reason": reason,
                                "message": message})
                    await websocket.close(code=4000 if reason == "idle_timeout" else 4001)
                finally:
                    queue.close()
                return

        tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream()),
                 asyncio.create_task(enforce_session_budget())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if not task.cancelled() and task.exception():
                logger.warning("live task failed: %s", task.exception())
        await media.stop("socket_lost")
        if explicit_close:
            try:
                await live_resumption.delete(
                    workspace_id=workspace_id, session_id=session_id)
            except Exception:  # noqa: BLE001 - disconnect remains available
                logger.warning(
                    "live resumption cleanup failed for explicit close",
                    extra={"workspace_id": workspace_id, "session_id": session_id},
                )
        from services import live_visual_context
        live_visual_context.clear(
            workspace_id=workspace_id, session_id=session_id,
            connection_generation=media.connection_generation)

def register_hiring_live(app) -> None:
    """Mount H4S voice: token-scoped turns never enter generic Live/ADK chat."""
    @app.websocket("/live/hiring")
    async def hiring_live_ws(websocket: WebSocket) -> None:
        from app import auth
        from services.actor_identity import resolve_actor_from_claims
        from services.durable_store import production_store
        from services.hiring_run_answer import HiringRunAnswerService

        # H4S requires a signed login session for an ActorPrincipal; the legacy
        # founder token intentionally cannot authorize restricted hiring data.
        claims = auth.session_claims(websocket)
        if not claims:
            await websocket.close(code=4401)
            return
        principal = await resolve_actor_from_claims(
            claims, workspace_id=str(
                websocket.query_params.get("workspace_id") or ""))
        if isinstance(principal, dict):
            await websocket.close(code=4403)
            return
        await websocket.accept()
        service = HiringRunAnswerService(production_store())
        token = ""
        try:
            while True:
                frame = json.loads(await websocket.receive_text())
                if frame.get("close"):
                    return
                if not token:
                    token = str(frame.get("conversation_token") or "")
                    if not token:
                        await websocket.send_text(json.dumps({
                            "error": "A scoped Hiring Run conversation is required."}))
                        return
                    continue
                question = str(frame.get("text") or "")
                result = await service.answer(
                    principal=principal, conversation_token=token, question=question,
                    client_turn_id=str(frame.get("client_turn_id") or "")[:128])
                if result.get("error"):
                    await websocket.send_text(json.dumps({
                        "error": result.get("message", "Hiring voice turn failed.")}))
                    continue
                await websocket.send_text(json.dumps({
                    "transcript": {"who": "agent", "text": result["answer"],
                                   "finished": True},
                    "record_refs": result["record_refs"], "turn_complete": True}))
        except WebSocketDisconnect:
            return
        except Exception as exc:  # errors as data on this distinct wire protocol
            logger.warning("H4S voice error: %s", exc)
            try:
                await websocket.send_text(json.dumps({"error": "Hiring voice session ended."}))
            except Exception:
                pass

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

from fastapi import WebSocket, WebSocketDisconnect
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.runners import Runner
from google.genai import types

from agents.co_founder.agent import build_root_agent
from agents.co_founder.config import LIVE_MODEL_ID

logger = logging.getLogger(__name__)

# Same app name as the text pipeline -> voice turns share the founder's chat
# session and its state. Only the root agent's model is the live native-audio one.
live_app = App(name="co_founder", root_agent=build_root_agent(Gemini(model=LIVE_MODEL_ID)))


def register_live(app, session_service, founder_id: str) -> None:
    """Mount the bidi voice websocket on the FastAPI app."""
    # One runner owns this surface for the process lifetime. Sessions remain
    # isolated by user/session ids passed to run_live.
    runner = Runner(app=live_app, session_service=session_service)

    @app.websocket("/live/{session_id}")
    async def live_ws(websocket: WebSocket, session_id: str) -> None:
        # HTTP middleware never sees websocket handshakes — gate here. The
        # app_auth cookie from the ?key= bootstrap rides the handshake.
        from app import auth

        if not auth.websocket_is_founder(websocket):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        session = await session_service.get_session(
            app_name=live_app.name, user_id=founder_id, session_id=session_id)
        if session is None:
            session = await session_service.create_session(
                app_name=live_app.name, user_id=founder_id, session_id=session_id)

        queue = LiveRequestQueue()
        run_config = RunConfig(
            response_modalities=["AUDIO"],
            streaming_mode=StreamingMode.BIDI,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )
        turns: list[tuple[str, str]] = []   # finished spoken turns, for the record
        pending_model_raw: list[str] = []
        pending_model_transcript: list[str] = []

        async def send(payload: dict) -> None:
            await websocket.send_text(json.dumps(payload))

        async def upstream() -> None:
            """Browser frames -> LiveRequestQueue."""
            try:
                while True:
                    frame = json.loads(await websocket.receive_text())
                    if "audio" in frame:
                        queue.send_realtime(types.Blob(
                            mime_type="audio/pcm;rate=16000",
                            data=base64.b64decode(frame["audio"])))
                    elif "text" in frame:
                        queue.send_content(types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=frame["text"])]))
                    elif frame.get("close"):
                        queue.close()
                        return
            except WebSocketDisconnect:
                queue.close()
            except Exception as exc:  # errors as data, even on the wire
                logger.warning("live upstream error: %s", exc)
                queue.close()

        async def downstream() -> None:
            """ADK live events -> browser frames."""
            try:
                async for event in runner.run_live(
                        user_id=founder_id, session_id=session_id,
                        live_request_queue=queue, run_config=run_config):
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.inline_data and part.inline_data.mime_type.startswith("audio/"):
                                await send({"audio": base64.b64encode(
                                    part.inline_data.data).decode()})
                            elif part.text:
                                pending_model_raw.append(part.text)
                                await send({"transcript": {"who": "agent",
                                                           "text": part.text, "finished": True}})
                    for attr, who in (("input_transcription", "you"),
                                      ("output_transcription", "agent")):
                        tr = getattr(event, attr, None)
                        if tr is not None and getattr(tr, "text", None):
                            if getattr(tr, "finished", False):
                                if attr == "output_transcription":
                                    pending_model_transcript.append(tr.text)
                                else:
                                    turns.append(("user", tr.text))
                            await send({"transcript": {"who": who, "text": tr.text,
                                                       "finished": bool(getattr(tr, "finished", False))}})
                    if getattr(event, "interrupted", False):
                        pending_model_raw.clear()
                        pending_model_transcript.clear()
                        await send({"interrupted": True})
                    if getattr(event, "turn_complete", False):
                        model_text = " ".join(
                            pending_model_transcript or pending_model_raw).strip()
                        if model_text:
                            turns.append(("model", model_text))
                        pending_model_raw.clear()
                        pending_model_transcript.clear()
                        await send({"turn_complete": True})
            except Exception as exc:
                logger.warning("live downstream error: %s", exc)
                try:
                    await send({"error": f"voice session ended: {exc}"[:300]})
                except Exception:
                    pass
            finally:
                queue.close()

        tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            if task.exception():
                logger.warning("live task failed: %s", task.exception())

        # Live audio is ephemeral — persist the spoken turns so the chat log
        # stays the true record of the conversation. Done here, outside the
        # cancellable tasks, so hangup never drops the transcript.
        if turns:
            try:
                from google.adk.events import Event

                fresh = await session_service.get_session(
                    app_name=live_app.name, user_id=founder_id, session_id=session_id)
                for role, text in turns:
                    if not text.strip():
                        continue
                    await session_service.append_event(fresh, Event(
                        author="user" if role == "user" else "co_founder",
                        invocation_id="voice",
                        content=types.Content(
                            role=role,
                            parts=[types.Part.from_text(text=text.strip())])))
            except Exception as exc:
                logger.warning("voice transcript persist failed: %s", exc)

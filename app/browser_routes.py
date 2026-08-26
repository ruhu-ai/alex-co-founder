"""Browser panel endpoints (docs/07, 18) — thin delegation to browser_service.

Import-light by design: this module never constructs the ADK app or the
session store, so the delegation and identity contract is unit-testable
without a database. app/main.py binds identity resolution via configure()
and mounts the router.
"""

import asyncio
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from services import browser_events, browser_service
from services.browser_events import TooManyBrowserSubscribers
from services.browser_events import hub as browser_event_hub


class BrowserStopRequest(BaseModel):
    session_id: str
    run_id: str | None = None


router = APIRouter()
_session_exists = None
_app_name = "co_founder"
_founder_id = "founder"


def configure(*, app_name: str, founder_id: str, session_exists) -> None:
    """Bind server-side identity resolution. Called once by app/main.py;
    ``session_exists`` is an async callable (session_id) -> bool."""
    global _session_exists, _app_name, _founder_id
    _app_name, _founder_id, _session_exists = app_name, founder_id, session_exists


def _session_key(session_id: str) -> dict[str, str]:
    return {"app_name": _app_name, "user_id": _founder_id, "session_id": session_id}


@router.get("/api/browser/state")
async def api_browser_state(session_id: str):
    """Browser panel snapshot; unknown/non-founder sessions get 404."""
    if _session_exists is None or not await _session_exists(session_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    return await browser_service.get_browser_state(_session_key(session_id))


def _sse(event: dict, *, event_type: str | None = None) -> str:
    kind = event_type or str(event.get("type") or "browser.status")
    run_id = str(event.get("run_id") or "snapshot")
    version = int(event.get("version", 0))
    return (
        f"event: {kind}\n"
        f"id: {run_id}:{version}\n"
        f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
    )


@router.get("/api/browser/events")
async def api_browser_events(session_id: str, request: Request):
    """Snapshot-first authenticated browser projection stream."""
    if _session_exists is None or not await _session_exists(session_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    key_dict = _session_key(session_id)
    key = (key_dict["app_name"], key_dict["user_id"], key_dict["session_id"])
    manager = browser_event_hub.subscribe(key)
    try:
        subscription = await manager.__aenter__()
    except TooManyBrowserSubscribers:
        return JSONResponse({"error": "too many Browser streams"}, status_code=429)

    async def stream():
        started = time.monotonic()
        try:
            # Subscribe happened first, closing the snapshot/publication race.
            snapshot = await browser_service.get_browser_state(key_dict)
            versions = [
                int(view.get("version", 0))
                for view in (snapshot.get("browse"), snapshot.get("fill"))
                if view
            ]
            yield _sse(
                {
                    "type": "browser.snapshot",
                    "run_id": "snapshot",
                    "version": max(versions, default=0),
                    "state": snapshot,
                },
                event_type="browser.snapshot",
            )
            while time.monotonic() - started < 55 * 60:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(subscription.queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event.get("type") == browser_events.SHUTDOWN_SENTINEL:
                    break  # process is shutting down; end the response promptly
                if event.get("type") == "browser.resync":
                    subscription.resync_pending = False
                yield _sse(event)
        finally:
            await manager.__aexit__(None, None, None)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            # No `Connection` header: it is hop-by-hop and illegal to set behind
            # Cloud Run's HTTP/2 front end. Same-origin hardening headers ride
            # along here because the observation stream carries run metadata.
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
        },
    )


@router.post("/api/browser/stop")
async def api_browser_stop(payload: BrowserStopRequest, request: Request):
    """Founder-initiated stop; JSON-only (CSRF hedge), idempotent."""
    if not request.headers.get("content-type", "").lower().startswith(
        "application/json"
    ):
        return JSONResponse({"error": "application/json required"}, status_code=415)
    if _session_exists is None or not await _session_exists(payload.session_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    return await browser_service.stop_browser(
        _session_key(payload.session_id), f"founder:{_founder_id}", payload.run_id
    )

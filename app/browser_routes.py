"""Browser panel endpoints (docs/07, 18) — thin delegation to browser_service.

Import-light by design: this module never constructs the ADK app or the
session store, so the delegation and identity contract is unit-testable
without a database. app/main.py binds identity resolution via configure()
and mounts the router.
"""

import asyncio
import json
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from services import browser_events
from services import browser_gateway as browser_service
from services.browser_events import TooManyBrowserSubscribers
from services.browser_events import hub as browser_event_hub


class BrowserStopRequest(BaseModel):
    session_id: str
    run_id: str | None = None
    client_request_id: str = ""


router = APIRouter()
_session_exists = None
_app_name = "co_founder"
_founder_id = "founder"
_principal_resolver = None
_workspace_session_exists = None


def configure(*, app_name: str, founder_id: str, session_exists,
              principal_resolver=None, workspace_session_exists=None) -> None:
    """Bind server-side identity resolution. Called once by app/main.py;
    ``session_exists`` is an async callable (session_id) -> bool."""
    global _session_exists, _app_name, _founder_id
    global _principal_resolver, _workspace_session_exists
    _app_name, _founder_id, _session_exists = app_name, founder_id, session_exists
    _principal_resolver = principal_resolver
    _workspace_session_exists = workspace_session_exists


def _session_key(session_id: str) -> dict[str, str]:
    return {"app_name": _app_name, "user_id": _founder_id, "session_id": session_id}


def _workspace_key(workspace_id: str, session_id: str) -> dict[str, str]:
    return {"app_name": _app_name, "user_id": workspace_id,
            "session_id": session_id}


async def _v1_principal(request: Request):
    if _principal_resolver is None:
        return JSONResponse({"error": "identity unavailable"}, status_code=503)
    principal = await _principal_resolver(request)
    if isinstance(principal, dict):
        return JSONResponse(principal, status_code=401)
    return principal


@router.get("/api/browser/state")
async def api_browser_state(session_id: str):
    """Browser panel snapshot; unknown/non-founder sessions get 404."""
    if os.environ.get("K_SERVICE"):
        return JSONResponse({"error": True, "error_code": "legacy_route_retired",
                             "message": "Use the v1 browser API."}, status_code=410)
    if _session_exists is None or not await _session_exists(session_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    return await browser_service.get_browser_state(_session_key(session_id))


@router.get("/api/v1/browser/state")
async def api_v1_browser_state(session_id: str, request: Request):
    principal = await _v1_principal(request)
    if isinstance(principal, JSONResponse):
        return principal
    if (_workspace_session_exists is None or not await _workspace_session_exists(
            principal.workspace_id, session_id)):
        return JSONResponse({"error": "not found"}, status_code=404)
    return await browser_service.get_browser_state(
        _workspace_key(principal.workspace_id, session_id))


def _sse(event: dict, *, event_type: str | None = None) -> str:
    kind = event_type or str(event.get("type") or "browser.status")
    run_id = str(event.get("run_id") or "snapshot")
    version = int(event.get("version", 0))
    return (
        f"event: {kind}\n"
        f"id: {run_id}:{version}\n"
        f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
    )


async def _browser_events_for(
        key_dict: dict[str, str], request: Request) -> StreamingResponse | JSONResponse:
    """Snapshot-first stream after the caller has authorized its key."""
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


@router.get("/api/browser/events")
async def api_browser_events(session_id: str, request: Request):
    """Local compatibility stream; production clients use the v1 stream."""
    if os.environ.get("K_SERVICE"):
        return JSONResponse({"error": True, "error_code": "legacy_route_retired",
                             "message": "Use the v1 browser API."}, status_code=410)
    if _session_exists is None or not await _session_exists(session_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    return await _browser_events_for(_session_key(session_id), request)


@router.get("/api/v1/browser/events")
async def api_v1_browser_events(session_id: str, request: Request):
    principal = await _v1_principal(request)
    if isinstance(principal, JSONResponse):
        return principal
    if (_workspace_session_exists is None or not await _workspace_session_exists(
            principal.workspace_id, session_id)):
        return JSONResponse({"error": "not found"}, status_code=404)
    return await _browser_events_for(
        _workspace_key(principal.workspace_id, session_id), request)


@router.post("/api/browser/stop")
async def api_browser_stop(payload: BrowserStopRequest, request: Request):
    """Founder-initiated stop; JSON-only (CSRF hedge), idempotent."""
    if not request.headers.get("content-type", "").lower().startswith(
        "application/json"
    ):
        return JSONResponse({"error": "application/json required"}, status_code=415)
    if os.environ.get("K_SERVICE"):
        return JSONResponse({"error": True, "error_code": "legacy_route_retired",
                             "message": "Use the v1 browser API."}, status_code=410)
    if _session_exists is None or not await _session_exists(payload.session_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    return await browser_service.stop_browser(
        _session_key(payload.session_id), f"founder:{_founder_id}", payload.run_id
    )


@router.post("/api/v1/browser:stop")
async def api_v1_browser_stop(payload: BrowserStopRequest, request: Request):
    """Workspace-bound, receipted cancellation of an owned browser run."""
    from services.command_service import CommandService, transport_status
    from services.durable_store import production_store

    principal = await _v1_principal(request)
    if isinstance(principal, JSONResponse):
        return principal
    if (_workspace_session_exists is None or not await _workspace_session_exists(
            principal.workspace_id, payload.session_id)):
        return JSONResponse({"error": "not found"}, status_code=404)
    commands = CommandService(production_store())
    command = await commands.accept(
        principal=principal, client_request_id=payload.client_request_id,
        command_type="browser.stop",
        request={"session_id": payload.session_id, "run_id": payload.run_id},
        origin_session_id=payload.session_id)
    if command.get("error") or command.get("duplicate"):
        return JSONResponse(command, status_code=transport_status(command))
    result = await browser_service.stop_browser(
        _workspace_key(principal.workspace_id, payload.session_id),
        f"actor:{principal.actor_id}", payload.run_id)
    terminal = await commands.transition(
        workspace_id=principal.workspace_id, command_id=command["command_id"],
        expected_version=command["version"],
        status="COMPLETED" if not result.get("error") else "REJECTED",
        result_ref=({"session_id": payload.session_id,
                     "run_id": str(payload.run_id or result.get("run_id") or "none")}
                    if not result.get("error") else None),
        error_code=str(result.get("error_code") or "browser_stop_failed"))
    return JSONResponse(terminal, status_code=transport_status(terminal))

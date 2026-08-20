"""Browser panel endpoints (docs/07, 18) — thin delegation to browser_service.

Import-light by design: this module never constructs the ADK app or the
session store, so the delegation and identity contract is unit-testable
without a database. app/main.py binds identity resolution via configure()
and mounts the router.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services import browser_service


class BrowserStopRequest(BaseModel):
    session_id: str


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
        _session_key(payload.session_id), f"founder:{_founder_id}"
    )

"""Delegation and identity checks for the two Browser panel endpoints."""

from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

from app import browser_routes

pytestmark = pytest.mark.asyncio

APP_NAME = "co_founder"
FOUNDER_ID = "founder"


@pytest.fixture
def session_exists():
    """Bind a controllable identity resolver; reset to unconfigured after."""
    resolver = AsyncMock(return_value=True)
    browser_routes.configure(
        app_name=APP_NAME, founder_id=FOUNDER_ID, session_exists=resolver
    )
    yield resolver
    browser_routes.configure(
        app_name=APP_NAME, founder_id=FOUNDER_ID, session_exists=None
    )


async def test_browser_state_rejects_unknown_session(session_exists):
    session_exists.return_value = False
    service = AsyncMock()
    original = browser_routes.browser_service.get_browser_state
    browser_routes.browser_service.get_browser_state = service
    try:
        result = await browser_routes.api_browser_state("missing")
        assert isinstance(result, JSONResponse)
        assert result.status_code == 404
        service.assert_not_awaited()
    finally:
        browser_routes.browser_service.get_browser_state = original


async def test_browser_state_delegates_once_with_server_identity(session_exists):
    service = AsyncMock(
        return_value={"status": "success", "browse": None, "fill": None}
    )
    original = browser_routes.browser_service.get_browser_state
    browser_routes.browser_service.get_browser_state = service
    try:
        result = await browser_routes.api_browser_state("s-1")
        assert result["status"] == "success"
        service.assert_awaited_once_with(
            {"app_name": APP_NAME, "user_id": FOUNDER_ID, "session_id": "s-1"}
        )
    finally:
        browser_routes.browser_service.get_browser_state = original


async def test_browser_stop_requires_json_and_delegates_once(session_exists):
    service = AsyncMock(return_value={"status": "success", "already_closed": False})
    original = browser_routes.browser_service.stop_browser
    browser_routes.browser_service.stop_browser = service
    try:
        payload = browser_routes.BrowserStopRequest(session_id="s-2")
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/browser/stop",
                "headers": [(b"content-type", b"application/json")],
            }
        )
        result = await browser_routes.api_browser_stop(payload, request)
        assert result["status"] == "success"
        service.assert_awaited_once_with(
            {"app_name": APP_NAME, "user_id": FOUNDER_ID, "session_id": "s-2"},
            f"founder:{FOUNDER_ID}",
        )

        bad_request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/browser/stop",
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        refused = await browser_routes.api_browser_stop(payload, bad_request)
        assert refused.status_code == 415
        assert service.await_count == 1
    finally:
        browser_routes.browser_service.stop_browser = original

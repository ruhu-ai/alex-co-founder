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
            None,
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


async def test_browser_events_subscribes_before_snapshot_and_cleans_up(
    session_exists, monkeypatch
):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/browser/events",
            "query_string": b"session_id=s-3",
            "headers": [],
        },
        receive=receive,
    )
    key = (APP_NAME, FOUNDER_ID, "s-3")
    observed_counts = []

    async def snapshot(_session_key):
        observed_counts.append(browser_routes.browser_event_hub.subscriber_count(key))
        return {
            "status": "success",
            "browse": {"run_id": "r1", "status": "active", "version": 3},
            "fill": None,
        }

    monkeypatch.setattr(browser_routes.browser_service, "get_browser_state", snapshot)
    response = await browser_routes.api_browser_events("s-3", request)
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-store"
    iterator = response.body_iterator
    first = await anext(iterator)
    assert "event: browser.snapshot" in first
    assert observed_counts == [1]
    await browser_routes.browser_event_hub.publish(
        key, {"type": "browser.closed", "run_id": "r1", "version": 4}
    )
    second = await anext(iterator)
    assert "event: browser.closed" in second
    assert "id: r1:4" in second
    await iterator.aclose()
    assert browser_routes.browser_event_hub.subscriber_count(key) == 0


async def test_browser_stop_forwards_explicit_fill_run(session_exists):
    service = AsyncMock(return_value={"status": "success", "kind": "fill"})
    original = browser_routes.browser_service.stop_browser
    browser_routes.browser_service.stop_browser = service
    try:
        payload = browser_routes.BrowserStopRequest(session_id="s-4", run_id="fill-1")
        request = Request(
            {
                "type": "http", "method": "POST", "path": "/api/browser/stop",
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await browser_routes.api_browser_stop(payload, request)
        service.assert_awaited_once_with(
            {"app_name": APP_NAME, "user_id": FOUNDER_ID, "session_id": "s-4"},
            f"founder:{FOUNDER_ID}",
            "fill-1",
        )
    finally:
        browser_routes.browser_service.stop_browser = original

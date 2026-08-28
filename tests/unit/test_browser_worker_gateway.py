from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import browser_worker_routes
from services import browser_gateway


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(browser_worker_routes.router)
    return app


def test_browser_worker_rpc_is_private_and_closed(monkeypatch):
    client = TestClient(_app())
    denied = client.post(
        "/internal/browser/v1/execute",
        json={"operation": "get_browser_state", "params": {
            "args": [{"app_name": "co_founder", "user_id": "workspace_a",
                      "session_id": "session_a"}], "kwargs": {}}})
    assert denied.status_code == 401

    monkeypatch.setenv("BROWSER_WORKER_ROLE", "1")
    monkeypatch.setenv("BROWSER_WORKER_ALLOW_LOCAL", "1")
    unknown = client.post(
        "/internal/browser/v1/execute",
        json={"operation": "arbitrary_python", "params": {}})
    assert unknown.status_code == 400
    assert unknown.json()["error_code"] == "browser_operation_unregistered"


def test_worker_returns_only_opaque_page_handle(monkeypatch):
    class Page:
        url = "https://portal.example/application"

    async def open_and_login(*_args, **_kwargs):
        return {"status": "success", "run_id": "run_a",
                "page": Page(), "context": object()}

    monkeypatch.setenv("BROWSER_WORKER_ROLE", "1")
    monkeypatch.setenv("BROWSER_WORKER_ALLOW_LOCAL", "1")
    monkeypatch.setattr(
        browser_worker_routes.browser_service, "open_and_login", open_and_login)
    response = TestClient(_app()).post(
        "/internal/browser/v1/execute",
        json={"operation": "open_and_login", "params": {
            "args": ["https://portal.example", "user", "secret"],
            "kwargs": {"session_key": {
                "app_name": "co_founder", "user_id": "workspace_a",
                "session_id": "session_a"}, "application_id": "app_a"}}})
    assert response.status_code == 200
    result = response.json()
    assert "context" not in result
    assert result["page"] == {
        "__remote_page__": True, "application_id": "app_a",
        "run_id": "run_a", "url": "https://portal.example/application"}


@pytest.mark.asyncio
async def test_api_instance_lifecycle_never_shuts_down_remote_worker(monkeypatch):
    monkeypatch.setenv("BROWSER_WORKER_URL", "https://browser.example")
    monkeypatch.delenv("BROWSER_WORKER_ROLE", raising=False)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("API lifecycle was forwarded to browser worker")

    monkeypatch.setattr(browser_gateway, "_call", forbidden)
    assert await browser_gateway.reconcile_all_runs() is None
    assert await browser_gateway.shutdown() is None

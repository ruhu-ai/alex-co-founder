from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth


def _app() -> FastAPI:
    app = FastAPI()
    auth.install(app)

    @app.post("/api/mutate")
    async def mutate():
        return {"status": "success"}

    return app


def test_session_cookie_mutation_requires_platform_csrf(monkeypatch):
    monkeypatch.setenv("APP_SESSION_SECRET", "csrf-test-secret")
    monkeypatch.delenv("APP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    client = TestClient(_app())
    client.cookies.set(auth.SESSION_COOKIE, auth.mint_session(
        "owner@example.test", subject="subject_a", auth_time=int(time.time())))

    refused = client.post("/api/mutate")
    token = client.get("/auth/me").json()["csrf_token"]
    allowed = client.post("/api/mutate", headers={"X-CSRF-Token": token})

    assert refused.status_code == 403
    assert refused.json()["error_code"] == "csrf_failed"
    assert allowed.status_code == 200


def test_explicit_api_key_does_not_require_browser_csrf(monkeypatch):
    monkeypatch.setenv("APP_AUTH_TOKEN", "api-key-test")
    monkeypatch.setenv("APP_SESSION_SECRET", "csrf-test-secret")
    monkeypatch.delenv("K_SERVICE", raising=False)
    client = TestClient(_app())

    response = client.post(
        "/api/mutate", headers={"X-App-Key": "api-key-test"})

    assert response.status_code == 200

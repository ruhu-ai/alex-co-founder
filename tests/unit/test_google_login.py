"""Current-tab Google founder login security and session contracts."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth, google_login

ENV_VARS = (
    "AGENT_BASE_URL",
    "ALLOWED_LOGIN_EMAILS",
    "APP_AUTH_TOKEN",
    "APP_SESSION_SECRET",
    "GOOGLE_LOGIN_REDIRECT_URI",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "K_SERVICE",
)


class FakeFlow:
    def __init__(self):
        self.authorization_params = {}
        self.fetch_codes = []
        self.credentials = SimpleNamespace(id_token="encoded-google-id-token")

    def authorization_url(self, **kwargs):
        self.authorization_params = kwargs
        return "https://accounts.google.com/o/oauth2/auth?test=1", kwargs["state"]

    def fetch_token(self, *, code):
        self.fetch_codes.append(code)


@pytest.fixture
def env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_BASE_URL", "http://127.0.0.1:8098")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "web-client.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("APP_SESSION_SECRET", "session-secret")
    monkeypatch.setenv("ALLOWED_LOGIN_EMAILS", "founder@ruhu.ai")
    return monkeypatch


@pytest.fixture
def client(env):
    app = FastAPI()
    auth.install(app)
    return TestClient(app)


def _successful_claims():
    return {
        "sub": "google-subject",
        "email": "Founder@ruhu.ai",
        "email_verified": True,
        "name": "Founder",
        "auth_time": 1_700_000_000,
    }


def test_start_is_current_tab_redirect_with_signed_short_lived_state(
        client, monkeypatch):
    flow = FakeFlow()
    monkeypatch.setattr(google_login, "_flow", lambda *_args, **_kwargs: flow)

    response = client.get("/auth/google/start", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("https://accounts.google.com/")
    assert response.cookies.get(google_login.STATE_COOKIE)
    assert flow.authorization_params["access_type"] == "online"
    assert flow.authorization_params["include_granted_scopes"] == "false"
    assert flow.authorization_params["prompt"] == "select_account"
    assert flow.authorization_params["nonce"]
    assert flow.authorization_params["state"]


def test_callback_verifies_identity_and_mints_app_session(
        client, monkeypatch):
    flow = FakeFlow()
    monkeypatch.setattr(google_login, "_flow", lambda *_args, **_kwargs: flow)

    async def verify(_token, *, nonce):
        assert nonce == flow.authorization_params["nonce"]
        return _successful_claims()

    monkeypatch.setattr(google_login, "_verify_id_token", verify)
    start = client.get("/auth/google/start", follow_redirects=False)
    assert start.status_code == 303

    response = client.get(
        "/auth/google/callback",
        params={"code": "one-time-code",
                "state": flow.authorization_params["state"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert flow.fetch_codes == ["one-time-code"]
    me = client.get("/auth/me").json()
    assert me["authenticated"] is True
    assert me["mode"] == "session"
    assert me["email"] == "founder@ruhu.ai"


def test_callback_rejects_missing_or_mismatched_state(client, monkeypatch):
    flow = FakeFlow()
    monkeypatch.setattr(google_login, "_flow", lambda *_args, **_kwargs: flow)
    client.get("/auth/google/start", follow_redirects=False)

    response = client.get(
        "/auth/google/callback?code=x&state=attacker",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("google_error=invalid_state")
    assert flow.fetch_codes == []


def test_callback_returns_only_to_signed_same_origin_path(client, monkeypatch):
    flow = FakeFlow()
    monkeypatch.setattr(google_login, "_flow", lambda *_args, **_kwargs: flow)

    async def verify(_token, *, nonce):
        assert nonce
        return _successful_claims()

    monkeypatch.setattr(google_login, "_verify_id_token", verify)
    start = client.get(
        "/auth/google/start?next=/api/integrations/google/connect%3Fconnector%3Dalex_calendar",
        follow_redirects=False,
    )
    response = client.get(
        "/auth/google/callback",
        params={"code": "one-time-code", "state": flow.authorization_params["state"]},
        follow_redirects=False,
    )
    assert start.status_code == 303
    assert response.headers["location"] == "/api/integrations/google/connect?connector=alex_calendar"


def test_callback_enforces_verified_email_allowlist(client, monkeypatch):
    flow = FakeFlow()
    monkeypatch.setattr(google_login, "_flow", lambda *_args, **_kwargs: flow)

    async def verify(_token, *, nonce):
        assert nonce
        return {**_successful_claims(), "email": "intruder@example.com"}

    monkeypatch.setattr(google_login, "_verify_id_token", verify)
    client.get("/auth/google/start", follow_redirects=False)
    response = client.get(
        "/auth/google/callback",
        params={"code": "x", "state": flow.authorization_params["state"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("google_error=not_authorized")
    assert client.get("/auth/me").json()["mode"] == "open"


def test_state_cookie_tamper_and_expiry_fail_closed(env, monkeypatch):
    now = 1_700_000_000
    monkeypatch.setattr(google_login.time, "time", lambda: now)
    value = google_login._seal_state({
        "iat": now,
        "state": "state",
        "nonce": "nonce",
        "verifier": "verifier",
        "redirect_uri": "http://127.0.0.1:8098/auth/google/callback",
    })
    assert google_login._open_state(value)["state"] == "state"
    assert google_login._open_state(value + "tampered") is None
    monkeypatch.setattr(
        google_login.time, "time",
        lambda: now + google_login.STATE_TTL_SECONDS + 1)
    assert google_login._open_state(value) is None

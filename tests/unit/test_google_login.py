"""Current-tab Google founder login security and session contracts."""

import json
import logging
import time
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
        "iat": int(time.time()),
    }


@pytest.mark.parametrize("target", [
    "/auth/google/callback?state=s&code=one-time-secret",
    "/api/integrations/google/callback?code=one-time-secret&state=s",
])
def test_oauth_callback_credentials_are_redacted_from_access_logs(target):
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", target, "1.1", 303),
        None,
    )

    assert google_login._OAuthCallbackAccessLogFilter().filter(record)
    assert record.args[2].endswith("?[redacted]")
    assert "one-time-secret" not in record.getMessage()
    assert "state=s" not in record.getMessage()


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
    assert json.loads(flow.authorization_params["claims"]) == {
        "id_token": {"auth_time": {"essential": True}},
    }
    assert flow.authorization_params["nonce"]
    assert flow.authorization_params["state"]


def test_fresh_start_forces_login_and_seals_exact_return_path(
        client, monkeypatch):
    flow = FakeFlow()
    monkeypatch.setattr(google_login, "_flow", lambda *_args, **_kwargs: flow)

    response = client.get(
        "/auth/google/start",
        params={"fresh": "1", "next": "/hiring.html?role=role_a"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert flow.authorization_params["prompt"] == "login"
    assert flow.authorization_params["max_age"] == "0"
    assert flow.authorization_params["include_granted_scopes"] == "false"
    pending = google_login._open_state(
        response.cookies.get(google_login.STATE_COOKIE))
    assert pending["fresh"] is True
    assert pending["return_path"] == "/hiring.html?role=role_a"


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


def test_callback_uses_fresh_verified_token_issue_time_when_auth_time_is_optional(
        client, monkeypatch):
    flow = FakeFlow()
    expected_issued_at = int(time.time())
    monkeypatch.setattr(google_login, "_flow", lambda *_args, **_kwargs: flow)

    async def verify(_token, *, nonce):
        assert nonce
        claims = _successful_claims()
        claims.pop("auth_time")
        claims["iat"] = expected_issued_at
        return claims

    monkeypatch.setattr(google_login, "_verify_id_token", verify)
    client.get("/auth/google/start", follow_redirects=False)
    response = client.get(
        "/auth/google/callback",
        params={"code": "x", "state": flow.authorization_params["state"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    session = auth.read_session(client.cookies.get(auth.SESSION_COOKIE))
    assert session["auth_time"] == expected_issued_at
    assert session["auth_time_source"] == "oidc_token_iat"


def test_fresh_callback_rejects_stale_idp_auth_and_preserves_step_up_retry(
        client, monkeypatch):
    now = 1_700_000_000
    flow = FakeFlow()
    monkeypatch.setattr(google_login.time, "time", lambda: now)
    monkeypatch.setattr(google_login, "_flow", lambda *_args, **_kwargs: flow)

    async def verify(_token, *, nonce):
        assert nonce
        return {
            **_successful_claims(),
            "auth_time": now
            - google_login.ID_TOKEN_ISSUED_AT_MAX_AGE_SECONDS - 1,
            "iat": now,
        }

    monkeypatch.setattr(google_login, "_verify_id_token", verify)
    client.get(
        "/auth/google/start",
        params={"fresh": "1", "next": "/hiring.html?role=role_a"},
        follow_redirects=False,
    )
    response = client.get(
        "/auth/google/callback",
        params={"code": "x", "state": flow.authorization_params["state"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/login.html?google_error=authentication_time_missing"
        "&fresh=1&next=%2Fhiring.html%3Frole%3Drole_a"
    )
    assert client.get("/auth/me").json()["mode"] == "open"


def test_fresh_callback_accepts_recent_iat_when_auth_time_is_absent(
        client, monkeypatch):
    now = 1_700_000_000
    flow = FakeFlow()
    monkeypatch.setattr(google_login.time, "time", lambda: now)
    monkeypatch.setattr(google_login, "_flow", lambda *_args, **_kwargs: flow)

    async def verify(_token, *, nonce):
        assert nonce
        claims = _successful_claims()
        claims.pop("auth_time")
        claims["iat"] = now
        return claims

    monkeypatch.setattr(google_login, "_verify_id_token", verify)
    client.get(
        "/auth/google/start",
        params={"fresh": "1", "next": "/hiring.html?role=role_a"},
        follow_redirects=False,
    )
    response = client.get(
        "/auth/google/callback",
        params={"code": "x", "state": flow.authorization_params["state"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/hiring.html?role=role_a"
    session = auth.read_session(client.cookies.get(auth.SESSION_COOKIE))
    assert session["auth_time"] == now
    assert session["auth_time_source"] == "oidc_token_iat"


@pytest.mark.parametrize("issued_at", [None, 1, 10**12])
def test_callback_rejects_missing_or_unfresh_session_timestamp(
        client, monkeypatch, issued_at):
    flow = FakeFlow()
    monkeypatch.setattr(google_login, "_flow", lambda *_args, **_kwargs: flow)

    async def verify(_token, *, nonce):
        assert nonce
        claims = _successful_claims()
        claims.pop("auth_time")
        if issued_at is None:
            claims.pop("iat")
        else:
            claims["iat"] = issued_at
        return claims

    monkeypatch.setattr(google_login, "_verify_id_token", verify)
    client.get("/auth/google/start", follow_redirects=False)
    response = client.get(
        "/auth/google/callback",
        params={"code": "x", "state": flow.authorization_params["state"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith(
        "google_error=authentication_time_missing")
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

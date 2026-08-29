"""Auth gate contract tests (docs/12 §Founder sign-in).

No network: Firebase verification is monkeypatched. Under test is the POLICY —
signed-cookie integrity, the email_verified + allowlist gate, exemptions, the
?key= bootstrap, and the fail-closed production posture.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth

ENV_VARS = ("APP_AUTH_TOKEN", "APP_SESSION_SECRET", "K_SERVICE",
            "FIREBASE_WEB_API_KEY", "FIREBASE_PROJECT_ID",
            "FIREBASE_AUTH_DOMAIN", "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_LOGIN_REDIRECT_URI",
            "ALLOWED_LOGIN_EMAILS", "WORKSPACE_ID")


@pytest.fixture
def env(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def client(env):
    app = FastAPI()
    auth.install(app)

    @app.get("/private")
    async def private():
        return {"ok": True}

    return TestClient(app)


# ---------------------------------------------------------------------------
# gate + token path
# ---------------------------------------------------------------------------

def test_local_dev_is_open(client):
    assert client.get("/private").status_code == 200


def test_token_gate(env, client):
    env.setenv("APP_AUTH_TOKEN", "t-secret")
    assert client.get("/private").status_code == 401
    assert client.get("/private", headers={"X-App-Key": "wrong"}).status_code == 401
    assert client.get("/private", headers={"X-App-Key": "t-secret"}).status_code == 200
    assert client.get(
        "/private", headers={"Authorization": "Bearer t-secret"}).status_code == 200


def test_unauthorized_browser_nav_redirects_to_login(env, client):
    env.setenv("APP_AUTH_TOKEN", "t-secret")
    r = client.get("/private", headers={"accept": "text/html"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login.html?next=%2Fprivate"


def test_post_login_return_path_rejects_open_redirects():
    assert auth.safe_local_return_path("/api/integrations/google/connect?connector=alex_calendar") \
        == "/api/integrations/google/connect?connector=alex_calendar"
    assert auth.safe_local_return_path("https://attacker.example") == "/"
    assert auth.safe_local_return_path("//attacker.example") == "/"
    assert auth.safe_local_return_path("/\\attacker.example") == "/"


def test_key_bootstrap_strips_query_and_sets_cookie(env, client):
    env.setenv("APP_AUTH_TOKEN", "t-secret")
    r = client.get("/private?key=t-secret&x=1", headers={"accept": "text/html"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/private?x=1"
    assert r.cookies.get(auth.COOKIE_NAME) == "t-secret"


def test_production_fails_closed_without_any_auth(env, client):
    env.setenv("K_SERVICE", "co-founder")
    assert client.get("/private").status_code == 503


# ---------------------------------------------------------------------------
# signed session cookie
# ---------------------------------------------------------------------------

def test_session_roundtrip(env):
    env.setenv("APP_SESSION_SECRET", "s1")
    value = auth.mint_session("founder@ruhu.ai", "Founder")
    claims = auth.read_session(value)
    assert claims["email"] == "founder@ruhu.ai"
    assert claims["name"] == "Founder"


def test_session_tamper_rejected(env):
    env.setenv("APP_SESSION_SECRET", "s1")
    value = auth.mint_session("founder@ruhu.ai")
    payload, sig = value.rsplit(".", 1)
    flipped = ("0" if sig[0] != "0" else "1") + sig[1:]
    assert auth.read_session(f"{payload}.{flipped}") is None
    assert auth.read_session(payload) is None
    assert auth.read_session("") is None


def test_session_expiry_rejected(env, monkeypatch):
    env.setenv("APP_SESSION_SECRET", "s1")
    monkeypatch.setattr(auth, "SESSION_TTL_SECONDS", -10)
    assert auth.read_session(auth.mint_session("founder@ruhu.ai")) is None


def test_session_signed_with_other_secret_rejected(env):
    env.setenv("APP_SESSION_SECRET", "s1")
    value = auth.mint_session("founder@ruhu.ai")
    env.setenv("APP_SESSION_SECRET", "s2")
    assert auth.read_session(value) is None


def test_session_cookie_passes_the_gate(env, client):
    env.setenv("APP_AUTH_TOKEN", "t-secret")   # gate closed to anonymous
    env.setenv("APP_SESSION_SECRET", "s1")
    client.cookies.set(auth.SESSION_COOKIE, auth.mint_session("founder@ruhu.ai"))
    assert client.get("/private").status_code == 200


# ---------------------------------------------------------------------------
# allowlist policy
# ---------------------------------------------------------------------------

def test_allowlist_matching_is_case_insensitive(env):
    env.setenv("ALLOWED_LOGIN_EMAILS", " Founder@Ruhu.ai , second@x.io ")
    assert auth.email_may_log_in("founder@ruhu.ai")
    assert auth.email_may_log_in("SECOND@X.IO")
    assert not auth.email_may_log_in("intruder@x.io")


def test_empty_allowlist_fails_closed_in_production(env):
    assert auth.email_may_log_in("anyone@x.io")          # local dev: allowed
    env.setenv("K_SERVICE", "co-founder")
    assert not auth.email_may_log_in("anyone@x.io")      # prod: rejected


# ---------------------------------------------------------------------------
# /auth/* routes
# ---------------------------------------------------------------------------

def _enable_firebase(env):
    env.setenv("FIREBASE_WEB_API_KEY", "AIza-public")
    env.setenv("FIREBASE_PROJECT_ID", "proj-1")
    env.setenv("APP_SESSION_SECRET", "s1")


def _fake_verifier(claims):
    async def _verify(id_token):
        if isinstance(claims, Exception):
            raise claims
        return claims
    return _verify


def test_auth_config_is_reachable_without_credentials(env, client):
    env.setenv("APP_AUTH_TOKEN", "t-secret")
    r = client.get("/auth/config")
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_login_page_is_never_cached(client):
    response = client.get("/login.html")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_auth_config_reports_email_and_google_independently(env, client):
    env.setenv("GOOGLE_OAUTH_CLIENT_ID", "web-client")
    env.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    google_only = client.get("/auth/config").json()
    assert google_only["enabled"] is True
    assert google_only["googleEnabled"] is True
    assert google_only["firebaseEnabled"] is False

    env.setenv("FIREBASE_WEB_API_KEY", "AIza-public")
    env.setenv("FIREBASE_PROJECT_ID", "proj-1")
    both = client.get("/auth/config").json()
    assert both["firebaseEnabled"] is True


def test_auth_session_disabled_without_firebase(client):
    r = client.post("/auth/session", json={"id_token": "x"})
    assert r.status_code == 503


def test_auth_session_rejects_bad_token(env, client, monkeypatch):
    _enable_firebase(env)
    monkeypatch.setattr(auth, "_verify_firebase_id_token",
                        _fake_verifier(ValueError("bad")))
    assert client.post("/auth/session", json={"id_token": "x"}).status_code == 401


def test_auth_session_requires_verified_email(env, client, monkeypatch):
    _enable_firebase(env)
    monkeypatch.setattr(auth, "_verify_firebase_id_token", _fake_verifier(
        {"email": "founder@ruhu.ai", "email_verified": False}))
    assert client.post("/auth/session", json={"id_token": "x"}).status_code == 403


def test_auth_session_enforces_allowlist(env, client, monkeypatch):
    _enable_firebase(env)
    env.setenv("ALLOWED_LOGIN_EMAILS", "founder@ruhu.ai")
    monkeypatch.setattr(auth, "_verify_firebase_id_token", _fake_verifier(
        {"email": "intruder@x.io", "email_verified": True}))
    assert client.post("/auth/session", json={"id_token": "x"}).status_code == 403


def test_auth_session_grants_and_me_reports_it(env, client, monkeypatch):
    _enable_firebase(env)
    env.setenv("APP_AUTH_TOKEN", "t-secret")   # gate closed to anonymous
    env.setenv("ALLOWED_LOGIN_EMAILS", "founder@ruhu.ai")
    monkeypatch.setattr(auth, "_verify_firebase_id_token", _fake_verifier(
        {"email": "Founder@ruhu.ai", "email_verified": True, "name": "Ijidai"}))
    r = client.post("/auth/session", json={"id_token": "x"})
    assert r.status_code == 200
    # The cookie the login minted now opens the gate and identifies the founder.
    assert client.get("/private").status_code == 200
    me = client.get("/auth/me").json()
    assert me["authenticated"] is True
    assert me["mode"] == "session"
    assert me["email"] == "founder@ruhu.ai"
    assert me["name"] == "Ijidai"


def test_main_ui_starts_founder_workspace_without_legacy_access_pass():
    source = open("app/static/index.html", encoding="utf-8").read()
    assert "await startAuthorizedApp()" in source
    assert "checkWorkspaceAccess" not in source
    assert "accessGate" not in source
    assert "/auth/workspace/bootstrap" not in source


def test_login_restores_founder_directly_and_shell_redirect_is_single_shot():
    login = open("app/static/login.html", encoding="utf-8").read()
    shell = open("app/static/index.html", encoding="utf-8").read()
    assert 'fetch("/auth/access"' not in login
    assert 'if (me?.authenticated)' in login
    assert "let signInRedirectStarted = false;" in shell
    assert 'location.replace("/login.html")' in shell
    assert 'location.href = "/login.html"' not in shell


def test_auth_me_modes(env, client):
    assert client.get("/auth/me").json()["mode"] == "open"
    env.setenv("APP_AUTH_TOKEN", "t-secret")
    anonymous = client.get("/auth/me")
    assert anonymous.status_code == 200
    assert anonymous.json()["authenticated"] is False
    assert anonymous.json()["mode"] == "anonymous"
    r = client.get("/auth/me", headers={"X-App-Key": "t-secret"})
    assert r.json()["authenticated"] is True
    assert r.json()["mode"] == "token"


def test_logout_clears_the_session(env, client, monkeypatch):
    _enable_firebase(env)
    env.setenv("APP_AUTH_TOKEN", "t-secret")
    monkeypatch.setattr(auth, "_verify_firebase_id_token", _fake_verifier(
        {"email": "founder@ruhu.ai", "email_verified": True}))
    assert client.post("/auth/session", json={"id_token": "x"}).status_code == 200
    assert client.get("/private").status_code == 200
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/private").status_code == 401

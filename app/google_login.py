"""Current-tab Google founder login without cross-origin browser storage.

Firebase's ``signInWithRedirect`` relies on storage owned by the configured
``*.firebaseapp.com`` helper.  Co-Founder is served by Cloud Run (and by a
loopback origin in development), so embedded and privacy-preserving browsers
can return from Google without a credential.  This module instead uses
Google's web-server authorization-code flow:

* the browser only performs current-tab redirects;
* state, PKCE verifier and OIDC nonce are held in a short-lived, signed,
  HttpOnly cookie;
* the server exchanges the one-time code and verifies the ID token;
* no Google access or refresh token is persisted;
* the existing verified-email allowlist and signed app session remain the
  authorization boundary.

The redirect URI must be registered on ``GOOGLE_OAUTH_CLIENT_ID``.  It defaults
to ``<AGENT_BASE_URL>/auth/google/callback`` and may be overridden explicitly
with ``GOOGLE_LOGIN_REDIRECT_URI`` for local development.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse

from app import auth

STATE_COOKIE = "google_login_state"
STATE_TTL_SECONDS = 10 * 60
LOGIN_SCOPES = ["openid", "email", "profile"]


def configured() -> bool:
    """Whether the confidential web-server OAuth client is configured."""
    return bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
                and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"))


def _state_secret() -> str:
    secret = (os.environ.get("APP_SESSION_SECRET")
              or os.environ.get("APP_AUTH_TOKEN"))
    if not secret and not os.environ.get("K_SERVICE"):
        return "dev-session-secret"
    return secret or ""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _seal_state(payload: dict) -> str:
    secret = _state_secret()
    if not secret:
        raise RuntimeError("session signing secret is not configured")
    encoded = _b64url(json.dumps(
        payload, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _open_state(value: str) -> dict | None:
    secret = _state_secret()
    if not secret or not value or "." not in value:
        return None
    encoded, signature = value.rsplit(".", 1)
    expected = hmac.new(
        secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_unb64url(encoded))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    issued_at = payload.get("iat")
    if not isinstance(issued_at, int):
        return None
    age = int(time.time()) - issued_at
    if age < 0 or age > STATE_TTL_SECONDS:
        return None
    return payload


def _redirect_uri(request: Request) -> str:
    explicit = os.environ.get("GOOGLE_LOGIN_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    base = os.environ.get("AGENT_BASE_URL", "").strip().rstrip("/")
    if not base and request.url.hostname in {"127.0.0.1", "localhost", "::1"}:
        base = str(request.base_url).rstrip("/")
    if not base:
        raise ValueError("Google login redirect URI is not configured")
    return f"{base}/auth/google/callback"


def _flow(redirect_uri: str, *, verifier: str):
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_config(
        {"web": {
            "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }},
        scopes=LOGIN_SCOPES,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
    )


async def _verify_id_token(encoded: str, *, nonce: str) -> dict:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    audience = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    claims = await asyncio.to_thread(
        id_token.verify_oauth2_token,
        encoded,
        google_requests.Request(),
        audience,
    )
    if not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise ValueError("OIDC nonce mismatch")
    return claims


def _cookie_kwargs(*, secure: bool, max_age: int) -> dict:
    return {
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "max_age": max_age,
        "path": "/",
    }


def _error_response(code: str, *, secure: bool) -> RedirectResponse:
    response = RedirectResponse(
        f"/login.html?{urlencode({'google_error': code})}", status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/", secure=secure, samesite="lax")
    return response


def register(app) -> None:
    """Register the current-tab Google login start and callback routes."""

    @app.get("/auth/google/start")
    async def google_login_start(request: Request):
        secure = request.url.scheme == "https" or bool(os.environ.get("K_SERVICE"))
        if not configured():
            return _error_response("not_configured", secure=secure)
        try:
            redirect_uri = _redirect_uri(request)
            state = secrets.token_urlsafe(32)
            nonce = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            flow = _flow(redirect_uri, verifier=verifier)
            url, _ = flow.authorization_url(
                state=state,
                nonce=nonce,
                access_type="online",
                include_granted_scopes="false",
                prompt="select_account",
            )
            sealed = _seal_state({
                "iat": int(time.time()),
                "state": state,
                "nonce": nonce,
                "verifier": verifier,
                "redirect_uri": redirect_uri,
            })
        except Exception:
            return _error_response("start_failed", secure=secure)
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(
            STATE_COOKIE, sealed,
            **_cookie_kwargs(secure=secure, max_age=STATE_TTL_SECONDS))
        return response

    @app.get("/auth/google/callback")
    async def google_login_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
    ):
        secure = request.url.scheme == "https" or bool(os.environ.get("K_SERVICE"))
        pending = _open_state(request.cookies.get(STATE_COOKIE, ""))
        if not pending or not state or not hmac.compare_digest(
                str(pending.get("state") or ""), state):
            return _error_response("invalid_state", secure=secure)
        if error or not code:
            return _error_response("cancelled", secure=secure)
        try:
            flow = _flow(
                str(pending["redirect_uri"]),
                verifier=str(pending["verifier"]),
            )
            await asyncio.to_thread(flow.fetch_token, code=code)
            encoded_id_token = str(flow.credentials.id_token or "")
            if not encoded_id_token:
                raise ValueError("Google did not return an ID token")
            claims = await _verify_id_token(
                encoded_id_token, nonce=str(pending["nonce"]))
        except Exception:
            return _error_response("exchange_failed", secure=secure)

        email = str(claims.get("email") or "").lower()
        if not email or claims.get("email_verified") is not True:
            return _error_response("unverified_email", secure=secure)
        if not auth.email_may_log_in(email):
            return _error_response("not_authorized", secure=secure)

        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            auth.SESSION_COOKIE,
            auth.mint_session(
                email,
                str(claims.get("name") or ""),
                subject=str(claims.get("sub") or ""),
                auth_time=(int(claims["auth_time"])
                           if isinstance(claims.get("auth_time"), (int, float))
                           else None),
            ),
            **_cookie_kwargs(secure=secure, max_age=auth.SESSION_TTL_SECONDS),
        )
        response.delete_cookie(STATE_COOKIE, path="/", secure=secure, samesite="lax")
        return response

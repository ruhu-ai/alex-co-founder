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
import logging
import os
import secrets
import time
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse

from app import auth

STATE_COOKIE = "google_login_state"
STATE_TTL_SECONDS = 10 * 60
ID_TOKEN_ISSUED_AT_MAX_AGE_SECONDS = 5 * 60
ID_TOKEN_CLOCK_SKEW_SECONDS = 60
LOGIN_SCOPES = ["openid", "email", "profile"]
_OAUTH_CALLBACK_PATHS = (
    "/auth/google/callback",
    "/api/integrations/google/callback",
)


class _OAuthCallbackAccessLogFilter(logging.Filter):
    """Remove OAuth codes and state from Uvicorn's raw request-target log.

    Uvicorn stores the request target as the third positional formatting
    argument. OAuth callbacks must remain observable, but their single-use
    credentials must never be copied into terminal or collected access logs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.args, tuple) or len(record.args) < 3:
            return True
        target = record.args[2]
        if not isinstance(target, str):
            return True
        if any(target.startswith(f"{path}?") for path in _OAUTH_CALLBACK_PATHS):
            args = list(record.args)
            args[2] = target.split("?", 1)[0] + "?[redacted]"
            record.args = tuple(args)
        return True


def _install_access_log_redaction() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _OAuthCallbackAccessLogFilter)
               for item in logger.filters):
        logger.addFilter(_OAuthCallbackAccessLogFilter())


_install_access_log_redaction()


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


def _error_response(
    code: str,
    *,
    secure: bool,
    fresh: bool = False,
    return_path: str = "/",
) -> RedirectResponse:
    query = {"google_error": code}
    if fresh:
        query.update({
            "fresh": "1",
            "next": auth.safe_local_return_path(return_path),
        })
    response = RedirectResponse(
        f"/login.html?{urlencode(query)}", status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/", secure=secure, samesite="lax")
    return response


def _session_auth_time(claims: dict) -> tuple[int, str] | None:
    """Resolve a recent, verified timestamp for the local application session.

    Google returns ``auth_time`` only for OAuth clients with that optional
    claim enabled. The ID token's signed ``iat`` is required by Google and is
    issued during this one-time, nonce-bound authorization-code exchange. It
    is therefore a safe session-establishment timestamp when the optional
    upstream authentication timestamp is absent, provided it is fresh.
    """

    auth_time = claims.get("auth_time")
    if isinstance(auth_time, (int, float)):
        return int(auth_time), "idp_auth_time"
    issued_at = claims.get("iat")
    if not isinstance(issued_at, (int, float)):
        return None
    now = int(time.time())
    issued_at = int(issued_at)
    if (issued_at > now + ID_TOKEN_CLOCK_SKEW_SECONDS
            or issued_at < now - ID_TOKEN_ISSUED_AT_MAX_AGE_SECONDS):
        return None
    return issued_at, "oidc_token_iat"


def register(app) -> None:
    """Register the current-tab Google login start and callback routes."""

    @app.get("/auth/google/start")
    async def google_login_start(request: Request):
        secure = request.url.scheme == "https" or bool(os.environ.get("K_SERVICE"))
        fresh = request.query_params.get("fresh") == "1"
        if not configured():
            return _error_response("not_configured", secure=secure)
        try:
            redirect_uri = _redirect_uri(request)
            state = secrets.token_urlsafe(32)
            nonce = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            flow = _flow(redirect_uri, verifier=verifier)
            authorization_options = {
                "state": state,
                "nonce": nonce,
                "access_type": "online",
                "include_granted_scopes": "false",
                "prompt": "login" if fresh else "select_account",
                # Google only includes auth_time when it is explicitly
                # requested. Workspace principals and recent-auth checks use
                # that verified value; minting a session without it would
                # authenticate the browser but fail every authority lookup.
                "claims": json.dumps({
                    "id_token": {"auth_time": {"essential": True}},
                }, separators=(",", ":")),
            }
            if fresh:
                # A signed-in application session is not recent-auth proof.
                # Force the identity provider to authenticate again; callback
                # validation below also rejects a stale returned auth_time.
                # The OAuth client library drops integer ``0`` as falsey;
                # use the protocol's string representation so the parameter
                # is actually present in the authorization request.
                authorization_options["max_age"] = "0"
            url, _ = flow.authorization_url(
                **authorization_options,
            )
            sealed = _seal_state({
                "iat": int(time.time()),
                "state": state,
                "nonce": nonce,
                "verifier": verifier,
                "redirect_uri": redirect_uri,
                "fresh": fresh,
                # This is validated before it enters the signed state. It is
                # deliberately a local path so completing sign-in can resume a
                # connector-consent request without creating an open redirect.
                "return_path": auth.safe_local_return_path(
                    request.query_params.get("next", "/")),
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
            return _error_response(
                "cancelled",
                secure=secure,
                fresh=pending.get("fresh") is True,
                return_path=str(pending.get("return_path") or "/"),
            )
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
            return _error_response(
                "exchange_failed",
                secure=secure,
                fresh=pending.get("fresh") is True,
                return_path=str(pending.get("return_path") or "/"),
            )

        email = str(claims.get("email") or "").lower()
        if not email or claims.get("email_verified") is not True:
            return _error_response(
                "unverified_email",
                secure=secure,
                fresh=pending.get("fresh") is True,
                return_path=str(pending.get("return_path") or "/"),
            )
        if not auth.email_may_log_in(email):
            return _error_response(
                "not_authorized",
                secure=secure,
                fresh=pending.get("fresh") is True,
                return_path=str(pending.get("return_path") or "/"),
            )
        session_auth = _session_auth_time(claims)
        if session_auth is None:
            # Do not create a valid-looking session that the server's durable
            # authority resolver must immediately reject.
            return _error_response(
                "authentication_time_missing",
                secure=secure,
                fresh=pending.get("fresh") is True,
                return_path=str(pending.get("return_path") or "/"),
            )
        auth_time, auth_time_source = session_auth
        if pending.get("fresh") is True:
            age = int(time.time()) - auth_time
            if (age < -ID_TOKEN_CLOCK_SKEW_SECONDS
                    or age > ID_TOKEN_ISSUED_AT_MAX_AGE_SECONDS):
                return _error_response(
                    "authentication_time_missing",
                    secure=secure,
                    fresh=True,
                    return_path=str(pending.get("return_path") or "/"),
                )

        response = RedirectResponse(
            auth.safe_local_return_path(str(pending.get("return_path") or "/")),
            status_code=303,
        )
        response.set_cookie(
            auth.SESSION_COOKIE,
            auth.mint_session(
                email,
                str(claims.get("name") or ""),
                subject=str(claims.get("sub") or ""),
                auth_time=auth_time,
                auth_time_source=auth_time_source,
            ),
            **_cookie_kwargs(secure=secure, max_age=auth.SESSION_TTL_SECONDS),
        )
        response.delete_cookie(STATE_COOKIE, path="/", secure=secure, samesite="lax")
        return response

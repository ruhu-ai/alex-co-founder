"""App-layer auth gate (docs/12): founder token + signed login sessions.

Two independent ways to be "the founder":

1. Founder token (dev/ops path, unchanged): APP_AUTH_TOKEN presented as
   Bearer/X-App-Key/cookie, bootstrapped once via /?key=<token>. This is what
   the e2e scripts, curl, and the manual-run task buttons keep using.

2. Login session (founder path): /login.html signs in with email+password or
   Google via Firebase Auth, then POSTs the Firebase ID token to
   /auth/session. The server verifies the token with Google's public certs
   (google-auth — no Firebase Admin SDK), requires a VERIFIED email on the
   ALLOWED_LOGIN_EMAILS allowlist, and mints its own signed HttpOnly session
   cookie. /auth/me feeds the account menu; /auth/logout clears the cookie.

Cloud Run stays --allow-unauthenticated at the platform layer because the
mock portal webhook and Pub/Sub push must reach us without IAM identities —
so the app enforces its own gate instead: every route requires one of the two
credentials above except /healthz, /login.html, /auth/*, and the routes that
carry their own verification (portal/alex webhooks, OIDC task routes).

Posture matches the other verifiers in app/main.py: local dev (no K_SERVICE)
is open when nothing is configured; production fails closed — an unset
allowlist rejects every login, an unset signing secret rejects every session.

The email/password path REQUIRES email_verified: without it, anyone could
register an allowlisted address with their own password and impersonate the
founder before the real owner ever signs up.
"""

import base64
import hmac
import json
import os
import time
from urllib.parse import urlencode

COOKIE_NAME = "app_auth"
SESSION_COOKIE = "app_session"
QUERY_PARAM = "key"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14  # matches the token cookie

# Browser chrome and candidate-facing hiring notices are intentionally public.
# Dynamic public prefixes are narrow and their route handlers expose only the
# published contract plus a role-bound application token.
PUBLIC_PATHS = frozenset({"/favicon.ico", "/favicon.svg", "/hiring-notice.html"})
PUBLIC_PREFIXES = ("/jobs/", "/api/public/hiring/roles/")

# Routes that verify their own callers (portal token, OIDC) or must stay
# reachable for probes and for signing in. Everything else requires a
# founder credential.
EXEMPT_PREFIXES = ("/health", "/webhooks/", "/tasks/",  # "/health" covers /healthz
                   "/auth/", "/login.html")


def configured_token() -> str:
    return os.environ.get("APP_AUTH_TOKEN", "")


def _in_cloud_run() -> bool:
    return bool(os.environ.get("K_SERVICE"))


# ---------------------------------------------------------------------------
# Login sessions (Firebase Auth → our own signed cookie)
# ---------------------------------------------------------------------------

def firebase_config() -> dict:
    """Client config for /login.html. The web API key is a public identifier
    (it names the project, it grants nothing) — the gate is server-side."""
    project = (os.environ.get("FIREBASE_PROJECT_ID")
               or os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
    api_key = os.environ.get("FIREBASE_WEB_API_KEY", "")
    firebase_enabled = bool(api_key and project)
    google_enabled = bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
                          and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"))
    return {
        "enabled": firebase_enabled or google_enabled,
        "firebaseEnabled": firebase_enabled,
        "googleEnabled": google_enabled,
        "apiKey": api_key,
        "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN",
                                     f"{project}.firebaseapp.com" if project else ""),
        "projectId": project,
    }


def _session_secret() -> str:
    """Cookie-signing secret. Falls back to the founder token so a deploy that
    already binds APP_AUTH_TOKEN needs no new secret; local dev falls back to
    a fixed value (same open-by-default posture as the rest of this module)."""
    secret = os.environ.get("APP_SESSION_SECRET", "") or configured_token()
    if not secret and not _in_cloud_run():
        secret = "dev-session-secret"
    return secret


def allowed_login_emails() -> set[str]:
    raw = os.environ.get("ALLOWED_LOGIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def email_may_log_in(email: str) -> bool:
    """Allowlist check. Production fails closed on an empty allowlist; local
    dev accepts any verified account so the flow can be exercised."""
    allowed = allowed_login_emails()
    if not allowed:
        return not _in_cloud_run()
    return email.lower() in allowed


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), "sha256").hexdigest()


def mint_session(email: str, name: str = "", *, subject: str = "",
                 auth_time: int | None = None) -> str:
    """Signed, self-contained session value: base64url(claims).hmac."""
    secret = _session_secret()
    if not secret:
        raise RuntimeError("no session signing secret configured")
    claims = {
        "email": email, "name": name,
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }
    # Hiring requires both values and rejects legacy/token sessions. The
    # identity provider's original auth_time is preserved; refreshing this
    # application cookie never makes authentication newer.
    if subject:
        claims["sub"] = subject
    if isinstance(auth_time, int):
        claims["auth_time"] = auth_time
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    return f"{payload}.{_sign(payload, secret)}"


def read_session(value: str) -> dict | None:
    """The verified claims, or None for a missing/tampered/expired cookie."""
    secret = _session_secret()
    if not secret or not value or "." not in value:
        return None
    payload, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload, secret), sig):
        return None
    try:
        claims = json.loads(_unb64url(payload))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(claims, dict) or claims.get("exp", 0) < time.time():
        return None
    return claims


def _session_claims(cookies) -> dict | None:
    return read_session(cookies.get(SESSION_COOKIE, ""))


def session_claims(request) -> dict | None:
    """Verified signed-session claims for server-side identity resolution."""
    return _session_claims(request.cookies)


def csrf_token(request) -> str:
    """Cookie-bound CSRF token for every browser-authenticated mutation."""
    value = request.cookies.get(SESSION_COOKIE, "")
    valid = bool(value and read_session(value) is not None)
    if not valid:
        value = request.cookies.get(COOKIE_NAME, "")
        valid = bool(value and configured_token()
                     and hmac.compare_digest(value, configured_token()))
    secret = _session_secret()
    if not value or not secret or not valid:
        return ""
    return hmac.new(secret.encode(), f"platform-csrf-v2\x1f{value}".encode(),
                    "sha256").hexdigest()


def csrf_is_valid(request) -> bool:
    expected = csrf_token(request)
    presented = request.headers.get("X-CSRF-Token", "")
    return bool(expected and presented and hmac.compare_digest(expected, presented))


# ---------------------------------------------------------------------------
# Request gating (token OR session)
# ---------------------------------------------------------------------------

def _presented_token(headers, cookies, query_params) -> str:
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ")
    return (headers.get("X-App-Key", "")
            or cookies.get(COOKIE_NAME, "")
            or query_params.get(QUERY_PARAM, ""))


def _token_ok(presented: str) -> bool:
    expected = configured_token()
    if not expected:
        # Local-only convenience; production fails closed (docs/12 posture).
        return not _in_cloud_run()
    return bool(presented) and hmac.compare_digest(presented, expected)


def request_is_founder(request) -> bool:
    """True when the HTTP request carries the founder token, a valid login
    session, or dev-open applies."""
    if _session_claims(request.cookies):
        return True
    return _token_ok(_presented_token(
        request.headers, request.cookies, request.query_params))


def websocket_is_founder(websocket) -> bool:
    """The /live handshake: login-session or token cookie from bootstrap, or
    explicit ?key= on the websocket URL itself."""
    if _session_claims(websocket.cookies):
        return True
    return _token_ok(_presented_token(
        websocket.headers, websocket.cookies, websocket.query_params))


def _wants_html(request) -> bool:
    """A real browser navigating for an HTML document (vs programmatic API
    calls with Accept: */*)."""
    return (request.method == "GET"
            and "text/html" in request.headers.get("accept", ""))


def _strip_key_query(url) -> str:
    """The request path with the ?key= parameter removed (other params kept)."""
    from urllib.parse import parse_qsl, urlencode

    remaining = urlencode([(k, v) for k, v
                           in parse_qsl(url.query, keep_blank_values=True)
                           if k != QUERY_PARAM])
    return url.path + (f"?{remaining}" if remaining else "")


def safe_local_return_path(candidate: str) -> str:
    """Return a bounded same-origin path suitable for post-login navigation.

    Authentication is a redirect boundary. ``next`` therefore accepts only a
    path and query on this application, never an absolute or scheme-relative
    URL. The access-key parameter is also removed before it can reach browser
    history via a login round-trip.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit

    if not isinstance(candidate, str) or len(candidate) > 2048:
        return "/"
    parsed = urlsplit(candidate)
    if (not parsed.path.startswith("/") or parsed.path.startswith("//")
            or parsed.scheme or parsed.netloc or "\\" in candidate):
        return "/"
    query = urlencode([(key, value) for key, value in parse_qsl(
        parsed.query, keep_blank_values=True) if key != QUERY_PARAM])
    return parsed.path + (f"?{query}" if query else "")


async def _verify_firebase_id_token(id_token: str) -> dict:
    """Verify a Firebase ID token against Google's public certs. Returns the
    claims; raises ValueError on any failure. The cert fetch is blocking HTTP
    — kept off the event loop (docs/07)."""
    import asyncio

    from google.auth.transport import requests as _auth_requests
    from google.oauth2 import id_token as _id_token

    project = firebase_config()["projectId"]
    if not project:
        raise ValueError("FIREBASE_PROJECT_ID is not configured")
    claims = await asyncio.to_thread(
        _id_token.verify_firebase_token, id_token,
        _auth_requests.Request(), audience=project)
    if claims.get("iss") != f"https://securetoken.google.com/{project}":
        raise ValueError("wrong token issuer")
    return claims


def install(app) -> None:
    """Gate every non-exempt route; register the /auth/* routes."""
    from fastapi import Request
    from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
    from pydantic import BaseModel

    from app import google_login

    def _cookie_kwargs() -> dict:
        # HttpOnly so JS can't read it; it rides the SPA's fetches and the
        # /live handshake. secure only in Cloud Run (localhost dev is http).
        return dict(httponly=True, samesite="lax", secure=_in_cloud_run(),
                    max_age=SESSION_TTL_SECONDS)

    def _set_bootstrap_cookie(response) -> None:
        response.set_cookie(COOKIE_NAME, configured_token(), **_cookie_kwargs())

    # ---- login/session routes (all under the exempt /auth/ prefix) --------

    class SessionRequest(BaseModel):
        id_token: str

    @app.get("/login.html", include_in_schema=False)
    async def login_page():
        # Auth flows and their error handling are security-sensitive and change
        # independently of the main static bundle. Never let a browser's memory
        # cache or back-forward cache resurrect an obsolete OAuth implementation.
        return FileResponse(
            "app/static/login.html",
            media_type="text/html",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "Referrer-Policy": "no-referrer",
            },
        )

    @app.get("/auth/config")
    async def auth_config():
        return firebase_config()

    @app.post("/auth/session")
    async def auth_session(payload: SessionRequest):
        if not firebase_config()["firebaseEnabled"]:
            return JSONResponse({"error": "sign-in is not configured"}, status_code=503)
        if not _session_secret():
            return JSONResponse(
                {"error": "APP_SESSION_SECRET not configured — deploy.sh binds "
                          "it (or APP_AUTH_TOKEN) from Secret Manager"},
                status_code=503)
        try:
            claims = await _verify_firebase_id_token(payload.id_token)
        except Exception:
            return JSONResponse({"error": "invalid sign-in token"}, status_code=401)
        email = (claims.get("email") or "").lower()
        # email_verified is the takeover guard — see module docstring.
        if not email or not claims.get("email_verified"):
            return JSONResponse(
                {"error": "verify your email address first — check your inbox "
                          "for the verification link"}, status_code=403)
        if not email_may_log_in(email):
            return JSONResponse(
                {"error": f"{email} is not authorized for this workspace"},
                status_code=403)
        resp = JSONResponse({"status": "success", "email": email})
        resp.set_cookie(SESSION_COOKIE,
                        mint_session(email, claims.get("name", ""),
                                     subject=str(claims.get("sub") or ""),
                                     auth_time=(int(claims["auth_time"])
                                                if isinstance(claims.get("auth_time"),
                                                              (int, float)) else None)),
                        **_cookie_kwargs())
        return resp

    @app.post("/auth/logout")
    async def auth_logout():
        resp = JSONResponse({"status": "success"})
        resp.delete_cookie(SESSION_COOKIE)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    @app.get("/auth/me")
    async def auth_me(request: Request):
        claims = _session_claims(request.cookies)
        if claims:
            return {"status": "success", "authenticated": True,
                    "mode": "session",
                    "email": claims.get("email", ""),
                    "name": claims.get("name", ""),
                    "csrf_token": csrf_token(request),
                    "sign_in_enabled": firebase_config()["enabled"]}
        if _token_ok(_presented_token(request.headers, request.cookies,
                                      request.query_params)):
            return {"status": "success", "authenticated": True,
                    "mode": "token" if configured_token() else "open",
                    "email": "", "name": "",
                    "csrf_token": csrf_token(request),
                    "sign_in_enabled": firebase_config()["enabled"]}
        # This endpoint is a session probe used by the public login page.
        # Signed-out is a normal state, not a failed request; returning 200
        # avoids a misleading console error without weakening the middleware
        # gate on any protected route.
        return {"status": "success", "authenticated": False,
                "mode": "anonymous", "email": "", "name": "",
                "sign_in_enabled": firebase_config()["enabled"]}

    google_login.register(app)

    # ---- the gate ----------------------------------------------------------

    @app.middleware("http")
    async def _founder_gate(request: Request, call_next):
        path = request.url.path
        if (path in PUBLIC_PATHS
                or any(path.startswith(p) for p in PUBLIC_PREFIXES)
                or any(path.startswith(p) for p in EXEMPT_PREFIXES)):
            return await call_next(request)
        if (not configured_token() and not firebase_config()["enabled"]
                and _in_cloud_run()):
            return JSONResponse(
                {"error": "no auth configured — deploy.sh binds APP_AUTH_TOKEN "
                          "from Secret Manager, or set FIREBASE_WEB_API_KEY + "
                          "ALLOWED_LOGIN_EMAILS for sign-in"}, status_code=503)
        if not request_is_founder(request):
            # A person navigating gets the sign-in page (which explains the
            # ?key= flow when sign-in isn't configured); programmatic callers
            # always get the bare 401.
            if _wants_html(request):
                return RedirectResponse(
                    "/login.html?" + urlencode({
                        "next": safe_local_return_path(
                            _strip_key_query(request.url)),
                    }),
                    status_code=303,
                )
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # SameSite is defense in depth, not the mutation authorization. A
        # browser request relying on either HttpOnly auth cookie must prove it
        # read a same-origin CSRF token. Explicit Authorization/X-App-Key
        # clients and self-verifying task/webhook routes do not use this path.
        cookie_authenticated = bool(
            _session_claims(request.cookies)
            or (request.cookies.get(COOKIE_NAME)
                and configured_token()
                and hmac.compare_digest(
                    request.cookies.get(COOKIE_NAME, ""), configured_token())))
        explicit_credential = bool(
            request.headers.get("Authorization", "").startswith("Bearer ")
            or request.headers.get("X-App-Key", ""))
        if (request.method in {"POST", "PUT", "PATCH", "DELETE"}
                and cookie_authenticated and not explicit_credential
                and not csrf_is_valid(request)):
            return JSONResponse(
                {"status": "error", "error": True,
                 "error_code": "csrf_failed",
                 "message": "A valid same-origin CSRF token is required."},
                status_code=403)

        # ?key= bootstrap: a valid token in the query and no cookie yet. Set the
        # grant cookie once. hmac.compare_digest, not ==, to keep the token
        # comparison constant-time.
        presented_key = request.query_params.get(QUERY_PARAM, "")
        is_bootstrap = (configured_token() and presented_key
                        and hmac.compare_digest(presented_key, configured_token())
                        and not request.cookies.get(COOKIE_NAME))
        # For a top-level page navigation, redirect to the same URL WITHOUT the
        # key so the token stops lingering in the address bar / referer /
        # history; the cookie carries the grant from here on.
        if is_bootstrap and _wants_html(request):
            resp = RedirectResponse(_strip_key_query(request.url), status_code=303)
            _set_bootstrap_cookie(resp)
            return resp

        response = await call_next(request)
        if is_bootstrap:
            _set_bootstrap_cookie(response)
        return response

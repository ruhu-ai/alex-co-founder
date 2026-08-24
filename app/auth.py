"""App-layer auth gate (docs/12): one shared founder token.

Cloud Run stays --allow-unauthenticated at the platform layer because the
mock portal webhook and Pub/Sub push must reach us without IAM identities —
so the app enforces its own gate instead: every route requires the founder
token except /healthz and the routes that carry their own verification
(portal/alex webhooks, OIDC task routes). The founder bootstraps once by
opening /?key=<token>; the middleware sets an HttpOnly cookie and the UI
works unchanged from then on (the cookie also rides the /live websocket
handshake).

Posture matches the other verifiers in app/main.py: local dev (no K_SERVICE)
is open when APP_AUTH_TOKEN is unset; production fails closed.
"""

import hmac
import os

COOKIE_NAME = "app_auth"
QUERY_PARAM = "key"

# Routes that verify their own callers (portal token, OIDC) or must stay
# reachable for probes. Everything else requires the founder token.
EXEMPT_PREFIXES = ("/healthz", "/webhooks/", "/tasks/")


def configured_token() -> str:
    return os.environ.get("APP_AUTH_TOKEN", "")


def _in_cloud_run() -> bool:
    return bool(os.environ.get("K_SERVICE"))


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
    """True when the HTTP request carries the founder token (or dev-open)."""
    return _token_ok(_presented_token(
        request.headers, request.cookies, request.query_params))


def websocket_is_founder(websocket) -> bool:
    """The /live handshake: cookie from the ?key= bootstrap, or explicit
    ?key= on the websocket URL itself."""
    return _token_ok(_presented_token(
        websocket.headers, websocket.cookies, websocket.query_params))


def _wants_clean_redirect(request) -> bool:
    """A real browser opening /?key=<token> navigates for an HTML document —
    that is the documented bootstrap and the one URL the token must be stripped
    from (address bar, referer, history). Programmatic ?key= callers (Accept:
    */*) keep the set-cookie-only path and are not bounced through a redirect."""
    return (request.method == "GET"
            and "text/html" in request.headers.get("accept", ""))


def _strip_key_query(url) -> str:
    """The request path with the ?key= parameter removed (other params kept)."""
    from urllib.parse import parse_qsl, urlencode

    remaining = urlencode([(k, v) for k, v
                           in parse_qsl(url.query, keep_blank_values=True)
                           if k != QUERY_PARAM])
    return url.path + (f"?{remaining}" if remaining else "")


def install(app) -> None:
    """Gate every non-exempt route behind the founder token."""
    from fastapi import Request
    from fastapi.responses import JSONResponse, RedirectResponse

    def _set_bootstrap_cookie(response) -> None:
        # HttpOnly so JS can't read it; it rides the SPA's fetches and the /live
        # handshake. secure only in Cloud Run (localhost dev is plain http).
        response.set_cookie(
            COOKIE_NAME, configured_token(), httponly=True,
            samesite="lax", secure=_in_cloud_run(), max_age=60 * 60 * 24 * 14)

    @app.middleware("http")
    async def _founder_gate(request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)
        if not configured_token() and _in_cloud_run():
            return JSONResponse(
                {"error": "APP_AUTH_TOKEN not configured — deploy.sh binds it "
                          "from Secret Manager"}, status_code=503)
        if not request_is_founder(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

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
        if is_bootstrap and _wants_clean_redirect(request):
            resp = RedirectResponse(_strip_key_query(request.url), status_code=303)
            _set_bootstrap_cookie(resp)
            return resp

        response = await call_next(request)
        if is_bootstrap:
            _set_bootstrap_cookie(response)
        return response

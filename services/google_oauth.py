"""Founder-scoped Google OAuth (docs/12) + Alex's mailbox account (adr/001 v2).

Two accounts, each with its own refresh token:
  - "founder": read-only Drive + Gmail + Calendar (GOOGLE_OAUTH_REFRESH_TOKEN)
  - "alex":    the alex@ruhu.ai role mailbox (ALEX_OAUTH_REFRESH_TOKEN) —
               gmail.readonly + gmail.send; sending is approval-gated in code
               (services/alex_mailbox.py), never by scope alone.

Refresh tokens are obtained via the Connectors panel (in-browser loopback
flow) or scripts/oauth_setup.py, and stored in Secret Manager (prod) or .env
(local dev). Access tokens are minted at execution time, held only in memory,
and never enter session state, prompts, or logs.

Adapters degrade to errors-as-data when OAuth is not configured.
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request

SCOPE_MAP = {
    "drive": [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.file",  # sync produced documents; per-file only
    ],
    "founder_gmail": ["https://www.googleapis.com/auth/gmail.readonly"],
    "calendar": [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",  # booking is
        # approval-gated in code (services/calendar_adapter.py) — docs/adr/002
    ],
    "alex_mail": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",  # gated in code, never autonomous
    ],
}
# Which Google account each connector auths as (adr/001: Alex's mailbox is a
# separate Workspace user). Unlisted connectors use the founder account.
CONNECTOR_ACCOUNT = {"alex_mail": "alex"}
ACCOUNT_ENV = {"founder": "GOOGLE_OAUTH_REFRESH_TOKEN",
               "alex": "ALEX_OAUTH_REFRESH_TOKEN"}

# Subset that decides "connected" in the panel — write scopes are upgrades,
# not status requirements (a readonly-granted calendar still shows Connected;
# booking degrades to an error until the founder re-consents).
STATUS_SCOPES = {
    "calendar": ["https://www.googleapis.com/auth/calendar.readonly"],
}

# Full founder grant (union of founder-account scopes) — used by
# scripts/oauth_setup.py and as the callback flow's scope set. Per-connector
# Connect buttons request only their own scopes; Google merges them into one
# grant (incremental authorization).
# calendar.events (booking) ships only with approval-gated invites — docs/adr/002.
SCOPES = [s for group in SCOPE_MAP.values() for s in group
          if group != SCOPE_MAP["alex_mail"]]
# Union including Alex's scopes — used only by the OAuth callback flow so
# oauthlib's scope check never trips on an alex_mail consent.
ALL_SCOPES = [s for group in SCOPE_MAP.values() for s in group]

_creds: dict = {}          # per-account
_granted: dict = {}        # per-account frozenset of scopes the token carries


_sm_missing: set = set()  # accounts whose token Secret Manager does NOT have —
# cached for the process: without this, every panel status check re-blocks on
# a slow Secret Manager call (grpc retries can stall the server for minutes).
# Only negatives are cached, so a token added later via Connect still wins
# (the env var is checked first, every time).


def runtime_value(key: str) -> str:
    """Read a mutable connector value from env or Secret Manager by name."""
    value = os.environ.get(key, "")
    if value:
        return value
    if not os.environ.get("K_SERVICE"):
        return ""
    try:
        from services import secrets

        return secrets.get(key)
    except Exception:
        return ""


def _refresh_token(account: str = "founder") -> str:
    token = runtime_value(ACCOUNT_ENV.get(account, ""))
    if token:
        return token
    if account in _sm_missing:
        return ""
    try:
        from services import secrets

        return secrets.get(ACCOUNT_ENV.get(account, ""))
    except Exception as exc:
        # Negative-cache ONLY a definitive "not configured" — no project set
        # (KeyError) or the secret genuinely does not exist (NotFound). A
        # transient Secret Manager error must NOT block the account until the
        # next restart; leave it uncached so the next call retries.
        from google.api_core import exceptions as gexc

        if isinstance(exc, (KeyError, gexc.NotFound)):
            _sm_missing.add(account)
        return ""


def configured(connector: str | None = None, account: str = "founder") -> bool:
    """OAuth ready? With `connector`, True only when that connector's scopes
    were actually granted on its account (per-connector Connect buttons,
    incremental auth)."""
    if connector:
        account = CONNECTOR_ACCOUNT.get(connector, "founder")
    if not (
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        and _refresh_token(account)
    ):
        return False
    if connector is None:
        return True
    required = STATUS_SCOPES.get(connector, SCOPE_MAP.get(connector, ()))
    return set(required) <= set(granted_scopes(account))


def granted_scopes(account: str = "founder") -> frozenset:
    """Scopes the stored token really carries (tokeninfo), cached per token."""
    if account in _granted:
        return _granted[account]
    creds = get_credentials(account)
    if creds is None or not creds.token:
        return frozenset()
    try:
        from googleapiclient.discovery import build

        info = build("oauth2", "v2", credentials=creds,
                     cache_discovery=False).tokeninfo(access_token=creds.token).execute()
        _granted[account] = frozenset((info.get("scope") or "").split())
    except Exception:
        return frozenset()
    return _granted[account]


def get_credentials(account: str = "founder"):
    """User credentials minted from the account's stored refresh token, or
    None when OAuth is not configured for that account."""
    if not (
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        and _refresh_token(account)
    ):
        return None
    creds = _creds.get(account)
    if creds is None or not creds.valid:
        import google.auth.transport.requests
        import google.oauth2.credentials

        creds = google.oauth2.credentials.Credentials(
            token=None,
            refresh_token=_refresh_token(account),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            scopes=None,  # the access token inherits the grant's scopes; asking
            # for MORE than granted fails the refresh with invalid_scope
        )
        creds.refresh(google.auth.transport.requests.Request())
        _creds[account] = creds
    return creds


def reset_for_tests() -> None:
    global _account_email
    _creds.clear()
    _granted.clear()
    _sm_missing.clear()
    _account_email = ""


def clear_account_cache(account: str) -> None:
    """Drop every in-process credential/status cache for one Google account."""
    global _account_email
    _creds.pop(account, None)
    _granted.pop(account, None)
    _sm_missing.discard(account)
    if account == "founder":
        _account_email = ""


_account_email = ""


def account_email(account: str = "founder") -> str:
    """Email of the connected Google account (for the Connections panel).
    Cached; empty string when unconfigured. Gmail profile is the cheapest
    endpoint our read-only scopes can call; Drive about works as fallback."""
    global _account_email
    if account != "founder":
        return _account_email_for(account)
    if _account_email:
        return _account_email
    _account_email = _account_email_for(account)
    return _account_email


def _account_email_for(account: str) -> str:
    creds = get_credentials(account)
    if creds is None:
        return ""
    from googleapiclient.discovery import build

    try:  # gmail scope granted?
        prof = build("gmail", "v1", credentials=creds,
                     cache_discovery=False).users().getProfile(userId="me").execute()
        return prof.get("emailAddress", "")
    except Exception:
        try:  # fall back to Drive (works with drive scopes only)
            about = build("drive", "v3", credentials=creds,
                          cache_discovery=False).about().get(fields="user").execute()
            return about.get("user", {}).get("emailAddress", "")
        except Exception:
            return ""


def save_env_var(key: str, value: str) -> dict:
    """Persist a connector value durably, then make it live in this process.

    On Cloud Run (``K_SERVICE`` set) the durable store is Secret Manager
    (docs/12) — the local filesystem is ephemeral, so a token written only to
    .env is lost on the next cold start and sits in plaintext meanwhile. Local
    dev keeps the .env path. Empty value durably deletes the connector secret.
    Token values are never logged. Errors are returned as data and the live
    process is not changed unless durable persistence succeeds."""
    if os.environ.get("K_SERVICE"):
        try:
            from services import secrets

            secrets.put(key, value) if value else secrets.delete(key)
        except Exception as exc:
            return {"status": "error", "error": True,
                    "message": f"secret persistence failed: {exc}"[:200]}
        os.environ.pop(key, None)
        if value:
            os.environ[key] = value
        return {"status": "success"}
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        lines = []
        if os.path.exists(env_path):
            with open(env_path) as fh:
                lines = [ln for ln in fh.read().splitlines() if not ln.startswith(f"{key}=")]
        if value:
            lines.append(f"{key}={value}")
        with open(env_path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        return {"status": "error", "error": True,
                "message": f"local connector persistence failed: {exc}"[:200]}
    os.environ.pop(key, None)
    if value:
        os.environ[key] = value
    return {"status": "success"}


def save_refresh_token(token: str, account: str = "founder") -> dict:
    """Persist a newly-consented refresh token — no restart needed (cached
    creds and account identity are reset)."""
    result = save_env_var(ACCOUNT_ENV.get(account, "GOOGLE_OAUTH_REFRESH_TOKEN"), token)
    if result.get("status") == "success":
        reset_for_tests()
    return result


def verify_consent(credentials, connector: str) -> dict:
    """Verify a fresh OAuth token's scopes and provider account identity.

    This is intentionally called only in the OAuth callback, never while
    rendering connector status.  Returned failures are safe data and never
    include provider bodies or tokens.
    """
    if connector not in SCOPE_MAP:
        return {"status": "error", "error": True,
                "error_code": "invalid_contract",
                "message": "unknown Google connector"}
    try:
        from googleapiclient.discovery import build

        info = build("oauth2", "v2", credentials=credentials,
                     cache_discovery=False).tokeninfo(
                         access_token=credentials.token).execute()
        granted = sorted(set((info.get("scope") or "").split()))
        required = set(STATUS_SCOPES.get(connector, SCOPE_MAP[connector]))
        if not required.issubset(granted):
            return {"status": "error", "error": True,
                    "error_code": "scope_missing",
                    "message": "required Google scope was not granted"}
        if connector == "drive":
            identity = build("drive", "v3", credentials=credentials,
                             cache_discovery=False).about().get(
                                 fields="user").execute().get("user", {})
            hint = identity.get("emailAddress", "")
        elif connector in {"founder_gmail", "alex_mail"}:
            identity = build("gmail", "v1", credentials=credentials,
                             cache_discovery=False).users().getProfile(
                                 userId="me").execute()
            hint = identity.get("emailAddress", "")
        else:
            identity = build("calendar", "v3", credentials=credentials,
                             cache_discovery=False).calendarList().get(
                                 calendarId="primary").execute()
            hint = identity.get("id", "")
        if not hint:
            return {"status": "error", "error": True,
                    "error_code": "provider_rejected",
                    "message": "Google account identity could not be verified"}
        local, sep, domain = hint.partition("@")
        masked = ((local[:1] + "***@" + domain) if sep else "Google account")
        return {"status": "success", "granted_scopes": granted,
                "account_hint": masked}
    except Exception:
        return {"status": "error", "error": True,
                "error_code": "provider_unavailable",
                "message": "Google account verification failed"}


def revoke_account_grant(account: str = "founder", timeout_seconds: int = 10
                         ) -> dict:
    """Revoke the account-wide refresh-token grant at Google.

    The caller must already have decided that account-wide revocation is safe.
    A missing token is uncertain, not success: local absence does not prove the
    provider grant was revoked.
    """
    token = _refresh_token(account)
    if not token:
        return {"status": "error", "error": True,
                "error_code": "remote_revocation_uncertain",
                "message": "provider revocation could not be confirmed"}
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/revoke",
        data=urllib.parse.urlencode({"token": token}).encode(), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise OSError("unexpected status")
    except Exception:
        return {"status": "error", "error": True,
                "error_code": "remote_revocation_uncertain",
                "message": "provider revocation could not be confirmed"}
    return {"status": "success"}


def delete_account_credential(account: str = "founder") -> dict:
    """Delete the named refresh-token secret and invalidate live caches."""
    key = ACCOUNT_ENV.get(account)
    if not key:
        return {"status": "error", "error": True,
                "error_code": "invalid_contract",
                "message": "unknown Google account"}
    result = save_env_var(key, "")
    if result.get("status") == "success":
        clear_account_cache(account)
    return result

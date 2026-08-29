"""Founder-scoped Google OAuth plus Alex's role-owned Google account.

Two accounts, each with its own refresh token:
  - "founder": read-only Drive + Gmail + Calendar (GOOGLE_OAUTH_REFRESH_TOKEN)
  - "alex":    the alex@ruhu.ai role account (ALEX_OAUTH_REFRESH_TOKEN) —
               full Drive plus mailbox/calendar scopes granted separately.
               Provider scopes never replace application approval gates.

Refresh tokens are obtained via the Connectors panel (in-browser loopback
flow) or scripts/oauth_setup.py, and stored in Secret Manager (prod) or .env
(local dev). Access tokens are minted at execution time, held only in memory,
and never enter session state, prompts, or logs.

Adapters degrade to errors-as-data when OAuth is not configured.
"""

from __future__ import annotations

import hashlib
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SCOPE_MAP = {
    "drive": [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.file",  # sync produced documents; per-file only
    ],
    # Alex's role-owned Drive is operational storage, not a founder data source.
    # The provider grant permits full read/write access inside that account;
    # application code still owns every consequence/approval boundary.
    "alex_drive": ["https://www.googleapis.com/auth/drive"],
    "founder_gmail": ["https://www.googleapis.com/auth/gmail.readonly"],
    "calendar": [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",  # booking is
        # approval-gated in code (services/calendar_adapter.py) — docs/adr/002
        "openid", "https://www.googleapis.com/auth/userinfo.email",
    ],
    "alex_mail": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",  # gated in code, never autonomous
        "openid", "https://www.googleapis.com/auth/userinfo.email",
    ],
    "alex_calendar": [
        "https://www.googleapis.com/auth/calendar.events",
        "openid", "https://www.googleapis.com/auth/userinfo.email",
    ],
}
# Which Google account each connector auths as (adr/001: Alex's mailbox is a
# separate Workspace user). Unlisted connectors use the founder account.
CONNECTOR_ACCOUNT = {
    "alex_drive": "alex",
    "alex_mail": "alex",
    "alex_calendar": "alex",
}
ACCOUNT_ENV = {"founder": "GOOGLE_OAUTH_REFRESH_TOKEN",
               "alex": "ALEX_OAUTH_REFRESH_TOKEN"}


def expected_account_email(account: str) -> str:
    """Exact provider identity required for a role-owned OAuth slot."""
    if account == "alex":
        return os.environ.get("ALEX_ROLE_EMAIL", "alex@ruhu.ai").strip().lower()
    return ""


def provider_account_hash(email: str) -> str:
    """Stable opaque correlation key for provider-originated notifications."""
    normalized = (email or "").strip().lower()
    if not normalized:
        return ""
    return "sha256:" + hashlib.sha256(
        f"google-account-v1\x1e{normalized}".encode()).hexdigest()

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
SCOPES = [scope for connector, group in SCOPE_MAP.items()
          if CONNECTOR_ACCOUNT.get(connector, "founder") == "founder"
          for scope in group]
# Union including Alex's scopes — used only by the OAuth callback flow so
# oauthlib's scope check never trips on an alex_mail consent.
ALL_SCOPES = [s for group in SCOPE_MAP.values() for s in group]

_creds: dict = {}          # per-workspace/account credential, refreshed on expiry
_granted: dict = {}        # per-workspace/account frozenset of granted scopes

# Refresh this far ahead of stated expiry so a token cannot lapse mid-call.
_TOKEN_REFRESH_SKEW = timedelta(seconds=120)


def _expires_within(creds, skew: timedelta) -> bool:
    """True when the credential has no expiry or expires inside SKEW."""
    expiry = getattr(creds, "expiry", None)
    if expiry is None:
        return True
    if expiry.tzinfo is None:  # google-auth stores naive UTC
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry - skew <= datetime.now(timezone.utc)


_sm_missing: set = set()  # credential slots Secret Manager does NOT have —
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


def credential_ref(account: str = "founder", workspace_id: str = "") -> str:
    """Opaque secret/.env key for one workspace's provider account.

    The legacy unscoped key remains available to local compatibility callers.
    Platform requests always supply ``workspace_id`` and therefore cannot read
    another workspace's OAuth grant, even for the same provider account role.
    """
    base = ACCOUNT_ENV.get(account, ACCOUNT_ENV["founder"])
    if not workspace_id:
        return base
    digest = hashlib.sha256(
        f"google-oauth-v1\x1e{workspace_id}\x1e{account}".encode()).hexdigest()[:40]
    return f"{base}_W_{digest}"


def _cache_key(account: str, workspace_id: str) -> tuple[str, str]:
    return (workspace_id, account)


def _refresh_token(account: str = "founder", workspace_id: str = "") -> str:
    slot = credential_ref(account, workspace_id)
    token = runtime_value(slot)
    if token:
        return token
    if slot in _sm_missing:
        return ""
    try:
        from services import secrets

        return secrets.get(slot)
    except Exception as exc:
        # Negative-cache ONLY a definitive "not configured" — no project set
        # (KeyError) or the secret genuinely does not exist (NotFound). A
        # transient Secret Manager error must NOT block the account until the
        # next restart; leave it uncached so the next call retries.
        from google.api_core import exceptions as gexc

        if isinstance(exc, (KeyError, gexc.NotFound)):
            _sm_missing.add(slot)
        return ""


def configured(connector: str | None = None, account: str = "founder",
               workspace_id: str = "") -> bool:
    """OAuth ready? With `connector`, True only when that connector's scopes
    were actually granted on its account (per-connector Connect buttons,
    incremental auth)."""
    if connector:
        account = CONNECTOR_ACCOUNT.get(connector, "founder")
    if not (
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        and _refresh_token(account, workspace_id)
    ):
        return False
    if connector is None:
        return True
    required = STATUS_SCOPES.get(connector, SCOPE_MAP.get(connector, ()))
    return set(required) <= set(granted_scopes(account, workspace_id))


def granted_scopes(account: str = "founder", workspace_id: str = "") -> frozenset:
    """Scopes the stored token really carries (tokeninfo), cached per token."""
    cache_key = _cache_key(account, workspace_id)
    if cache_key in _granted:
        return _granted[cache_key]
    creds = get_credentials(account, workspace_id)
    if creds is None or not creds.token:
        return frozenset()
    try:
        from googleapiclient.discovery import build

        info = build("oauth2", "v2", credentials=creds,
                     cache_discovery=False).tokeninfo(access_token=creds.token).execute()
        _granted[cache_key] = frozenset((info.get("scope") or "").split())
    except Exception:
        return frozenset()
    return _granted[cache_key]


def get_credentials(account: str = "founder", workspace_id: str = ""):
    """User credentials minted from the account's stored refresh token, or
    None when OAuth is not configured for that account."""
    if not (
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        and _refresh_token(account, workspace_id)
    ):
        return None
    import google.auth.transport.requests
    import google.oauth2.credentials

    # Access tokens are cached per account and reused only while they are
    # comfortably unexpired, so a Drive/Gmail/Calendar call does not pay a
    # token-endpoint round trip every time (docs/25 §7.8). The cache is keyed by
    # the refresh token in use, so rotating or revoking a grant — or switching
    # accounts — can never hand back the previous grant's access token.
    refresh_token = _refresh_token(account, workspace_id)
    cache_key = _cache_key(account, workspace_id)
    cached = _creds.get(cache_key)
    if (cached is not None and cached.refresh_token == refresh_token
            and cached.token and not _expires_within(cached, _TOKEN_REFRESH_SKEW)):
        return cached
    creds = google.oauth2.credentials.Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        scopes=None,  # the access token inherits the grant's scopes; asking
        # for MORE than granted fails the refresh with invalid_scope
    )
    creds.refresh(google.auth.transport.requests.Request())
    _creds[cache_key] = creds
    return creds


def reset_for_tests() -> None:
    _creds.clear()
    _granted.clear()
    _sm_missing.clear()
    _account_emails.clear()


def clear_account_cache(account: str, workspace_id: str = "") -> None:
    """Drop every in-process credential/status cache for one Google account."""
    key = _cache_key(account, workspace_id)
    _creds.pop(key, None)
    _granted.pop(key, None)
    _sm_missing.discard(credential_ref(account, workspace_id))
    _account_emails.pop(key, None)


_account_emails: dict[tuple[str, str], str] = {}


def account_email(account: str = "founder", workspace_id: str = "") -> str:
    """Email of the connected Google account (for the Connections panel).
    Cached; empty string when unconfigured. Gmail profile is the cheapest
    endpoint our read-only scopes can call; Drive about works as fallback."""
    key = _cache_key(account, workspace_id)
    if key not in _account_emails:
        _account_emails[key] = _account_email_for(account, workspace_id)
    return _account_emails[key]


def account_subject_hash(account: str = "founder", workspace_id: str = "") -> str:
    """Return the opaque Google OAuth subject hash, never the raw subject.

    Provider effect lanes use this to pin an account before an irreversible
    action.  It deliberately returns an empty value rather than falling back
    to an email address: an email is mutable account metadata, while the
    provider subject is the stable identity being approved.
    """
    creds = get_credentials(account, workspace_id)
    if creds is None:
        return ""
    try:
        from googleapiclient.discovery import build

        identity = build("oauth2", "v2", credentials=creds,
                         cache_discovery=False).userinfo().get().execute()
        subject = str(identity.get("id") or "")
        return "sha256:" + hashlib.sha256(subject.encode()).hexdigest() if subject else ""
    except Exception:
        return ""


def _account_email_for(account: str, workspace_id: str = "") -> str:
    creds = get_credentials(account, workspace_id)
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


def save_refresh_token(token: str, account: str = "founder",
                       workspace_id: str = "") -> dict:
    """Persist a newly-consented refresh token — no restart needed (cached
    creds and account identity are reset)."""
    result = save_env_var(credential_ref(account, workspace_id), token)
    if result.get("status") == "success":
        clear_account_cache(account, workspace_id)
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
        if connector in {"calendar", "alex_calendar"}:
            # These grants intentionally include userinfo.email so account
            # identity can be verified without broad Calendar metadata access.
            # calendar.events permits event operations but does not permit
            # calendarList.get("primary"), which returns a misleading 403.
            identity = build("oauth2", "v2", credentials=credentials,
                             cache_discovery=False).userinfo().get().execute()
            hint = identity.get("email", "")
        elif connector in {"drive", "alex_drive"}:
            identity = build("drive", "v3", credentials=credentials,
                             cache_discovery=False).about().get(
                                 fields="user").execute().get("user", {})
            hint = identity.get("emailAddress", "")
        elif connector in {"founder_gmail", "alex_mail"}:
            identity = build("gmail", "v1", credentials=credentials,
                             cache_discovery=False).users().getProfile(
                                 userId="me").execute()
            hint = identity.get("emailAddress", "")
        if not hint:
            return {"status": "error", "error": True,
                    "error_code": "provider_rejected",
                    "message": "Google account identity could not be verified"}
        account = CONNECTOR_ACCOUNT.get(connector, "founder")
        expected_email = expected_account_email(account)
        if expected_email and hint.strip().lower() != expected_email:
            return {"status": "error", "error": True,
                    "error_code": "account_mismatch",
                    "message": ("Sign in as the configured Alex role account; "
                                "the selected Google account was not accepted")}
        local, sep, domain = hint.partition("@")
        masked = ((local[:1] + "***@" + domain) if sep else "Google account")
        return {"status": "success", "granted_scopes": granted,
                "account_hint": masked,
                "provider_account_hash": provider_account_hash(hint)}
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Google consent verification failed for %s: %s",
            connector, type(exc).__name__)
        return {"status": "error", "error": True,
                "error_code": "provider_unavailable",
                "message": "Google account verification failed"}


def revoke_account_grant(account: str = "founder", timeout_seconds: int = 10,
                         workspace_id: str = ""
                         ) -> dict:
    """Revoke the account-wide refresh-token grant at Google.

    The caller must already have decided that account-wide revocation is safe.
    A missing token is uncertain, not success: local absence does not prove the
    provider grant was revoked.
    """
    token = _refresh_token(account, workspace_id)
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


def delete_account_credential(account: str = "founder",
                              workspace_id: str = "") -> dict:
    """Delete the named refresh-token secret and invalidate live caches."""
    key = credential_ref(account, workspace_id) if account in ACCOUNT_ENV else ""
    if not key:
        return {"status": "error", "error": True,
                "error_code": "invalid_contract",
                "message": "unknown Google account"}
    result = save_env_var(key, "")
    if result.get("status") == "success":
        clear_account_cache(account, workspace_id)
    return result

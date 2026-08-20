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

SCOPE_MAP = {
    "drive": [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.file",  # sync produced documents; per-file only
    ],
    "gmail": ["https://www.googleapis.com/auth/gmail.readonly"],
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


def _refresh_token(account: str = "founder") -> str:
    token = os.environ.get(ACCOUNT_ENV.get(account, ""), "")
    if token:
        return token
    try:
        from services import secrets

        return secrets.get(ACCOUNT_ENV.get(account, ""))
    except Exception:
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

        scopes = [s for conn, group in SCOPE_MAP.items()
                  if CONNECTOR_ACCOUNT.get(conn, "founder") == account for s in group]
        creds = google.oauth2.credentials.Credentials(
            token=None,
            refresh_token=_refresh_token(account),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            scopes=scopes,
        )
        creds.refresh(google.auth.transport.requests.Request())
        _creds[account] = creds
    return creds


def reset_for_tests() -> None:
    global _account_email
    _creds.clear()
    _granted.clear()
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


def save_env_var(key: str, value: str) -> None:
    """Persist a key to the process env (live immediately) and .env (next
    process). Empty value removes the key. Prod: Secret Manager (docs/12)."""
    os.environ.pop(key, None)
    if value:
        os.environ[key] = value
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        lines = []
        if os.path.exists(env_path):
            with open(env_path) as fh:
                lines = [l for l in fh.read().splitlines() if not l.startswith(f"{key}=")]
        if value:
            lines.append(f"{key}={value}")
        with open(env_path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass  # env var is already set for this process; .env write is best-effort


def save_refresh_token(token: str, account: str = "founder") -> None:
    """Persist a newly-consented refresh token — no restart needed (cached
    creds and account identity are reset)."""
    save_env_var(ACCOUNT_ENV.get(account, "GOOGLE_OAUTH_REFRESH_TOKEN"), token)
    reset_for_tests()

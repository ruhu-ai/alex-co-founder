"""Founder-scoped Google OAuth (docs/12): read-only Drive + Gmail.

The refresh token is obtained once via scripts/oauth_setup.py and stored in
Secret Manager (prod) or .env (local dev). Access tokens are minted at
execution time, held only in memory, and never enter session state, prompts,
or logs. Scopes are read-only by construction — the agent cannot send mail,
delete, or modify anything in the founder's Google account.

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
    "calendar": ["https://www.googleapis.com/auth/calendar.readonly"],
}
# Full grant (union) — used by scripts/oauth_setup.py and as the callback flow's
# scope set. Per-connector Connect buttons request only their own scopes;
# Google merges them into one grant (incremental authorization).
# calendar.events (booking) ships only with approval-gated invites — docs/adr/002.
SCOPES = [s for group in SCOPE_MAP.values() for s in group]

_creds = None
_granted = None  # frozenset of scopes the current token actually carries


def _refresh_token() -> str:
    token = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")
    if token:
        return token
    try:
        from services import secrets

        return secrets.get("GOOGLE_OAUTH_REFRESH_TOKEN")
    except Exception:
        return ""


def configured(connector: str | None = None) -> bool:
    """OAuth ready? With `connector`, True only when that connector's scopes
    were actually granted (per-connector Connect buttons, incremental auth)."""
    if not (
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        and _refresh_token()
    ):
        return False
    if connector is None:
        return True
    return set(SCOPE_MAP.get(connector, ())) <= set(granted_scopes())


def granted_scopes() -> frozenset:
    """Scopes the stored token really carries (tokeninfo), cached per token."""
    global _granted
    if _granted is not None:
        return _granted
    creds = get_credentials()
    if creds is None or not creds.token:
        return frozenset()
    try:
        from googleapiclient.discovery import build

        info = build("oauth2", "v2", credentials=creds,
                     cache_discovery=False).tokeninfo(access_token=creds.token).execute()
        _granted = frozenset((info.get("scope") or "").split())
    except Exception:
        return frozenset()
    return _granted


def get_credentials():
    """User credentials minted from the stored refresh token, or None when
    OAuth is not configured."""
    global _creds
    if not configured():
        return None
    if _creds is None or not _creds.valid:
        import google.auth.transport.requests
        import google.oauth2.credentials

        _creds = google.oauth2.credentials.Credentials(
            token=None,
            refresh_token=_refresh_token(),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            scopes=SCOPES,
        )
        _creds.refresh(google.auth.transport.requests.Request())
    return _creds


def reset_for_tests() -> None:
    global _creds, _granted
    _creds = None
    _granted = None


_account_email = ""


def account_email() -> str:
    """Email of the connected Google account (for the Connections panel).
    Cached; empty string when unconfigured. Gmail profile is the cheapest
    endpoint our read-only scopes can call."""
    global _account_email
    if _account_email:
        return _account_email
    creds = get_credentials()
    if creds is None:
        return ""
    from googleapiclient.discovery import build

    try:  # gmail scope granted?
        prof = build("gmail", "v1", credentials=creds,
                     cache_discovery=False).users().getProfile(userId="me").execute()
        _account_email = prof.get("emailAddress", "")
    except Exception:
        try:  # fall back to Drive (works with drive scopes only)
            about = build("drive", "v3", credentials=creds,
                          cache_discovery=False).about().get(fields="user").execute()
            _account_email = about.get("user", {}).get("emailAddress", "")
        except Exception:
            return ""
    return _account_email


def save_refresh_token(token: str) -> None:
    """Persist a newly-consented refresh token: env now (so no restart is
    needed — the cached creds are reset) and .env for the next process.
    Prod: store via Secret Manager instead (docs/13)."""
    global _account_email
    os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"] = token
    reset_for_tests()
    _account_email = ""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        lines = []
        if os.path.exists(env_path):
            with open(env_path) as fh:
                lines = [l for l in fh.read().splitlines()
                         if not l.startswith("GOOGLE_OAUTH_REFRESH_TOKEN=")]
        lines.append(f"GOOGLE_OAUTH_REFRESH_TOKEN={token}")
        with open(env_path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass  # env var is already set for this process; .env write is best-effort

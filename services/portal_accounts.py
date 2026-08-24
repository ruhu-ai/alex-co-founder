"""Per-portal credential lifecycle (docs/17): generate → store → fetch by host.

Storage: Google Secret Manager when GOOGLE_CLOUD_PROJECT is set (secret id
`portal-cred-{host}`), else a local dev file (.portal_secrets.json, gitignored).
Passwords never enter session state, prompts, logs, or artifacts (docs/12) —
only metadata (email, created_at) is ever exposed. Errors as data.
"""

from __future__ import annotations

import json
import os
import secrets as _secrets
import string
from datetime import datetime, timezone

_CACHE: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _err(message: str) -> dict:
    return {"status": "error", "error": True, "message": message}


def _register_secret(value: str) -> None:
    """Register a portal password with the log scrubber (docs/12). Fail-safe."""
    try:
        from services import log_scrub

        log_scrub.register_secret(value)
    except Exception:
        pass


def _local_path() -> str:
    return os.environ.get("PORTAL_SECRETS_FILE", ".portal_secrets.json")


def _load_local() -> dict:
    path = _local_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_local(data: dict) -> None:
    with open(_local_path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def _sm_project() -> str:
    return os.environ.get("GOOGLE_CLOUD_PROJECT", "")


def generate_password(length: int = 24) -> str:
    """Strong password: guaranteed lower, upper, digit, and symbol."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pw = "".join(_secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "!@#$%^&*" for c in pw)):
            return pw


def _secret_id(host: str) -> str:
    """Secret Manager IDs allow only [A-Za-z0-9_-] — hostnames contain dots
    (and sometimes ports), so 'portal-cred-{host}' verbatim ALWAYS failed in
    prod: create_secret raised InvalidArgument (swallowed as 'already
    exists'), add_secret_version then failed NotFound, and the password lived
    only in the in-memory cache until the instance recycled. Sanitize the id;
    the real host stays inside the JSON payload."""
    import re

    return "portal-cred-" + re.sub(r"[^A-Za-z0-9_-]", "-", host)


def store_credential(host: str, email: str, password: str,
                     verified: bool = True, portal_url: str = "",
                     session_id: str = "") -> dict:
    """Persist credentials for a portal host (e.g. 'flagship.aplica.500.co')."""
    entry = {"email": email, "password": password, "created_at": _now(),
             "verified": verified, "portal_url": portal_url,
             "session_id": session_id, "host": host}
    _register_secret(password)
    _CACHE[host] = entry
    if _sm_project():
        try:
            from google.api_core import exceptions as gexc
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            parent = f"projects/{_sm_project()}"
            secret_id = _secret_id(host)
            try:
                client.create_secret(request={
                    "parent": parent, "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}}})
            except gexc.AlreadyExists:
                pass  # only "already exists" is benign — anything else surfaces
            client.add_secret_version(request={
                "parent": f"{parent}/secrets/{secret_id}",
                "payload": {"data": json.dumps(entry).encode()}})
            return {"status": "success", "store": "secret_manager"}
        except Exception as exc:
            return _err(f"secret store failed: {exc}"[:200])
    data = _load_local()
    data[host] = entry
    _save_local(data)
    return {"status": "success", "store": "local"}


def get_credential(host: str) -> dict | None:
    """Fetch credentials by host. None when the host was never registered.

    A "secret not found" (NotFound) legitimately means no account exists and
    falls through to the local dev file / None. Any OTHER Secret Manager error
    (transient outage, permission, quota) PROPAGATES — a caller must never read
    a Secret Manager hiccup as "no account exists" and create a duplicate
    account on the real portal."""
    if host in _CACHE:
        return _CACHE[host]
    if _sm_project():
        from google.api_core import exceptions as gexc
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = (f"projects/{_sm_project()}/secrets/{_secret_id(host)}"
                "/versions/latest")
        try:
            entry = json.loads(
                client.access_secret_version(request={"name": name})
                .payload.data.decode())
        except gexc.NotFound:
            entry = None  # absent in Secret Manager → try local dev file / None
        else:
            _register_secret(entry.get("password", ""))
            _CACHE[host] = entry
            return entry
    return _load_local().get(host)


def list_portals() -> dict[str, dict]:
    """Metadata only — never passwords. 'Where does Alex have accounts?'"""
    merged = {**_load_local(), **_CACHE}
    return {host: {"email": v.get("email", ""), "created_at": v.get("created_at", ""),
                   "verified": v.get("verified", True)}
            for host, v in merged.items()}


def pending_registrations() -> list[dict]:
    """Safe routing metadata for event-driven verification; never passwords."""
    merged = {**_load_local(), **_CACHE}
    return [
        {"host": host, "email": value.get("email", ""),
         "portal_url": value.get("portal_url", ""),
         "session_id": value.get("session_id", "")}
        for host, value in merged.items()
        if value.get("verified") is False
    ]


def mark_verified(host: str) -> dict:
    """Persist successful email verification without exposing the password."""
    credential = get_credential(host)
    if not credential:
        return _err(f"no stored credential for {host}")
    return store_credential(
        host, credential["email"], credential["password"], verified=True,
        portal_url=credential.get("portal_url", ""),
        session_id=credential.get("session_id", ""))


def delete_credential(host: str) -> dict:
    _CACHE.pop(host, None)
    data = _load_local()
    if host in data:
        del data[host]
        _save_local(data)
    return {"status": "success"}

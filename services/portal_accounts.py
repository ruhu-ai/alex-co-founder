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


def store_credential(host: str, email: str, password: str) -> dict:
    """Persist credentials for a portal host (e.g. 'flagship.aplica.500.co')."""
    entry = {"email": email, "password": password, "created_at": _now()}
    _CACHE[host] = entry
    if _sm_project():
        try:
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            parent = f"projects/{_sm_project()}"
            secret_id = f"portal-cred-{host}"
            try:
                client.create_secret(request={
                    "parent": parent, "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}}})
            except Exception:
                pass  # already exists
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
    """Fetch credentials by host. None when the host was never registered."""
    if host in _CACHE:
        return _CACHE[host]
    if _sm_project():
        try:
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            name = (f"projects/{_sm_project()}/secrets/portal-cred-{host}"
                    "/versions/latest")
            entry = json.loads(
                client.access_secret_version(request={"name": name})
                .payload.data.decode())
            _CACHE[host] = entry
            return entry
        except Exception:
            pass
    return _load_local().get(host)


def list_portals() -> dict[str, dict]:
    """Metadata only — never passwords. 'Where does Alex have accounts?'"""
    merged = {**_load_local(), **_CACHE}
    return {host: {"email": v.get("email", ""), "created_at": v.get("created_at", "")}
            for host, v in merged.items()}


def delete_credential(host: str) -> dict:
    _CACHE.pop(host, None)
    data = _load_local()
    if host in data:
        del data[host]
        _save_local(data)
    return {"status": "success"}

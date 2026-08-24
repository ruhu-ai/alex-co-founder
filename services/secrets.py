"""Secret Manager access (docs/12).

Secrets are fetched BY NAME via the API at execution time, cached in memory for
at most 5 minutes, and never appear in prompts, session state, or logs.
"""

from __future__ import annotations

import os
import time

_cache: dict[str, tuple[float, str]] = {}
_TTL_SECONDS = 300


def _register(value: str) -> None:
    """Hand a fetched secret to the log scrubber (docs/12). Fail-safe."""
    try:
        from services import log_scrub

        log_scrub.register_secret(value)
    except Exception:
        pass


def get(name: str) -> str:
    """Fetch a secret's value by name (env PORTAL_SECRET_NAME-style ids)."""
    now = time.time()
    if name in _cache and now - _cache[name][0] < _TTL_SECONDS:
        return _cache[name][1]
    from google.cloud import secretmanager

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    path = f"projects/{project}/secrets/{name}/versions/latest"
    value = secretmanager.SecretManagerServiceClient().access_secret_version(
        request={"name": path}
    ).payload.data.decode("utf-8")
    _cache[name] = (now, value)
    _register(value)
    return value


def put(name: str, value: str) -> None:
    """Persist a secret value by name to Secret Manager (prod token storage).

    Creates the secret on first write. No-op when no project is configured.
    Never logs the value; registers it with the scrubber."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for secret persistence")
    if not value:
        raise ValueError("secret value must be non-empty; use delete() to disconnect")
    from google.api_core import exceptions as gexc
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{project}"
    try:
        client.create_secret(request={
            "parent": parent, "secret_id": name,
            "secret": {"replication": {"automatic": {}}}})
    except gexc.AlreadyExists:
        pass
    client.add_secret_version(request={
        "parent": f"{parent}/secrets/{name}",
        "payload": {"data": value.encode("utf-8")}})
    _cache[name] = (time.time(), value)
    _register(value)


def delete(name: str) -> None:
    """Delete a runtime-managed secret and evict its cached value.

    Connector tokens are intentionally not Cloud Run environment bindings; the
    application fetches them by name at execution time.  Removing the Secret
    Manager resource therefore makes a disconnect survive every cold start.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for secret deletion")
    from google.api_core import exceptions as gexc
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    path = f"projects/{project}/secrets/{name}"
    try:
        client.delete_secret(request={"name": path})
    except gexc.NotFound:
        pass
    _cache.pop(name, None)

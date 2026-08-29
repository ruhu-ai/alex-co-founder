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

    Adds a version to a pre-provisioned secret first.  This lets a runtime
    identity hold narrowly scoped ``secretVersionManager`` on an exact
    connector slot without project-wide secret-creation authority.  Local
    setup callers that do have create authority still get first-write setup.
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
    secret_path = f"{parent}/secrets/{name}"
    request = {
        "parent": secret_path,
        "payload": {"data": value.encode("utf-8")},
    }
    try:
        client.add_secret_version(request=request)
    except gexc.NotFound:
        try:
            client.create_secret(request={
                "parent": parent, "secret_id": name,
                "secret": {"replication": {"automatic": {}}}})
        except gexc.AlreadyExists:
            # Another setup request may have won the create race.
            pass
        client.add_secret_version(request=request)
    _cache[name] = (time.time(), value)
    _register(value)


def delete(name: str) -> None:
    """Destroy every enabled version and evict the cached value.

    Connector token containers are pre-provisioned with exact secret-level IAM.
    Keeping that empty container preserves least-privilege reconnect support;
    destroying every enabled version still makes reads fail closed after every
    cold start and leaves no usable refresh token behind.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for secret deletion")
    from google.api_core import exceptions as gexc
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    path = f"projects/{project}/secrets/{name}"
    try:
        versions = client.list_secret_versions(request={
            "parent": path,
            "filter": "state:ENABLED",
        })
        for version in versions:
            client.destroy_secret_version(request={"name": version.name})
    except gexc.NotFound:
        pass
    _cache.pop(name, None)

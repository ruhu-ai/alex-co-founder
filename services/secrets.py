"""Secret Manager access (docs/12).

Secrets are fetched BY NAME via the API at execution time, cached in memory for
at most 5 minutes, and never appear in prompts, session state, or logs.
"""

from __future__ import annotations

import os
import time

_cache: dict[str, tuple[float, str]] = {}
_TTL_SECONDS = 300


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
    return value

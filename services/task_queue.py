"""Durable Cloud Tasks enqueueing for work that must outlive a request."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone

QUEUE_IDENTITY_ENV = {
    "co-founder-provider-events": "TASKS_PROVIDER_EVENTS_SA",
    "co-founder-discovery-ingestion": "TASKS_DISCOVERY_INGESTION_SA",
    "co-founder-timers": "TASKS_TIMERS_SA",
    "co-founder-browser-expiry": "TASKS_BROWSER_SA",
    "co-founder-reconciliation": "TASKS_RECONCILIATION_SA",
    "co-founder-interactive": "TASKS_INTERACTIVE_SA",
}


def enqueue(
    path: str,
    payload: dict,
    dedupe_key: str,
    *,
    queue_name: str = "co-founder-events",
    schedule_at: str | None = None,
    audience: str | None = None,
) -> dict:
    """Create one authenticated HTTP task; errors are returned as data."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    region = os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")
    base_url = os.environ.get("AGENT_BASE_URL", "").rstrip("/")
    identity_env = QUEUE_IDENTITY_ENV.get(queue_name, "TASKS_INVOKER_SA")
    service_account = (os.environ.get(identity_env, "")
                       or os.environ.get("TASKS_INVOKER_SA", ""))
    if not all((project, region, base_url, service_account)):
        return {"status": "error", "error": True,
                "message": "Cloud Tasks is not fully configured"}
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        session = AuthorizedSession(credentials)
        parent = f"projects/{project}/locations/{region}/queues/{queue_name}"
        task_id = hashlib.sha256(dedupe_key.encode()).hexdigest()[:32]
        body = base64.b64encode(json.dumps(payload).encode()).decode()
        task = {
            "name": f"{parent}/tasks/{task_id}",
            "httpRequest": {
                "httpMethod": "POST",
                "url": f"{base_url}{path}",
                "headers": {"Content-Type": "application/json"},
                "body": body,
                "oidcToken": {"serviceAccountEmail": service_account,
                              "audience": audience or base_url},
            },
        }
        if schedule_at:
            scheduled = datetime.fromisoformat(schedule_at.replace("Z", "+00:00"))
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            task["scheduleTime"] = scheduled.astimezone(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
        response = session.post(
            f"https://cloudtasks.googleapis.com/v2/{parent}/tasks",
            timeout=20,
            json={"task": task})
        if response.status_code == 409:
            return {"status": "success", "duplicate": True}
        response.raise_for_status()
        return {"status": "success"}
    except Exception as exc:
        return {"status": "error", "error": True,
                "message": f"task enqueue failed: {exc}"[:200]}


def enqueue_hiring(path: str, payload: dict, dedupe_key: str, *,
                   schedule_at: str | None = None) -> dict:
    """Enqueue a hiring delivery with the exact route as OIDC audience."""
    if not path.startswith("/tasks/hiring/") or "?" in path or "#" in path:
        return {"status": "error", "error": True,
                "error_code": "invalid_contract",
                "message": "Invalid hiring worker route."}
    base_url = os.environ.get("AGENT_BASE_URL", "").rstrip("/")
    if not base_url:
        return {"status": "error", "error": True,
                "message": "Cloud Tasks is not fully configured"}
    return enqueue(path, payload, dedupe_key, queue_name="co-founder-events",
                   schedule_at=schedule_at, audience=f"{base_url}{path}")

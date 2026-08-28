"""Durable Cloud Tasks enqueueing for work that must outlive a request."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

QUEUE_IDENTITY_ENV = {
    "co-founder-provider-events": "TASKS_PROVIDER_EVENTS_SA",
    "co-founder-discovery-ingestion": "TASKS_DISCOVERY_INGESTION_SA",
    "co-founder-timers": "TASKS_TIMERS_SA",
    "co-founder-browser-expiry": "TASKS_BROWSER_SA",
    "co-founder-reconciliation": "TASKS_RECONCILIATION_SA",
    "co-founder-interactive": "TASKS_INTERACTIVE_SA",
    "co-founder-background-pilot": "TASKS_BACKGROUND_PILOT_SA",
    "co-founder-background-pilot-real": "TASKS_BACKGROUND_PILOT_SA",
    "co-founder-background-pilot-gate-e": "TASKS_BACKGROUND_PILOT_SA",
}
LOGGER = logging.getLogger("background_pilot.local_dispatch")


def _enqueue_local_background_pilot(
        path: str, payload: dict, dedupe_key: str, *,
        queue_name: str, audience: str | None,
        schedule_at: str | None) -> dict | None:
    """Deliver the one pilot route asynchronously on loopback in local tests.

    This adapter cannot run in Cloud Run, cannot target a non-loopback host,
    and cannot dispatch another queue or path. Production always uses the
    authenticated Cloud Tasks branch below.
    """
    if (os.environ.get("K_SERVICE")
            or os.environ.get("BACKGROUND_PILOT_ALLOW_TEST_DISPATCH") != "1"):
        return None
    if (queue_name != "co-founder-background-pilot"
            or path != "/tasks/background-artifact-pilot" or schedule_at):
        return None
    base_url = os.environ.get("AGENT_BASE_URL", "").rstrip("/")
    parsed = urllib.parse.urlparse(base_url)
    if (parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.path or parsed.query or parsed.fragment):
        return {"status": "error", "error": True,
                "error_code": "local_dispatch_target_denied",
                "message": "Pilot test delivery requires an exact loopback origin."}
    expected_audience = f"{base_url}{path}"
    if audience != expected_audience:
        return {"status": "error", "error": True,
                "error_code": "local_dispatch_audience_invalid",
                "message": "Pilot test delivery audience is invalid."}
    secret = os.environ.get("BACKGROUND_PILOT_TEST_DISPATCH_SECRET", "")
    if len(secret) < 32:
        return {"status": "error", "error": True,
                "error_code": "local_dispatch_secret_invalid",
                "message": "Pilot test delivery secret is not configured."}
    delivery_id = hashlib.sha256(dedupe_key.encode()).hexdigest()[:32]
    claims = {
        "principal_kind": "CLOUD_TASKS",
        "service_account": "local-background-pilot-worker",
        "issuer": "local-test-dispatcher", "audience": expected_audience,
        "delivery_id": delivery_id, "exp": int(time.time()) + 120,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = hmac.new(
        secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    body = json.dumps(payload, separators=(",", ":")).encode()
    try:
        initial_delay = max(0, min(500, int(os.environ.get(
            "BACKGROUND_PILOT_LOCAL_DISPATCH_DELAY_MS", "0")))) / 1000
    except ValueError:
        initial_delay = 0

    def deliver() -> None:
        if initial_delay:
            time.sleep(initial_delay)
        for attempt in range(1, 4):
            request = urllib.request.Request(
                expected_audience, data=body, method="POST", headers={
                    "Content-Type": "application/json",
                    "X-Background-Pilot-Test-Principal": encoded,
                    "X-Background-Pilot-Test-Signature": signature,
                })
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    if 200 <= response.status < 300:
                        return
            except (urllib.error.URLError, TimeoutError):
                pass
            if attempt < 3:
                time.sleep(0.25 * attempt)
        LOGGER.error(json.dumps({
            "metric": "background_pilot_local_delivery_exhausted",
            "status": "failed", "delivery_id": delivery_id,
        }, sort_keys=True))

    threading.Thread(
        target=deliver, name=f"background-pilot-{delivery_id[:8]}",
        daemon=True).start()
    return {"status": "success", "local_test_delivery": True}


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
    local = _enqueue_local_background_pilot(
        path, payload, dedupe_key, queue_name=queue_name,
        audience=audience, schedule_at=schedule_at)
    if local is not None:
        return local
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
        # The model-backed discovery/ingestion lease is 900 seconds. Finish or
        # cancel the delivery before that lease can be reclaimed, preventing a
        # second worker from replaying the same expensive operation.
        task["dispatchDeadline"] = (
            "840s" if queue_name == "co-founder-discovery-ingestion" else "300s")
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

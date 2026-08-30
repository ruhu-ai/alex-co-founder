"""Route-scoped workload identity verification for hiring workers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkloadPrincipal:
    principal_kind: str
    service_account: str
    issuer: str
    audience: str
    delivery_id: str

    def audit_fields(self) -> dict[str, str]:
        return {
            "workload_kind": self.principal_kind,
            "workload_service_account": self.service_account,
            "workload_audience": self.audience,
            "delivery_id": self.delivery_id,
        }


def _error(code: str = "workload_unauthorized") -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": "Internal delivery is not authorized.", "http_status": 401}


def _allowlist(path: str) -> set[str]:
    variable = {
        "/tasks/background-artifact-pilot":
            "BACKGROUND_PILOT_WORKLOAD_ALLOWLIST_JSON",
        "/tasks/background-artifact-grounded-brief":
            "BACKGROUND_SKILL_WORKLOAD_ALLOWLIST_JSON",
    }.get(path, "HIRING_WORKLOAD_ALLOWLIST_JSON")
    try:
        configured = json.loads(os.environ.get(variable, "{}"))
    except ValueError:
        return set()
    values = configured.get(path, []) if isinstance(configured, dict) else []
    return {str(value) for value in values if value}


def _test_principal(request, audience: str,
                    route_path: str) -> WorkloadPrincipal | dict[str, Any]:
    pilot = route_path in {
        "/tasks/background-artifact-pilot",
        "/tasks/background-artifact-grounded-brief",
    }
    enabled_name = (
        "BACKGROUND_PILOT_ALLOW_TEST_DISPATCH" if pilot
        else "HIRING_ALLOW_TEST_DISPATCH")
    secret_name = (
        "BACKGROUND_PILOT_TEST_DISPATCH_SECRET" if pilot
        else "HIRING_TEST_DISPATCH_SECRET")
    if os.environ.get("K_SERVICE") or os.environ.get(enabled_name) != "1":
        return _error()
    secret = os.environ.get(secret_name, "")
    encoded = request.headers.get(
        "X-Background-Pilot-Test-Principal" if pilot
        else "X-Hiring-Test-Principal", "")
    signature = request.headers.get(
        "X-Background-Pilot-Test-Signature" if pilot
        else "X-Hiring-Test-Signature", "")
    if not secret or not encoded or not hmac.compare_digest(
            hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest(), signature):
        return _error()
    try:
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (ValueError, UnicodeDecodeError):
        return _error()
    if claims.get("audience") != audience or claims.get("issuer") != "local-test-dispatcher":
        return _error()
    try:
        expires_at = int(claims.get("exp"))
    except (TypeError, ValueError):
        return _error()
    now = int(time.time())
    if expires_at <= now or expires_at > now + 600:
        return _error()
    if (claims.get("principal_kind") not in {"CLOUD_TASKS", "PUBSUB", "INTERNAL_SERVICE"}
            or not claims.get("service_account") or not claims.get("delivery_id")):
        return _error()
    expected_test_account = {
        "/tasks/background-artifact-pilot": "local-background-pilot-worker",
        "/tasks/background-artifact-grounded-brief":
            "local-background-skill-worker",
        "/tasks/hiring/prepare_candidate_evidence":
            "local-hiring-evidence-worker",
    }.get(route_path)
    if expected_test_account and claims.get("service_account") != expected_test_account:
        return _error()
    return WorkloadPrincipal(
        principal_kind=str(claims.get("principal_kind") or ""),
        service_account=str(claims.get("service_account") or ""),
        issuer="local-test-dispatcher", audience=audience,
        delivery_id=str(claims.get("delivery_id") or ""),
    )


def _route_audience(request, route_path: str) -> str:
    """Build the audience the dispatcher signed, never the observed scheme.

    Behind the Cloud Run front end ``request.url.scheme`` can be plain http, so
    deriving the audience from it would never match the https audience
    ``task_queue.enqueue_hiring`` puts in the OIDC token. AGENT_BASE_URL is the
    one canonical origin both sides already agree on.
    """
    # Local signed-dispatch tests bind to the actual test server. They must not
    # inherit a deployment origin left in the process by another test or local
    # launcher. Production alone uses the configured canonical HTTPS origin.
    if not os.environ.get("K_SERVICE"):
        base = f"{request.url.scheme}://{request.url.netloc}"
    else:
        base = (os.environ.get("AGENT_BASE_URL", "").rstrip("/")
                or f"https://{request.url.hostname}")
    return f"{base}{route_path}"


async def verify_request(request, route_path: str) -> WorkloadPrincipal | dict[str, Any]:
    """Verify issuer, exact route audience, expiry and route service-account pin."""
    audience = _route_audience(request, route_path)
    if not os.environ.get("K_SERVICE"):
        return _test_principal(request, audience, route_path)
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return _error()
    allowed = _allowlist(route_path)
    if not allowed:
        return _error()
    try:
        import asyncio

        from google.auth.transport import requests as auth_requests
        from google.oauth2 import id_token

        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token, header.removeprefix("Bearer "),
            auth_requests.Request(), audience=audience)
        issuer = str(claims.get("iss") or "")
        service_account = str(claims.get("email") or "")
        if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
            return _error()
        if claims.get("email_verified") is not True or service_account not in allowed:
            return _error()
        delivery_id = (request.headers.get("X-CloudTasks-TaskName")
                       or request.headers.get("X-Goog-Message-Number") or "")
        return WorkloadPrincipal(
            principal_kind=("CLOUD_TASKS" if request.headers.get("X-CloudTasks-TaskName")
                            else "PUBSUB"),
            service_account=service_account, issuer=issuer, audience=audience,
            delivery_id=delivery_id[:512])
    except Exception:
        return _error()

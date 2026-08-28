"""Private exact-artifact host for one Founder-authorized Spec 40 pilot.

Unlike the synthetic canary host, this service cannot seed, alter, or delete
source data and exposes no failure-injection controls. Deployment configuration
binds it to one existing Founder membership, session, workspace, and immutable
artifact. The only executable capability is the reviewed content-free evidence
inventory; there is no agent runner, model, browser, connector, approval,
effect, memory, conversation delivery, or generic background route.
"""

from __future__ import annotations

import hmac
import os
import re
from typing import Any

from fastapi import FastAPI, Request

from app import background_pilot_routes
from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_store import production_store

FOUNDER_KEY_ENV = "SPEC40_REAL_PILOT_FOUNDER_KEY"
FOUNDER_HEADER = "X-Spec40-Real-Pilot-Key"
WORKSPACE_ENV = "SPEC40_REAL_PILOT_WORKSPACE_ID"
ACTOR_ENV = "SPEC40_REAL_PILOT_ACTOR_ID"
SESSION_ENV = "SPEC40_REAL_PILOT_SESSION_ID"
ARTIFACT_ENV = "SPEC40_REAL_PILOT_ARTIFACT_ID"

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")
_ARTIFACT_ID = re.compile(r"^[a-f0-9]{32}$")


def _binding() -> dict[str, str]:
    values = {
        "workspace_id": os.environ.get(WORKSPACE_ENV, ""),
        "actor_id": os.environ.get(ACTOR_ENV, ""),
        "session_id": os.environ.get(SESSION_ENV, ""),
        "artifact_id": os.environ.get(ARTIFACT_ENV, ""),
    }
    if (not _OPAQUE_ID.fullmatch(values["workspace_id"])
            or not _OPAQUE_ID.fullmatch(values["actor_id"])
            or not _OPAQUE_ID.fullmatch(values["session_id"])
            or not _ARTIFACT_ID.fullmatch(values["artifact_id"])):
        return {}
    return values


def _exact_configuration() -> dict[str, bool]:
    binding = _binding()
    return {
        "cloud_run": bool(os.environ.get("K_SERVICE")),
        "binding_complete": bool(binding),
        "admission_enabled": (
            os.environ.get("BACKGROUND_JOB_ADMISSION_ENABLED") == "true"
            and os.environ.get("BACKGROUND_ARTIFACT_PILOT_ENABLED") == "true"),
        "execution_enabled": (
            os.environ.get("BACKGROUND_SPECIALIST_EXECUTION_ENABLED") == "true"
            and os.environ.get("BACKGROUND_ARTIFACT_PILOT_EXECUTION_ENABLED")
            == "true"),
        "kill_switch_clear": (
            os.environ.get("BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH") == "false"),
        "single_workspace": bool(binding) and (
            os.environ.get("BACKGROUND_ARTIFACT_PILOT_WORKSPACES")
            == binding["workspace_id"]),
        "conversation_delivery_off": (
            os.environ.get("BACKGROUND_CONVERSATION_DELIVERY_ENABLED", "false")
            == "false"),
        "local_dispatch_off": (
            os.environ.get("BACKGROUND_PILOT_ALLOW_TEST_DISPATCH", "0") != "1"),
        "exact_queue": (
            os.environ.get("BACKGROUND_ARTIFACT_PILOT_QUEUE")
            == "co-founder-background-pilot-real"),
    }


def _key_valid(request: Request) -> bool:
    expected = os.environ.get(FOUNDER_KEY_ENV, "").strip()
    presented = request.headers.get(FOUNDER_HEADER, "").strip()
    return (len(expected) >= 32 and len(presented) >= 32
            and hmac.compare_digest(expected, presented))


async def _principal(request: Request) -> ActorPrincipal | dict[str, Any]:
    binding = _binding()
    if not binding or not _key_valid(request):
        return {"status": "error", "error": True,
                "error_code": "interactive_founder_required",
                "message": "Exact real-pilot Founder authorization is required."}
    members = await production_store().list("workspace_members", filters={
        "workspace_id": binding["workspace_id"],
        "actor_id": binding["actor_id"], "role": "FOUNDER",
        "status": "ACTIVE", "synthetic": False,
    }, limit=2)
    if len(members) != 1:
        return {"status": "error", "error": True,
                "error_code": "membership_missing",
                "message": "The bound real Founder membership is missing."}
    member = members[0]
    return ActorPrincipal(
        actor_id=binding["actor_id"], workspace_id=binding["workspace_id"],
        role=WorkspaceRole.FOUNDER, role_grants=frozenset(),
        candidate_assignments=frozenset(), interview_assignments=frozenset(),
        session_auth_time=1, membership_version=int(member["version"]),
        principal_kind="INTERACTIVE", membership_id=str(member["membership_id"]))


async def _session(workspace_id: str, session_id: str) -> bool:
    binding = _binding()
    return bool(binding and workspace_id == binding["workspace_id"]
                and session_id == binding["session_id"])


async def _artifact(principal: ActorPrincipal, session_id: str,
                    artifact_id: str) -> bool:
    binding = _binding()
    return bool(binding
                and principal.workspace_id == binding["workspace_id"]
                and principal.actor_id == binding["actor_id"]
                and session_id == binding["session_id"]
                and artifact_id == binding["artifact_id"])


app = FastAPI(
    title="Spec 40 private exact-artifact pilot",
    docs_url=None, redoc_url=None, openapi_url=None,
)
background_pilot_routes.register(
    app, principal_resolver=_principal, session_resolver=_session,
    store_factory=production_store, artifact_authorizer=_artifact)


@app.get("/health")
async def health():
    binding = _binding()
    return {
        "status": "ok" if all(_exact_configuration().values()) else "blocked",
        "template_id": "pilot.artifact_evidence_inventory@1",
        "binding": {
            "workspace_id": binding.get("workspace_id", ""),
            "session_id": binding.get("session_id", ""),
            "artifact_id": binding.get("artifact_id", ""),
        },
        "flags": _exact_configuration(),
    }

"""Compatibility adapters that shadow current domains onto the generic runtime."""

from __future__ import annotations

import os
from typing import Any

from services.durable_store import DurableStore, production_store
from services.workflow_contracts import RunKind, stable_id
from services.workflow_runtime import WorkflowRuntime


def shadow_enabled() -> bool:
    return os.environ.get("WORKFLOW_RUNTIME_SHADOW", "0").lower() in {
        "1", "true", "yes", "on"}


class WorkflowProjectionService:
    """Create only control-plane shadows; domain records remain authoritative.

    These adapters never call a provider or advance a domain state. A failed
    shadow is observable and retryable while the existing domain flow remains
    the compatibility reader during Phase 1.
    """

    def __init__(self, store: DurableStore | None = None):
        self.store = store or production_store()
        self.runtime = WorkflowRuntime(self.store)

    async def ensure_discovery(self, *, workspace_id: str,
                               discovery_request_id: str,
                               originating_actor_id: str = "") -> dict[str, Any]:
        journey_id = stable_id(
            "journey", workspace_id, "discovery", discovery_request_id)
        return await self.runtime.create_run(
            workspace_id=workspace_id, journey_id=journey_id,
            run_kind=RunKind.OPPORTUNITY_DISCOVERY,
            workflow_kind="opportunity_discovery:v1",
            idempotency_key=discovery_request_id,
            domain_ref=discovery_request_id,
            originating_actor_id=originating_actor_id)

    async def ensure_grant_application(
            self, *, workspace_id: str, application_id: str,
            originating_actor_id: str = "") -> dict[str, Any]:
        journey_id = stable_id(
            "journey", workspace_id, "grant", application_id)
        return await self.runtime.create_run(
            workspace_id=workspace_id, journey_id=journey_id,
            run_kind=RunKind.GRANT_APPLICATION,
            workflow_kind="grant_application:v1",
            idempotency_key=application_id, domain_ref=application_id,
            originating_actor_id=originating_actor_id)

    async def ensure_external_consequence(
            self, *, workspace_id: str, domain_ref: str,
            originating_actor_id: str = "") -> dict[str, Any]:
        """Create the deterministic control run for a standalone consequence."""
        journey_id = stable_id(
            "journey", workspace_id, "consequence", domain_ref)
        return await self.runtime.create_run(
            workspace_id=workspace_id, journey_id=journey_id,
            run_kind=RunKind.EXTERNAL_CONSEQUENCE,
            workflow_kind="external_consequence:v1",
            idempotency_key=domain_ref, domain_ref=domain_ref,
            originating_actor_id=originating_actor_id)

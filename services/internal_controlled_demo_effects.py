"""Idempotent executor for the closed internal-demo effect boundary.

The adapter is intentionally injectable. Production wiring may only supply an
adapter that re-verifies the pinned Alex/Founder account subjects; callers can
never supply OAuth credentials, addresses, text, or a provider selection.
"""

from __future__ import annotations

from typing import Any, Protocol

from services.actor_identity import ActorPrincipal
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import canonical_hash, stable_id, utc_now
from services.internal_controlled_demo import InternalControlledDemoService, build_exact_action


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "http_status": http_status}


class InternalDemoEffectAdapter(Protocol):
    """Narrow provider boundary: only exact closed actions reach it."""

    async def execute(self, *, exact_action: dict[str, Any], action_id: str) -> dict[str, Any]: ...

    async def reconcile(self, *, exact_action: dict[str, Any], action_id: str) -> dict[str, Any]: ...


class DisabledInternalDemoEffectAdapter:
    """Default until the separately reviewed Google adapter is installed."""

    async def execute(self, **_: Any) -> dict[str, Any]:
        return {"status": "failed", "error_code": "internal_demo_provider_not_configured"}

    async def reconcile(self, **_: Any) -> dict[str, Any]:
        return {"status": "failed", "error_code": "internal_demo_provider_not_configured"}


class InternalDemoEffectService:
    """Prepare, consume exact approval, execute once, and retain a receipt."""

    def __init__(self, *, store: DurableStore | None = None,
                 adapter: InternalDemoEffectAdapter | None = None):
        self.store = store or production_store()
        self.runs = InternalControlledDemoService(self.store)
        if adapter is None:
            from services.internal_controlled_demo_google import InternalDemoGoogleAdapter
            adapter = InternalDemoGoogleAdapter()
        self.adapter = adapter

    async def execute(self, *, principal: ActorPrincipal, demo_run_id: str,
                      approval_id: str, action_kind: str) -> dict[str, Any]:
        run = await self.runs._active_run(principal, demo_run_id)
        if run.get("error"):
            return run
        built = build_exact_action(
            action_kind, demo_run_id=demo_run_id,
            calendar_start_at=str(run["demo_run"].get("calendar_start_at") or ""))
        if built.get("error"):
            return built
        approval = await self.store.get("internal_demo_approvals", approval_id)
        if (not approval or approval.get("status") not in {"GRANTED", "CONSUMED"}
                or approval.get("workspace_id") != principal.workspace_id
                or approval.get("demo_run_id") != demo_run_id
                or approval.get("action_kind") != action_kind
                or approval.get("exact_action") != built["exact_action"]
                or approval.get("subject_hash") != built["subject_hash"]):
            return _error("approval_binding_mismatch",
                          "Approval does not cover the current exact action.")
        exact = approval["exact_action"]
        action_id = stable_id("idemoaction", principal.workspace_id, canonical_hash(exact))
        current = await self.store.get("internal_demo_actions", action_id)
        if current:
            if current.get("request_hash") != canonical_hash(exact):
                return _error("idempotency_conflict", "Action identity has drifted.")
            if current.get("status") == "SUCCEEDED":
                return {"status": "success", "duplicate": True, "action_id": action_id,
                        "receipt_status": "SUCCEEDED"}
            if current.get("provider_started_at"):
                return _error("reconciliation_required", "Provider outcome is not yet known.")
        if not current:
            row = {"schema_version": 1, "action_id": action_id,
                   "workspace_id": principal.workspace_id, "demo_run_id": demo_run_id,
                   "approval_id": approval_id, "action_kind": action_kind,
                   "exact_action": exact, "request_hash": canonical_hash(exact),
                   "status": "PREPARED", "approval_consumed": False,
                   "provider_started_at": None, "provider_effect_id": None,
                   "internal_demo": True, "fixture_id": run["demo_run"]["fixture_id"],
                   "created_at": utc_now(), "updated_at": utc_now(), "version": 1}
            await self.store.create("internal_demo_actions", action_id, row)
            current = await self.store.get("internal_demo_actions", action_id)
        if not current or current.get("approval_id") != approval_id:
            return _error("approval_binding_mismatch", "Approval does not name this exact action.")
        if not current.get("approval_consumed"):
            approval = await self.store.get("internal_demo_approvals", approval_id)
            if (not approval or approval.get("status") != "GRANTED"
                    or approval.get("demo_run_id") != demo_run_id
                    or approval.get("action_kind") != action_kind
                    or approval.get("exact_action") != exact
                    or approval.get("subject_hash") != built["subject_hash"]):
                return _error("approval_binding_mismatch", "Approval does not cover this exact action.")
            consumed = await self.store.compare_and_set(
                "internal_demo_approvals", approval_id, int(approval["version"]),
                {"status": "CONSUMED", "consumed_at": utc_now(), "updated_at": utc_now()})
            if not consumed:
                return _error("concurrency_conflict", "Approval changed concurrently.")
            current = await self.store.compare_and_set(
                "internal_demo_actions", action_id, int(current["version"]),
                {"approval_consumed": True, "updated_at": utc_now()})
            if not current:
                return _error("concurrency_conflict", "Action changed concurrently.")
        leased = await self.store.compare_and_set(
            "internal_demo_actions", action_id, int(current["version"]),
            {"provider_started_at": utc_now(), "updated_at": utc_now()})
        if not leased:
            return _error("concurrency_conflict", "Action changed concurrently.")
        result = await self.adapter.execute(exact_action=exact, action_id=action_id)
        provider_status = str(result.get("status") or "uncertain")
        status = {"success": "SUCCEEDED", "failed": "FAILED"}.get(provider_status, "UNCERTAIN")
        committed = await self.store.compare_and_set(
            "internal_demo_actions", action_id, int(leased["version"]),
            {"status": status, "provider_effect_id": result.get("provider_effect_id"),
             "result_ref": result.get("result_ref") if isinstance(result.get("result_ref"), dict) else {},
             "error_code": result.get("error_code"), "updated_at": utc_now(), "completed_at": utc_now()})
        if not committed:
            return _error("concurrency_conflict", "Action receipt changed concurrently.")
        if status == "SUCCEEDED":
            return {"status": "success", "duplicate": False, "action_id": action_id,
                    "receipt_status": status, "provider_effect_id": committed.get("provider_effect_id")}
        if status == "UNCERTAIN":
            return _error("reconciliation_required", "Provider outcome is uncertain; no automatic retry occurred.", 503)
        return _error(str(committed.get("error_code") or "provider_rejected"),
                      "The internal-demo provider rejected the action.")

    async def reconcile(self, *, principal: ActorPrincipal, demo_run_id: str,
                        action_id: str) -> dict[str, Any]:
        """Resolve a previously uncertain provider outcome; never retries it."""
        run = await self.runs._active_run(principal, demo_run_id)
        if run.get("error"):
            return run
        current = await self.store.get("internal_demo_actions", action_id)
        if (not current or current.get("workspace_id") != principal.workspace_id
                or current.get("demo_run_id") != demo_run_id):
            return _error("action_not_found", "Internal-demo action does not exist.", 404)
        if current.get("status") == "SUCCEEDED":
            return {"status": "success", "duplicate": True, "action_id": action_id,
                    "receipt_status": "SUCCEEDED"}
        if current.get("status") != "UNCERTAIN":
            return _error("reconciliation_not_required", "Action does not require reconciliation.")
        reconcile = getattr(self.adapter, "reconcile", None)
        if reconcile is None:
            return _error("reconciliation_unavailable", "Provider reconciliation is unavailable.", 503)
        result = await reconcile(exact_action=current["exact_action"], action_id=action_id)
        provider_status = str(result.get("status") or "uncertain")
        if provider_status == "uncertain":
            return _error("reconciliation_required", "Provider outcome remains uncertain; no retry occurred.", 503)
        status = "SUCCEEDED" if provider_status == "success" else "FAILED"
        committed = await self.store.compare_and_set(
            "internal_demo_actions", action_id, int(current["version"]), {
                "status": status, "provider_effect_id": result.get("provider_effect_id"),
                "result_ref": result.get("result_ref") if isinstance(result.get("result_ref"), dict) else {},
                "error_code": result.get("error_code"), "reconciled_at": utc_now(),
                "updated_at": utc_now(), "completed_at": utc_now(),
            })
        if not committed:
            return _error("concurrency_conflict", "Action receipt changed concurrently.")
        if status == "SUCCEEDED":
            return {"status": "success", "duplicate": False, "action_id": action_id,
                    "receipt_status": status, "provider_effect_id": committed.get("provider_effect_id")}
        return _error(str(committed.get("error_code") or "provider_rejected"),
                      "Provider reconciliation found no completed action.")

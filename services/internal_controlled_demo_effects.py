"""Idempotent executor for the closed internal-demo effect boundary.

The adapter is intentionally injectable. Production wiring may only supply an
adapter that re-verifies the pinned Alex/Founder account subjects; callers can
never supply OAuth credentials, addresses, text, or a provider selection.
"""

from __future__ import annotations

from typing import Any, Protocol

from services.actor_identity import ActorPrincipal
from services.capability_registry import require_controlled_action
from services.durable_store import AtomicMutation, DurableStore, production_store
from services.hiring_contracts import canonical_hash, stable_id, utc_now
from services.internal_controlled_demo import InternalControlledDemoService, build_exact_action


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    del http_status
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


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

    ACTION_COLLECTION = "external_actions"

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
        try:
            capability = require_controlled_action(
                action_kind, "internal_demo_google")
        except ValueError:
            return _error("capability_disabled",
                          "This internal-demo effect is not reviewed.", 403)
        run = await self.runs._active_run(principal, demo_run_id)
        if run.get("error"):
            return run
        built = build_exact_action(
            action_kind, demo_run_id=demo_run_id,
            calendar_start_at=str(run["demo_run"].get("calendar_start_at") or ""))
        if built.get("error"):
            return built
        approval = await self.store.get("approvals", approval_id)
        if (not approval or approval.get("status") not in {
                "GRANTED", "CLAIMED", "CONSUMED"}
                or approval.get("workspace_id") != principal.workspace_id
                or approval.get("demo_run_id") != demo_run_id
                or approval.get("action_kind") != action_kind
                or approval.get("exact_action") != built["exact_action"]
                or approval.get("subject_hash") != built["subject_hash"]):
            return _error("approval_binding_mismatch",
                          "Approval does not cover the current exact action.")
        exact = approval["exact_action"]
        action_id = stable_id("idemoaction", principal.workspace_id, canonical_hash(exact))
        current = await self.store.get(self.ACTION_COLLECTION, action_id)
        if current:
            if current.get("request_hash") != canonical_hash(exact):
                return _error("idempotency_conflict", "Action identity has drifted.")
            if current.get("status") == "SUCCEEDED":
                return {"status": "success", "duplicate": True, "action_id": action_id,
                        "receipt_status": "SUCCEEDED"}
            if current.get("status") in {"EXECUTING", "UNCERTAIN"} \
                    or current.get("provider_started_at"):
                return _error("reconciliation_required", "Provider outcome is not yet known.")
        if not current:
            claim_id = stable_id("claim", approval_id, action_id)
            row = {"schema_version": 2, "action_id": action_id,
                   "workspace_id": principal.workspace_id, "demo_run_id": demo_run_id,
                   "founder_id": principal.workspace_id,
                   "action_domain": "INTERNAL_CONTROLLED_DEMO",
                   "connection_id": "internal_demo_google",
                   "approval_id": approval_id, "action_kind": action_kind,
                   "capability_id": capability.capability_id,
                   "capability_version": capability.semantic_version,
                   "exact_action": exact, "request_hash": canonical_hash(exact),
                   "status": "PREPARED", "claim_id": claim_id,
                   "approval_consumed": False,
                   "provider_started_at": None,
                   "consequence_start_committed_at": None,
                   "provider_request_id": stable_id(
                       "providerrequest", action_id, "1"),
                   "provider_idempotency_key": stable_id(
                       "providerkey", action_id),
                   "provider_effect_id": None,
                   "internal_demo": True, "fixture_id": run["demo_run"]["fixture_id"],
                   "created_at": utc_now(), "updated_at": utc_now(), "version": 1}
            approval = await self.store.get("approvals", approval_id)
            if (not approval or approval.get("status") != "GRANTED"
                    or approval.get("demo_run_id") != demo_run_id
                    or approval.get("action_kind") != action_kind
                    or approval.get("exact_action") != exact
                    or approval.get("subject_hash") != built["subject_hash"]):
                return _error("approval_binding_mismatch",
                              "Approval does not cover this exact action.")
            committed = await self.store.atomic_compare_and_set((
                AtomicMutation(
                    "approvals", approval_id,
                    int(approval["version"]),
                    updates={"status": "CLAIMED", "claim_id": claim_id,
                             "claimed_action_id": action_id,
                             "claimed_at": utc_now(),
                             "updated_at": utc_now()}),
                AtomicMutation(
                    self.ACTION_COLLECTION, action_id, None, record=row),
            ))
            if not committed:
                return _error("concurrency_conflict",
                              "Approval or action changed concurrently.")
            current = committed[(self.ACTION_COLLECTION, action_id)]
        if not current or current.get("approval_id") != approval_id:
            return _error("approval_binding_mismatch", "Approval does not name this exact action.")
        approval = await self.store.get("approvals", approval_id)
        if not approval:
            return _error("approval_binding_mismatch", "Action approval is missing.")
        legacy_consumed = bool(
            int(current.get("schema_version") or 1) == 1
            and approval.get("status") == "CONSUMED")
        claimed = bool(
            approval.get("status") == "CLAIMED"
            and approval.get("claimed_action_id") == action_id
            and approval.get("claim_id") == current.get("claim_id"))
        if not claimed and not legacy_consumed:
            return _error("approval_binding_mismatch",
                          "The approval claim no longer belongs to this action.")
        started_at = utc_now()
        mutations = [AtomicMutation(
            self.ACTION_COLLECTION, action_id, int(current["version"]),
            updates={"status": "EXECUTING", "approval_consumed": True,
                     "provider_started_at": started_at,
                     "consequence_start_committed_at": started_at,
                     "updated_at": started_at})]
        if claimed:
            mutations.insert(0, AtomicMutation(
                "approvals", approval_id,
                int(approval["version"]),
                updates={"status": "CONSUMED", "consumed_at": started_at,
                         "terminal_action_id": action_id,
                         "updated_at": started_at}))
        started = await self.store.atomic_compare_and_set(tuple(mutations))
        if not started:
            return _error("concurrency_conflict", "Action changed concurrently.")
        leased = started[(self.ACTION_COLLECTION, action_id)]
        try:
            result = await self.adapter.execute(
                exact_action=exact, action_id=action_id)
        except Exception:
            committed = await self.store.compare_and_set(
                self.ACTION_COLLECTION, action_id, int(leased["version"]), {
                    "status": "UNCERTAIN",
                    "error_code": "provider_outcome_unconfirmed",
                    "updated_at": utc_now(), "completed_at": utc_now(),
                })
            if not committed:
                return _error("concurrency_conflict",
                              "Action receipt changed concurrently.")
            return _error(
                "reconciliation_required",
                "Provider outcome is uncertain; no automatic retry occurred.",
                503)
        provider_status = str(result.get("status") or "uncertain")
        status = {"success": "SUCCEEDED", "failed": "FAILED"}.get(provider_status, "UNCERTAIN")
        committed = await self.store.compare_and_set(
            self.ACTION_COLLECTION, action_id, int(leased["version"]),
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
        current = await self.store.get(self.ACTION_COLLECTION, action_id)
        if (not current or current.get("workspace_id") != principal.workspace_id
                or current.get("demo_run_id") != demo_run_id):
            return _error("action_not_found", "Internal-demo action does not exist.", 404)
        if current.get("status") == "SUCCEEDED":
            return {"status": "success", "duplicate": True, "action_id": action_id,
                    "receipt_status": "SUCCEEDED"}
        if current.get("status") not in {"UNCERTAIN", "EXECUTING"}:
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
            self.ACTION_COLLECTION, action_id, int(current["version"]), {
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

"""Closed H4S effect execution on the shared external-action ledger.

The service owns PREPARED/terminal receipts and the irreversible boundary. A
provider adapter receives only a server-resolved test binding, destination
addresses, and the already exact-approved rendered payload. It never receives
normal connector ids, a founder session, or a free-text recipient.
"""

from __future__ import annotations

from typing import Any, Protocol

from services.actor_identity import ActorPrincipal
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import canonical_hash, stable_id, utc_now
from services.hiring_sandbox import HiringSandboxService
from services.workflow_runtime import WorkflowRuntime


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "http_status": http_status}


class H4SEffectAdapter(Protocol):
    """Provider boundary; implementations must return errors as data."""

    async def execute(self, *, action_kind: str, binding: dict[str, Any],
                      normalized_destinations: list[str],
                      rendered_payload: dict[str, Any], action_id: str,
                      causal_token: str) -> dict[str, Any]: ...

    async def reconcile(self, *, action: dict[str, Any],
                        binding: dict[str, Any]) -> dict[str, Any]: ...


class DisabledH4SEffectAdapter:
    """Safe default until a reviewed test-account provider is installed."""

    async def execute(self, **_: Any) -> dict[str, Any]:
        return _error("sandbox_provider_not_configured",
                      "No H4S test-account provider is configured.", 503)

    async def reconcile(self, **_: Any) -> dict[str, Any]:
        return _error("sandbox_provider_not_configured",
                      "No H4S test-account provider is configured.", 503)


class H4SEffectService:
    """Prepare, consume exact approval, execute once, and reconcile H4S effects."""

    def __init__(self, *, sandbox: HiringSandboxService,
                 adapter: H4SEffectAdapter | None = None,
                 store: DurableStore | None = None):
        self.store = store or production_store()
        self.sandbox = sandbox
        if adapter is None:
            # Import lazily so unit tests retain a no-network disabled default
            # unless they explicitly exercise the reviewed Google boundary.
            from services.hiring_h4s_google import H4SGoogleEffectAdapter
            adapter = H4SGoogleEffectAdapter()
        self.adapter = adapter

    @staticmethod
    def _action_id(workspace_id: str, exact_action: dict[str, Any]) -> str:
        return stable_id("h4saction", workspace_id, canonical_hash(exact_action))

    async def execute(
            self, *, principal: ActorPrincipal, approval_id: str,
            sandbox_run_id: str, binding_id: str, candidate_application_id: str,
            destination_ids: list[str],
            action_kind: str, rendered_payload: dict[str, Any]) -> dict[str, Any]:
        """Execute one exact-approved effect, never retrying an attempted call.

        A PREPARED row is created before approval consumption. If the process
        dies before the provider call, a subsequent exact retry can safely
        consume the still-granted approval. Once ``provider_started_at`` is
        recorded, a retry is refused into reconciliation rather than sent again.
        """
        built = await self.sandbox.build_exact_action(
            sandbox_run_id=sandbox_run_id, binding_id=binding_id,
            candidate_application_id=candidate_application_id,
            destination_ids=destination_ids, action_kind=action_kind,
            rendered_payload=rendered_payload)
        if built.get("error"):
            return built
        exact_action = built["exact_action"]
        # A production adapter may have deployment-owned prerequisites (the
        # pinned test grant and OAuth client). Check those before consuming a
        # founder approval. Test adapters intentionally omit this optional,
        # credential-free hook.
        provider_kind = ("GMAIL_TEST" if action_kind == "H4S_SEND_EMAIL"
                         else "CALENDAR_TEST")
        binding_result = await self.sandbox.resolve_connector_binding(
            sandbox_run_id=sandbox_run_id, binding_id=binding_id,
            provider_kind=provider_kind)
        if binding_result.get("error"):
            return binding_result
        preflight = getattr(self.adapter, "preflight", None)
        if preflight is not None:
            readiness = await preflight(binding=binding_result["binding"])
            if readiness.get("status") != "success":
                return readiness
        action_id = self._action_id(principal.workspace_id, exact_action)
        current = await self.store.get("external_actions", action_id)
        if current:
            outcome = self._existing(current, principal.workspace_id, exact_action)
            if outcome is not None:
                if outcome.get("status") == "success":
                    await self._ensure_effect_followup(current)
                return outcome
        if not current:
            row = {
                "schema_version": 1, "action_id": action_id,
                "founder_id": principal.workspace_id, "workspace_id": principal.workspace_id,
                "actor_id": principal.actor_id, "connection_id": f"h4s:{binding_id}",
                "action_kind": action_kind, "idempotency_key": action_id,
                "request_hash": canonical_hash(exact_action),
                "approval_id": approval_id, "sandbox_context": built["sandbox_context"],
                "exact_action": exact_action, "status": "PREPARED",
                "approval_consumed": False, "provider_started_at": None,
                "provider_effect_id": None, "result_ref": {}, "error_code": None,
                "uncertainty_reason": None, "synthetic": True,
                "fixture_id": built["sandbox_context"]["fixture_id"],
                "synthetic_namespace": (await self.store.get(
                    "hiring_sandbox_runs", sandbox_run_id) or {}).get("synthetic_namespace"),
                "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
            }
            if not await self.store.create("external_actions", action_id, row):
                current = await self.store.get("external_actions", action_id)
                if current:
                    outcome = self._existing(current, principal.workspace_id, exact_action)
                    if outcome is not None:
                        if outcome.get("status") == "success":
                            await self._ensure_effect_followup(current)
                        return outcome
            current = await self.store.get("external_actions", action_id)
        if not current:
            return _error("action_prepare_failed", "Could not prepare the H4S action.", 503)
        if current.get("approval_id") != approval_id:
            return _error("approval_binding_mismatch",
                          "A different approval names this H4S action.")
        if not current.get("approval_consumed"):
            # There is no cross-collection transaction in the durable-store
            # contract. If a process dies after the approval CAS but before
            # this action row is marked, recover only when that exact approval
            # is demonstrably CONSUMED for this exact action. It is then safe
            # to continue to the one provider attempt; we never re-claim or
            # issue a fresh effect.
            approval = await self.store.get("approvals", approval_id)
            already_consumed = bool(
                approval
                and approval.get("status") == "CONSUMED"
                and approval.get("workspace_id") == principal.workspace_id
                and canonical_hash(approval.get("exact_action") or {})
                == canonical_hash(exact_action))
            if already_consumed:
                current = await self.store.compare_and_set(
                    "external_actions", action_id, int(current["version"]), {
                        "approval_consumed": True, "updated_at": utc_now()})
                if not current:
                    return _error("concurrency_conflict", "Action changed concurrently.")
            else:
                claimed = await self.sandbox.claim_effect_approval(
                    principal=principal, approval_id=approval_id,
                    sandbox_run_id=sandbox_run_id, binding_id=binding_id,
                    candidate_application_id=candidate_application_id,
                    destination_ids=destination_ids, action_kind=action_kind,
                    rendered_payload=rendered_payload)
                if claimed.get("error"):
                    return claimed
                current = await self.store.compare_and_set(
                    "external_actions", action_id, int(current["version"]), {
                        "approval_consumed": True, "updated_at": utc_now()})
                if not current:
                    # The approval is consumed. Leave the prepared durable row
                    # for the exact recovery path above; never call a provider
                    # from this ambiguous ownership state.
                    return _error("concurrency_conflict", "Action changed concurrently.")
        if current.get("provider_started_at"):
            return _error("reconciliation_required",
                          "The provider outcome is not yet known; reconcile first.")
        leased = await self.store.compare_and_set(
            "external_actions", action_id, int(current["version"]), {
                "provider_started_at": utc_now(), "updated_at": utc_now()})
        if not leased:
            return _error("concurrency_conflict", "Action changed concurrently.")
        binding = binding_result
        result = await self.adapter.execute(
            action_kind=action_kind, binding=binding["binding"],
            normalized_destinations=exact_action["normalized_destinations"],
            rendered_payload=rendered_payload, action_id=action_id,
            causal_token=stable_id("h4scausal", action_id))
        return await self._finish_provider_result(action_id, leased, result)

    async def reconcile(self, *, principal: ActorPrincipal,
                        sandbox_run_id: str, action_id: str) -> dict[str, Any]:
        """Resolve only an uncertain H4S receipt using provider evidence."""
        action = await self.store.get("external_actions", action_id)
        if (not action or action.get("workspace_id") != principal.workspace_id
                or action.get("status") != "UNCERTAIN"):
            return _error("reconciliation_required", "No uncertain H4S action was found.", 404)
        context = action.get("sandbox_context") or {}
        if context.get("sandbox_run_id") != sandbox_run_id:
            return _error("sandbox_action_mismatch", "Action is outside this sandbox.", 403)
        kind = "GMAIL_TEST" if action.get("action_kind") == "H4S_SEND_EMAIL" else "CALENDAR_TEST"
        binding = await self.sandbox.resolve_connector_binding(
            sandbox_run_id=sandbox_run_id,
            binding_id=str(context.get("connector_binding_id") or ""), provider_kind=kind)
        if binding.get("error"):
            return binding
        result = await self.adapter.reconcile(action=action, binding=binding["binding"])
        if result.get("status") not in {"success", "failed"}:
            return _error("reconciliation_required", "Provider evidence is still inconclusive.")
        status = "SUCCEEDED" if result["status"] == "success" else "FAILED"
        committed = await self.store.compare_and_set(
            "external_actions", action_id, int(action["version"]), {
                "status": status, "provider_effect_id": result.get("provider_effect_id"),
                "result_ref": self._safe_ref(result.get("result_ref")),
                "error_code": result.get("error_code"), "uncertainty_reason": None,
                "updated_at": utc_now(), "completed_at": utc_now()})
        return ({"status": "success", "action_id": action_id,
                 "receipt_status": status, "duplicate": False}
                if committed else _error("concurrency_conflict", "Action changed concurrently."))

    @staticmethod
    def _existing(row: dict[str, Any], workspace_id: str,
                  exact_action: dict[str, Any]) -> dict[str, Any] | None:
        if row.get("workspace_id") != workspace_id:
            return _error("owner_mismatch", "H4S action is not authorized.", 404)
        if row.get("request_hash") != canonical_hash(exact_action):
            return _error("idempotency_conflict", "H4S action payload changed.")
        if row.get("status") == "SUCCEEDED":
            return {"status": "success", "duplicate": True,
                    "action_id": row["action_id"], "receipt_status": "SUCCEEDED",
                    "provider_effect_id": row.get("provider_effect_id")}
        if row.get("status") == "FAILED":
            return _error(str(row.get("error_code") or "provider_rejected"),
                          "The H4S action already failed.")
        if row.get("status") == "UNCERTAIN" or row.get("provider_started_at"):
            return _error("reconciliation_required",
                          "The H4S action requires provider reconciliation.")
        return None

    @staticmethod
    def _safe_ref(value: Any) -> dict[str, str]:
        return ({str(key)[:64]: str(item)[:280] for key, item in value.items()}
                if isinstance(value, dict) else {})

    async def _finish_error(self, action_id: str, leased: dict[str, Any],
                            result: dict[str, Any]) -> dict[str, Any]:
        return await self._finish_provider_result(action_id, leased, {
            "status": "failed", "error_code": result.get("error_code", "provider_rejected")})

    async def _finish_provider_result(self, action_id: str, leased: dict[str, Any],
                                      result: dict[str, Any]) -> dict[str, Any]:
        provider_status = str(result.get("status") or "uncertain")
        status = {"success": "SUCCEEDED", "failed": "FAILED",
                  "uncertain": "UNCERTAIN"}.get(provider_status, "UNCERTAIN")
        committed = await self.store.compare_and_set(
            "external_actions", action_id, int(leased["version"]), {
                "status": status, "provider_effect_id": result.get("provider_effect_id"),
                "result_ref": self._safe_ref(result.get("result_ref")),
                "error_code": result.get("error_code"),
                "uncertainty_reason": result.get("uncertainty_reason"),
                "updated_at": utc_now(), "completed_at": utc_now()})
        if not committed:
            return _error("concurrency_conflict", "Action receipt changed concurrently.")
        if status == "SUCCEEDED":
            followup = await self._ensure_effect_followup(committed)
            return {"status": "success", "duplicate": False, "action_id": action_id,
                    "receipt_status": status,
                    "provider_effect_id": committed.get("provider_effect_id"),
                    "followup_wait_id": followup.get("wait_id")}
        if status == "UNCERTAIN":
            return _error("reconciliation_required",
                          "Provider outcome is uncertain; no automatic retry occurred.", 503)
        return _error(str(committed.get("error_code") or "provider_rejected"),
                      "The H4S provider rejected the action.")

    async def _ensure_effect_followup(self, action: dict[str, Any]) -> dict[str, Any]:
        """Open the causal reply wait after one committed outbound receipt.

        The durable action receipt is written first. Repeating this helper is
        safe, so a crash before wait creation heals on an idempotent action
        replay without a second mail send.
        """
        if action.get("status") != "SUCCEEDED" or action.get("action_kind") != "H4S_SEND_EMAIL":
            return {"status": "success", "wait_id": None}
        exact = action.get("exact_action") or {}
        candidate_run_id = str(exact.get("candidate_run_id") or "")
        if not candidate_run_id:
            return _error("sandbox_candidate_run_missing", "Outbound action lacks a candidate run.", 503)
        wait = await WorkflowRuntime(self.store).create_wait(
            candidate_run_id, wait_kind="H4S_TEST_REPLY",
            correlation_key=f"h4s-email:{action['action_id']}")
        if wait.get("error"):
            return wait
        return {"status": "success", "wait_id": wait["wait_id"],
                "duplicate": wait.get("duplicate", False)}

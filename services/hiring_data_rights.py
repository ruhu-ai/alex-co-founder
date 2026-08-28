"""Recent-auth candidate export and recoverable exact-plan deletion."""

from __future__ import annotations

from typing import Any

from services.actor_identity import ActorPrincipal, WorkspaceRole, authorize
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import canonical_hash, stable_id, utc_now
from services.hiring_identity_vault import CandidateIdentityVault


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    del http_status
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


class HiringDataRightsService:
    def __init__(self, *, identity_vault: CandidateIdentityVault,
                 store: DurableStore | None = None):
        self.store = store or production_store()
        self.identity_vault = identity_vault

    async def export_candidate(self, *, principal: ActorPrincipal,
                               application_id: str,
                               client_request_id: str) -> dict[str, Any]:
        application = await self.store.get("candidate_applications", application_id)
        gate = await self._authorize(principal, application)
        if gate.get("error"):
            return gate
        identity = await self.identity_vault.reveal_identity(
            identity_id=application["candidate_id"],
            workspace_id=application["workspace_id"], role_id=application["role_id"],
            candidate_application_id=application_id, principal=principal)
        if identity.get("error"):
            return identity
        inventory = await self._inventory(application)
        export_rows = []
        for collection, document_id in inventory:
            if collection == "candidate_identities":
                continue
            row = await self.store.get(collection, document_id)
            if row:
                export_rows.append({"collection": collection,
                                    "document_id": document_id, "record": row})
        audit_id = stable_id("audit", principal.workspace_id, "candidate_export",
                             application_id, client_request_id)
        await self.store.create("audit", audit_id, {
            "schema_version": 2, "audit_id": audit_id,
            "founder_id": principal.workspace_id,
            "workspace_id": principal.workspace_id, "actor": principal.actor_id,
            "actor_id": principal.actor_id, "action": "hiring.candidate.export",
            "target": f"candidate_applications/{application_id}",
            "result": "success", "detail": "authorized candidate data export",
            "idempotency_key": client_request_id, "created_at": utc_now(), "version": 1,
        })
        return {
            "status": "success", "candidate_application_id": application_id,
            "identity": identity["identity"], "records": export_rows,
            "record_count": len(export_rows) + 1,
            "generated_at": utc_now(),
            "audit_id": audit_id,
            "provider_limitations": [
                "The original provider email is not deleted by an H0-H3 export.",
                "A founder-published job-board post is outside this candidate record.",
                "Append-only security audit records are exported separately under retention policy.",
            ],
        }

    async def deletion_plan(self, *, principal: ActorPrincipal,
                            application_id: str) -> dict[str, Any]:
        application = await self.store.get("candidate_applications", application_id)
        gate = await self._authorize(principal, application)
        if gate.get("error"):
            return gate
        identity = await self.store.get("candidate_identities", application["candidate_id"])
        if identity and identity.get("legal_hold") is True:
            return _error("legal_hold", "Deletion is blocked by a reviewed legal hold.", 423)
        inventory = await self._inventory(application)
        paths = [f"{collection}/{document_id}" for collection, document_id in inventory]
        plan_hash = canonical_hash({"schema_version": 1,
                                    "workspace_id": principal.workspace_id,
                                    "application_id": application_id,
                                    "paths": sorted(paths)})
        return {"status": "success", "dry_run": True,
                "candidate_application_id": application_id,
                "inventory_hash": plan_hash, "delete_count": len(paths),
                "paths": paths,
                "provider_limitations": [
                    "H0-H3 does not delete the original Gmail message from Google.",
                    "Manual job-board publications are not candidate records and remain external.",
                    "Append-only security audit records retain opaque ids/hashes under audit retention.",
                ]}

    async def set_legal_hold(self, *, principal: ActorPrincipal,
                             application_id: str, active: bool,
                             reason_code: str, expected_identity_version: int,
                             client_request_id: str) -> dict[str, Any]:
        """Apply/release a synthetic legal hold with fresh owner authority."""
        if principal.role is not WorkspaceRole.OWNER:
            return _error("operation_forbidden",
                          "Only the workspace owner may change a legal hold.", 403)
        if reason_code not in {"LITIGATION", "REGULATORY", "QUALIFIED_REVIEW"}:
            return _error("invalid_contract", "Unknown legal-hold reason.", 400)
        application = await self.store.get("candidate_applications", application_id)
        gate = await self._authorize(principal, application)
        if gate.get("error"):
            return gate
        identity_id = application["candidate_id"]
        identity = await self.store.get("candidate_identities", identity_id)
        if not identity or identity.get("workspace_id") != principal.workspace_id:
            return _error("identity_not_found", "Candidate identity does not exist.", 404)
        request_id = stable_id("legal_hold", principal.workspace_id,
                               application_id, client_request_id)
        if identity.get("last_legal_hold_request_id") == request_id:
            return {"status": "success", "duplicate": True,
                    "legal_hold": identity.get("legal_hold") is True,
                    "identity_version": identity["version"]}
        committed = await self.store.compare_and_set(
            "candidate_identities", identity_id, expected_identity_version, {
                "legal_hold": active, "legal_hold_reason_code": reason_code,
                "legal_hold_changed_by_actor_id": principal.actor_id,
                "legal_hold_changed_at": utc_now(),
                "last_legal_hold_request_id": request_id,
            })
        if not committed:
            return _error("version_conflict", "Identity changed; reload legal hold.")
        audit_id = stable_id("audit", principal.workspace_id, request_id)
        await self.store.create("audit", audit_id, {
            "schema_version": 2, "audit_id": audit_id,
            "founder_id": principal.workspace_id,
            "workspace_id": principal.workspace_id, "actor": principal.actor_id,
            "actor_id": principal.actor_id, "action": "hiring.legal_hold.change",
            "target": f"candidate_applications/{application_id}",
            "result": "success",
            "detail": f"active={active} reason={reason_code}",
            "idempotency_key": client_request_id,
            "created_at": utc_now(), "version": 1,
        })
        return {"status": "success", "duplicate": False, "legal_hold": active,
                "identity_version": committed["version"], "audit_id": audit_id}

    async def execute_deletion(self, *, principal: ActorPrincipal,
                               application_id: str, expected_inventory_hash: str,
                               client_request_id: str) -> dict[str, Any]:
        receipt_id = stable_id("rights_receipt", principal.workspace_id,
                               client_request_id)
        receipt = await self.store.get("candidate_data_rights_receipts", receipt_id)
        if receipt:
            if (receipt.get("workspace_id") != principal.workspace_id
                    or receipt.get("actor_id") != principal.actor_id
                    or receipt.get("application_id_hash") != canonical_hash(
                        {"application_id": application_id})
                    or receipt.get("inventory_hash") != expected_inventory_hash):
                return _error("idempotency_conflict",
                              "Deletion request id names another plan.")
            if receipt.get("status") == "COMPLETE":
                return {"status": "success", "duplicate": True,
                        "deletion_receipt_id": receipt_id,
                        "deleted": receipt.get("deleted", 0),
                        "provider_limitations": receipt.get("provider_limitations", [])}
            paths = list(receipt.get("paths", []))
            identity_paths = [path for path in paths
                              if path.startswith("candidate_identities/")]
            if identity_paths:
                identity = await self.store.get(
                    "candidate_identities", identity_paths[0].split("/", 1)[1])
                if identity and identity.get("legal_hold") is True:
                    return _error("legal_hold",
                                  "Deletion is blocked by a reviewed legal hold.", 423)
        else:
            plan = await self.deletion_plan(
                principal=principal, application_id=application_id)
            if plan.get("error"):
                return plan
            if plan["inventory_hash"] != expected_inventory_hash:
                return _error("version_conflict",
                              "Deletion inventory changed; request a new dry-run.")
            paths = list(plan["paths"])
            receipt = {
                "schema_version": 1, "deletion_receipt_id": receipt_id,
                "workspace_id": principal.workspace_id,
                "founder_id": principal.workspace_id,
                "application_id_hash": canonical_hash({"application_id": application_id}),
                "inventory_hash": expected_inventory_hash, "paths": paths,
                "actor_id": principal.actor_id,
                "actor_membership_version": principal.membership_version,
                "status": "PREPARED", "deleted": 0,
                "provider_limitations": plan["provider_limitations"],
                "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
            }
            if not await self.store.create(
                    "candidate_data_rights_receipts", receipt_id, receipt):
                return _error("concurrency_conflict", "Deletion receipt raced; retry.", 503)
        deleted = int(receipt.get("deleted", 0))
        try:
            for path in paths[deleted:]:
                collection, document_id = path.split("/", 1)
                await self.store.delete(collection, document_id)
                deleted += 1
                current = await self.store.get(
                    "candidate_data_rights_receipts", receipt_id)
                if current:
                    await self.store.compare_and_set(
                        "candidate_data_rights_receipts", receipt_id,
                        int(current["version"]), {
                            "deleted": deleted, "updated_at": utc_now()})
        except Exception:
            return _error("deletion_incomplete",
                          "Deletion remains recoverable from its durable receipt.", 503)
        current = await self.store.get("candidate_data_rights_receipts", receipt_id)
        if current:
            await self.store.compare_and_set(
                "candidate_data_rights_receipts", receipt_id, int(current["version"]), {
                    "status": "COMPLETE", "deleted": deleted,
                    # Exact paths are no longer needed after proof of completion.
                    "paths": [], "completed_at": utc_now(), "updated_at": utc_now()})
        audit_id = stable_id("audit", principal.workspace_id, "candidate_delete",
                             receipt_id)
        await self.store.create("audit", audit_id, {
            "schema_version": 2, "audit_id": audit_id,
            "founder_id": principal.workspace_id,
            "workspace_id": principal.workspace_id, "actor": principal.actor_id,
            "actor_id": principal.actor_id, "action": "hiring.candidate.delete",
            "target": f"candidate_application_hash/{receipt['application_id_hash']}",
            "result": "success", "detail": f"deleted_records={deleted}",
            "idempotency_key": client_request_id,
            "created_at": utc_now(), "version": 1,
        })
        return {"status": "success", "duplicate": False,
                "deletion_receipt_id": receipt_id, "deleted": deleted,
                "provider_limitations": receipt["provider_limitations"],
                "audit_id": audit_id}

    async def _authorize(self, principal: ActorPrincipal,
                         application: dict[str, Any] | None) -> dict[str, Any]:
        if not application or application.get("workspace_id") != principal.workspace_id:
            return _error("application_not_found", "Application does not exist.", 404)
        gate = authorize(principal, "read_candidate", role_id=application["role_id"],
                         candidate_application_id=application["candidate_application_id"],
                         require_fresh=True)
        if gate.get("error"):
            return _error("step_up_required",
                          "Recent authorized sign-in is required.", 401)
        if principal.role not in {WorkspaceRole.OWNER, WorkspaceRole.HIRING_MANAGER}:
            return _error("operation_forbidden",
                          "Only the hiring owner or manager may perform data-rights work.", 403)
        return {"status": "success"}

    async def _inventory(self, application: dict[str, Any]) -> list[tuple[str, str]]:
        workspace_id = application["workspace_id"]
        application_id = application["candidate_application_id"]
        run_id = application["run_id"]
        rows: list[tuple[str, str]] = []
        for collection in (
                "hiring_candidate_artifacts", "candidate_evidence",
                "candidate_assessments", "hiring_decisions",
                "hiring_candidate_requests", "founder_inbox", "external_events"):
            for row in await self.store.list(
                    collection, filters={"workspace_id": workspace_id,
                                         "candidate_application_id": application_id},
                    limit=1000):
                rows.append((collection, str(row["id"])))
        for collection in ("workflow_steps", "step_attempts", "waits", "run_events"):
            for row in await self.store.list(
                    collection, filters={"run_id": run_id}, limit=1000):
                rows.append((collection, str(row["id"])))
        message_id = str(application.get("provider_message_id") or "")
        if message_id:
            if await self.store.get("hiring_fixture_messages", message_id):
                rows.append(("hiring_fixture_messages", message_id))
            for row in await self.store.list(
                    "mailbox_fetch_batch_entries",
                    filters={"provider_message_id": message_id}, limit=1000):
                rows.append(("mailbox_fetch_batch_entries", str(row["id"])))
        if await self.store.get("workflow_runs", run_id):
            rows.append(("workflow_runs", run_id))
        if await self.store.get("candidate_identities", application["candidate_id"]):
            rows.append(("candidate_identities", application["candidate_id"]))
        rows.append(("candidate_applications", application_id))
        # Unique deterministic child-first order; the deletion receipt is never
        # part of its own plan.
        return list(dict.fromkeys(rows))

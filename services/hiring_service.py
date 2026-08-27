"""H0-H3 hiring domain service: role, evidence and explicit human decision."""

from __future__ import annotations

import hashlib
from typing import Any

from services import hiring_activation, hiring_evidence
from services.actor_identity import ActorPrincipal, WorkspaceRole, authorize
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import (
    AnalystInput,
    CandidateState,
    ContentRisk,
    Criterion,
    CriterionAssessment,
    DecisionKind,
    EvidenceAuthority,
    EvidenceItem,
    EvidenceLocator,
    EvidenceStatus,
    HumanDecisionInput,
    RoleContract,
    RoleState,
    RunKind,
    Verification,
    canonical_hash,
    stable_id,
    utc_now,
)
from services.hiring_identity_vault import CandidateIdentityVault
from services.resource_sensitivity import registration_policy
from services.workflow_runtime import WorkflowRuntime


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "http_status": http_status}


def _is_https_url(value: str) -> bool:
    """Accept only an absolute https:// URL with a hostname and no credentials."""
    from urllib.parse import urlsplit

    if len(value) > 2000:
        return False
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return False
    return bool(parts.scheme == "https" and parts.hostname
                and "@" not in parts.netloc)


class HiringService:
    def __init__(self, *, store: DurableStore | None = None,
                 identity_vault: CandidateIdentityVault,
                 runtime: WorkflowRuntime | None = None):
        self.store = store or production_store()
        self.identity_vault = identity_vault
        self.runtime = runtime or WorkflowRuntime(self.store)

    async def create_role(self, *, principal: ActorPrincipal,
                          contract: RoleContract, client_request_id: str,
                          synthetic_guard: dict[str, Any]) -> dict[str, Any]:
        gate = hiring_activation.require_synthetic(synthetic_guard)
        if gate.get("error"):
            return gate
        if principal.role is not WorkspaceRole.OWNER:
            return _error("operation_forbidden", "Only an owner may create a role.", 403)
        role_id = stable_id("role", principal.workspace_id, client_request_id)
        journey_id = stable_id("journey", principal.workspace_id, role_id)
        run = await self.runtime.create_run(
            workspace_id=principal.workspace_id, journey_id=journey_id,
            run_kind=RunKind.ROLE, idempotency_key=f"role:{role_id}",
            domain_ref=role_id, synthetic_guard=synthetic_guard,
            originating_actor_id=principal.actor_id)
        if run.get("error"):
            return run
        mailbox_token = canonical_hash({"role_id": role_id, "kind": "mailbox"})[-20:]
        now = utc_now()
        row = {
            "schema_version": 1, "role_id": role_id,
            "workspace_id": principal.workspace_id, "journey_id": journey_id,
            "run_id": run["run_id"], "role_code": role_id[-8:].upper(),
            "company_name": contract.company_name, "role_title": contract.role_title,
            "role_state": RoleState.DRAFT.value,
            "current_policy_version_id": None, "current_policy_hash": None,
            "headcount_target": contract.headcount_target,
            "accepted_count": 0, "publication_package": {
                "public_job_description": contract.public_job_description,
                "application_address": f"apply+{mailbox_token}@ruhu.ai",
                "static_notice_path": "/hiring-notice.html",
                "linkedin_automation": False,
            },
            "publication_receipts": [], "mailbox_binding": None,
            "runtime_projection": {"status": run["runtime_status"],
                                   "run_id": run["run_id"]},
            "synthetic": True,
            "synthetic_namespace": synthetic_guard["synthetic_namespace"],
            "fixture_id": synthetic_guard["fixture_id"],
            "created_by_actor_id": principal.actor_id,
            "created_at": now, "updated_at": now, "version": 1,
        }
        created = await self.store.create("hiring_roles", role_id, row)
        existing = row if created else await self.store.get("hiring_roles", role_id)
        if not existing or existing.get("fixture_id") != row["fixture_id"]:
            return _error("idempotency_conflict", "Role request id names other work.")
        return {"status": "success", "duplicate": not created,
                "role": existing, "role_contract": contract}

    async def record_publication(self, *, principal: ActorPrincipal, role_id: str,
                                 destination: str, public_url: str,
                                 expected_version: int, client_request_id: str,
                                 attestation: str) -> dict[str, Any]:
        gate = authorize(principal, "record_publication", role_id=role_id)
        if gate.get("error"):
            return gate
        # Every destination, not just LINKEDIN. This value is persisted and
        # later handed to the embedded Browser launcher, so a non-HTTPS scheme
        # (javascript:, data:, http:) must be refused at the server boundary
        # rather than relying on the client-side guard alone.
        if not _is_https_url(public_url):
            return _error("invalid_contract",
                          "Publication URL must be an absolute HTTPS URL.", 400)
        role = await self.store.get("hiring_roles", role_id)
        if not role or role.get("workspace_id") != principal.workspace_id:
            return _error("role_not_found", "Role does not exist.", 404)
        if not role.get("current_policy_version_id"):
            return _error("policy_not_active", "Approve the Role Contract first.")
        receipt_id = stable_id("pubreceipt", role_id, client_request_id)
        if any(item.get("receipt_id") == receipt_id
               for item in role.get("publication_receipts", [])):
            # Heal a crash after the role projection but before wait creation.
            wait = await self.runtime.create_wait(
                role["run_id"], wait_kind="APPLICATION_EMAIL",
                correlation_key=f"role-mailbox:{role_id}")
            if wait.get("error"):
                return {**wait, "recoverable": True}
            return {"status": "success", "duplicate": True,
                    "receipt_id": receipt_id, "role_version": role["version"]}
        receipt = {
            "receipt_id": receipt_id, "destination": destination.upper(),
            "public_url": public_url[:2000], "attestation": attestation[:1000],
            "actor_id": principal.actor_id, "recorded_at": utc_now(),
            "verification_status": "DISABLED_PENDING_TERMS_REVIEW",
            "automated_publication": False,
            "policy_version_id": role["current_policy_version_id"],
            "policy_hash": role["current_policy_hash"],
        }
        committed = await self.store.compare_and_set(
            "hiring_roles", role_id, expected_version, {
                "publication_receipts": [*role.get("publication_receipts", []), receipt],
                "role_state": RoleState.PUBLISHED.value, "updated_at": utc_now(),
            })
        if not committed:
            return _error("version_conflict", "Role changed; reload before recording.")
        wait = await self.runtime.create_wait(
            role["run_id"], wait_kind="APPLICATION_EMAIL",
            correlation_key=f"role-mailbox:{role_id}")
        if wait.get("error"):
            return {**wait, "recoverable": True,
                    "receipt_id": receipt_id,
                    "message": "Publication is committed; wait creation will heal on retry."}
        return {"status": "success", "duplicate": False,
                "receipt_id": receipt_id, "role_version": committed["version"],
                "verification_status": receipt["verification_status"]}

    async def ingest_synthetic_application(
            self, *, role_id: str, provider_message_id: str,
            provider_thread_id: str, identity_fields: dict[str, str],
            blocks: list[dict[str, Any]], source_sha256: str,
            external_event_id: str, synthetic_guard: dict[str, Any],
            crash_point: str = "") -> dict[str, Any]:
        gate = hiring_activation.require_synthetic(synthetic_guard)
        if gate.get("error"):
            return gate
        role = await self.store.get("hiring_roles", role_id)
        if not role or role.get("synthetic") is not True:
            return _error("role_not_found", "Synthetic role does not exist.", 404)
        policy = await self.store.get(
            "hiring_policy_versions", str(role.get("current_policy_version_id") or ""))
        if not policy or policy.get("status") != "APPROVED":
            return _error("policy_not_active", "No approved Role Contract is active.")
        source_event = await self.store.get("external_events", external_event_id)
        if (not source_event or source_event.get("workspace_id") != role["workspace_id"]
                or source_event.get("role_id") != role_id
                or source_event.get("provider_event_id") != provider_message_id):
            return _error("external_event_missing",
                          "A committed trusted-route event must precede application work.", 503)
        application_id = stable_id("candidateapp", role_id, provider_message_id)
        existing = await self.store.get("candidate_applications", application_id)
        if existing:
            if (existing.get("role_id") != role_id
                    or existing.get("provider_message_id") != provider_message_id
                    or existing.get("source_event_id") != external_event_id):
                return _error("idempotency_conflict",
                              "Application identity names different source work.")
            if (existing.get("candidate_state") != CandidateState.ASSESSING.value
                    or existing.get("current_assessment_id")):
                return {"status": "success", "duplicate": True,
                        "candidate_application_id": application_id,
                        "candidate_state": existing["candidate_state"],
                        "assessment_id": existing.get("current_assessment_id")}
            # A worker may have crashed after the durable application receipt.
            # Resume from that checkpoint and idempotently rebuild derived rows.
            application = existing
            candidate_code = existing["candidate_code"]
            run = {"run_id": existing["run_id"]}
            now = utc_now()
        else:
            # Stable pseudonym without a contended role counter or application
            # count (duplicate/concurrent deliveries cannot allocate twice).
            candidate_number = int(hashlib.sha256(
                application_id.encode()).hexdigest()[:12], 16) % 100_000_000
            candidate_code = f"C-{candidate_number:08d}"
            run = await self.runtime.create_run(
                workspace_id=role["workspace_id"], journey_id=role["journey_id"],
                run_kind=RunKind.CANDIDATE, idempotency_key=application_id,
                domain_ref=application_id, parent_run_id=role["run_id"],
                synthetic_guard=synthetic_guard)
            if run.get("error"):
                return run
            identity = await self.identity_vault.store_identity(
                workspace_id=role["workspace_id"], role_id=role_id,
                candidate_application_id=application_id,
                identity_fields=identity_fields, synthetic_guard=synthetic_guard)
            if identity.get("error"):
                return identity
            now = utc_now()
            application = {
                "schema_version": 1, "candidate_application_id": application_id,
                "workspace_id": role["workspace_id"], "role_id": role_id,
                "candidate_id": identity["candidate_id"], "candidate_code": candidate_code,
                "run_id": run["run_id"], "journey_id": role["journey_id"],
                "provider_message_id": provider_message_id,
                "provider_thread_id": provider_thread_id,
                "source_event_id": external_event_id,
                "candidate_state": CandidateState.ASSESSING.value,
                "current_policy_version_id": policy["policy_version_id"],
                "current_assessment_id": None, "current_decision_id": None,
                "artifact_ids": [], "withdrawal": None, "retention_status": "ACTIVE",
                "synthetic": True,
                "synthetic_namespace": synthetic_guard["synthetic_namespace"],
                "fixture_id": synthetic_guard["fixture_id"],
                "created_at": now, "updated_at": now, "version": 1,
            }
            if not await self.store.create(
                    "candidate_applications", application_id, application):
                return _error("concurrency_conflict",
                              "Application was created concurrently; retry safely.", 503)
            if crash_point == "AFTER_APPLICATION_RECEIPT":
                return _error("injected_crash",
                              "Synthetic crash injected after application receipt.", 503)
        artifact_id = stable_id("hartifact", application_id, source_sha256)
        resource_policy = registration_policy(
            scope="HIRING_RESTRICTED", sensitivity="HIRING_RESTRICTED")
        if resource_policy.get("error"):
            return resource_policy
        await self.store.create("hiring_candidate_artifacts", artifact_id, {
            "schema_version": 1, "artifact_id": artifact_id,
            "workspace_id": role["workspace_id"], "role_id": role_id,
            "candidate_application_id": application_id,
            "scope": "HIRING_RESTRICTED", "sensitivity": "HIRING_RESTRICTED",
            "source_sha256": source_sha256,
            "general_search_registered": resource_policy["general_search"],
            "session_resource_registered": resource_policy["session_resource"],
            "profile_eligible": resource_policy["founder_profile"],
            "company_knowledge_eligible": resource_policy["company_knowledge"],
            "created_at": now, "synthetic": True,
            "synthetic_namespace": synthetic_guard["synthetic_namespace"],
            "fixture_id": synthetic_guard["fixture_id"], "version": 1,
        })
        evidence_items: list[EvidenceItem] = []
        inbox_items: list[str] = []
        allowed_criteria = {item["criterion_id"] for item in policy["contract"]["criteria"]}
        for index, block in enumerate(blocks):
            block_id = str(block.get("block_id") or f"block-{index + 1}")
            redacted = hiring_evidence.redact_block(
                str(block.get("text") or ""), block_id=block_id,
                classification_confidence=float(block.get("classification_confidence", 1.0)),
                classifier_disagreed=bool(block.get("classifier_disagreed", False)))
            criterion_ids = [str(item) for item in block.get("criterion_ids", [])
                             if str(item) in allowed_criteria]
            if not criterion_ids:
                continue
            evidence_id = stable_id("ce", application_id, block_id, source_sha256)
            risk = ContentRisk(redacted["content_risk"])
            item = EvidenceItem(
                evidence_id=evidence_id, workspace_id=role["workspace_id"],
                role_id=role_id, candidate_application_id=application_id,
                source_artifact_id=artifact_id,
                source_kind=str(block.get("source_kind") or "RESUME"),
                criterion_ids=criterion_ids, locator=EvidenceLocator(block=block_id),
                quote=redacted["safe_text"][:800] if risk is ContentRisk.CLEAR else "",
                normalized_fact="", authority=EvidenceAuthority.CANDIDATE_CLAIM,
                verification=Verification.UNVERIFIED, content_risk=risk,
                source_sha256=source_sha256,
                redaction_policy_version=hiring_evidence.REDACTION_POLICY_VERSION,
                created_at=datetime_from_iso(now))
            evidence_items.append(item)
            await self.store.create("candidate_evidence", evidence_id, {
                **item.model_dump(mode="json"), "evidence_hash": canonical_hash(item),
                "synthetic": True,
                "synthetic_namespace": synthetic_guard["synthetic_namespace"],
                "fixture_id": synthetic_guard["fixture_id"], "version": 1,
            })
            if redacted["inbox_required"]:
                inbox_id = stable_id("hinbox", evidence_id, redacted["content_risk"])
                await self.store.create("founder_inbox", inbox_id, {
                    "schema_version": 2, "inbox_item_id": inbox_id,
                    "workspace_id": role["workspace_id"],
                    "founder_id": role["workspace_id"], "role_id": role_id,
                    "candidate_application_id": application_id,
                    "kind": "HIRING_REDACTION_WITHHELD", "status": "OPEN",
                    "safe_reason": redacted["content_risk"],
                    "evidence_id": evidence_id, "created_at": now,
                    "synthetic": True,
                    "synthetic_namespace": synthetic_guard["synthetic_namespace"],
                    "fixture_id": synthetic_guard["fixture_id"], "version": 1,
                })
                inbox_items.append(inbox_id)
        criteria = [Criterion.model_validate(item) for item in policy["contract"]["criteria"]]
        invocation_id = stable_id("invoke", application_id, policy["canonical_hash"])
        analyst_input = hiring_evidence.build_analyst_input(
            invocation_id=invocation_id, workspace_id=role["workspace_id"],
            role_id=role_id, candidate_application_id=application_id,
            candidate_code=candidate_code,
            policy_version_id=policy["policy_version_id"],
            policy_hash=policy["canonical_hash"], criteria=criteria,
            evidence=evidence_items)
        if isinstance(analyst_input, dict):
            return analyst_input
        raw_output = _fixture_analyst_output(analyst_input)
        validated = hiring_evidence.validate_analyst_output(
            raw_output, expected=analyst_input,
            model_id="fixture-deterministic-v1", prompt_version="hiring-evidence-v1")
        if validated.get("error"):
            return validated
        output = validated["output"]
        assessment_id = stable_id("assessment", application_id,
                                  validated["assessment_hash"])
        assessment = {
            **output.model_dump(mode="json"), "assessment_id": assessment_id,
            "assessment_hash": validated["assessment_hash"],
            "input_evidence_hashes": sorted(canonical_hash(item) for item in evidence_items),
            "model_id": validated["model_id"], "prompt_version": validated["prompt_version"],
            "staleness": "CURRENT", "created_at": utc_now(),
            "synthetic": True,
            "synthetic_namespace": synthetic_guard["synthetic_namespace"],
            "fixture_id": synthetic_guard["fixture_id"], "version": 1,
        }
        await self.store.create("candidate_assessments", assessment_id, assessment)
        current = await self.store.get("candidate_applications", application_id)
        if not current:
            return _error("application_not_found", "Application disappeared.", 404)
        committed = await self.store.compare_and_set(
            "candidate_applications", application_id, int(current["version"]), {
                "candidate_state": CandidateState.AWAITING_HUMAN_DECISION.value,
                "current_assessment_id": assessment_id, "artifact_ids": [artifact_id],
                "updated_at": utc_now(),
            })
        if not committed:
            return _error("concurrency_conflict", "Application changed concurrently.")
        await self.runtime.append_event(
            run["run_id"], event_kind="EVIDENCE_PASSPORT_COMMITTED",
            idempotency_key=f"assessment:{assessment_id}",
            safe_payload={"source_event_id": external_event_id,
                          "assessment_id": assessment_id,
                          "withheld_blocks": len(inbox_items)})
        return {"status": "success", "duplicate": False,
                "candidate_application_id": application_id,
                "candidate_code": candidate_code,
                "candidate_state": committed["candidate_state"],
                "assessment_id": assessment_id,
                "withheld_inbox_item_ids": inbox_items}

    async def record_human_decision(self, *, principal: ActorPrincipal,
                                    application_id: str,
                                    decision_input: HumanDecisionInput,
                                    crash_point: str = "") -> dict[str, Any]:
        application = await self.store.get("candidate_applications", application_id)
        if not application or application.get("workspace_id") != principal.workspace_id:
            return _error("application_not_found", "Application does not exist.", 404)
        gate = authorize(principal, "human_decision", role_id=application["role_id"],
                         candidate_application_id=application_id)
        if gate.get("error"):
            return gate
        decision_id = stable_id("decision", principal.workspace_id,
                                decision_input.client_request_id)
        request_hash = canonical_hash(decision_input)
        existing = await self.store.get("hiring_decisions", decision_id)
        if existing:
            if (existing.get("workspace_id") != principal.workspace_id
                    or existing.get("candidate_application_id") != application_id
                    or existing.get("actor_id") != principal.actor_id
                    or existing.get("request_hash") != request_hash):
                return _error("idempotency_conflict",
                              "Decision request id names different work.")
            if existing.get("commit_status") == "COMMITTED":
                return {"status": "success", "duplicate": True,
                        "decision_id": decision_id,
                        "candidate_state": existing.get("candidate_state_after"),
                        "communication_required": existing.get(
                            "communication_required", False)}
            if existing.get("commit_status") == "ABORTED":
                return _error("version_conflict",
                              "The prepared decision lost a concurrent update.")
            if application.get("current_decision_id") == decision_id:
                await self.store.compare_and_set(
                    "hiring_decisions", decision_id, int(existing["version"]), {
                        "commit_status": "COMMITTED", "committed_at": utc_now()})
                await self.runtime.append_event(
                    application["run_id"], event_kind="HUMAN_DECISION_COMMITTED",
                    idempotency_key=f"decision:{decision_id}",
                    safe_payload={"decision_id": decision_id,
                                  "decision": existing["decision"]},
                    actor_id=principal.actor_id)
                return {"status": "success", "duplicate": True,
                        "decision_id": decision_id,
                        "candidate_state": existing.get("candidate_state_after"),
                        "communication_required": existing.get(
                            "communication_required", False)}
        role = await self.store.get("hiring_roles", application["role_id"])
        policy = await self.store.get(
            "hiring_policy_versions", str(role.get("current_policy_version_id") if role else ""))
        assessment = await self.store.get("candidate_assessments",
                                          decision_input.assessment_id)
        if (not role or not policy or not assessment
                or assessment.get("candidate_application_id") != application_id
                or assessment.get("policy_version_id") != role.get("current_policy_version_id")
                or assessment.get("policy_hash") != role.get("current_policy_hash")):
            return _error("stale_policy", "Current evidence must be reviewed before deciding.")
        allowed_reasons = set(policy["contract"]["approved_reason_codes"])
        if not decision_input.reason_codes or not set(decision_input.reason_codes) <= allowed_reasons:
            return _error("reason_code_invalid", "Use an approved job-related reason code.", 400)
        for evidence_id in decision_input.evidence_ids_reviewed:
            evidence = await self.store.get("candidate_evidence", evidence_id)
            if (not evidence
                    or evidence.get("candidate_application_id") != application_id
                    or evidence.get("role_id") != application["role_id"]
                    or evidence.get("workspace_id") != principal.workspace_id):
                return _error("evidence_scope_mismatch",
                              "Decision references unauthorized evidence.", 400)
        if int(application["version"]) != decision_input.expected_application_version:
            return _error("version_conflict", "Application changed; reload before deciding.")
        if decision_input.decision in {DecisionKind.REOPEN, DecisionKind.CORRECT}:
            prior_id = decision_input.supersedes_decision_id
            prior = await self.store.get("hiring_decisions", prior_id or "")
            if not prior or prior.get("candidate_application_id") != application_id:
                return _error("superseded_decision_missing",
                              "Correction/reopen must name the prior decision.")
        state_after = _state_for_decision(decision_input.decision)
        now = utc_now()
        row = {
            "schema_version": 1, "decision_id": decision_id,
            "workspace_id": principal.workspace_id,
            "role_id": application["role_id"],
            "candidate_application_id": application_id,
            "run_id": application["run_id"], "stage": application["candidate_state"],
            "decision": decision_input.decision.value,
            "candidate_state_after": state_after,
            "policy_version_id": policy["policy_version_id"],
            "policy_hash": policy["canonical_hash"],
            "assessment_id": assessment["assessment_id"],
            "assessment_hash": assessment["assessment_hash"],
            "evidence_ids_reviewed": decision_input.evidence_ids_reviewed,
            "reason_codes": decision_input.reason_codes,
            "human_note": decision_input.note,
            "actor_id": principal.actor_id,
            "request_hash": request_hash,
            "actor_membership_version": principal.membership_version,
            "supersedes_decision_id": decision_input.supersedes_decision_id,
            "communication_required": decision_input.decision is DecisionKind.DECLINE,
            "communication_action_id": None, "commit_status": "PREPARED",
            "created_at": now, "synthetic": True,
            "synthetic_namespace": application["synthetic_namespace"],
            "fixture_id": application["fixture_id"], "version": 1,
        }
        if not existing and not await self.store.create("hiring_decisions", decision_id, row):
            return await self.record_human_decision(
                principal=principal, application_id=application_id,
                decision_input=decision_input)
        committed = await self.store.compare_and_set(
            "candidate_applications", application_id,
            decision_input.expected_application_version, {
                "candidate_state": state_after, "current_decision_id": decision_id,
                "updated_at": now,
            })
        if not committed:
            prepared = await self.store.get("hiring_decisions", decision_id)
            if prepared and prepared.get("commit_status") == "PREPARED":
                await self.store.compare_and_set(
                    "hiring_decisions", decision_id, int(prepared["version"]), {
                        "commit_status": "ABORTED", "aborted_at": utc_now(),
                        "safe_failure_code": "APPLICATION_VERSION_CONFLICT"})
            return _error("version_conflict", "Application changed; decision not committed.")
        if crash_point == "AFTER_APPLICATION_PROJECTION":
            return _error("injected_crash",
                          "Synthetic crash injected after decision projection.", 503)
        prepared = await self.store.get("hiring_decisions", decision_id)
        if prepared:
            await self.store.compare_and_set(
                "hiring_decisions", decision_id, int(prepared["version"]),
                {"commit_status": "COMMITTED", "committed_at": utc_now()})
        await self.runtime.append_event(
            application["run_id"], event_kind="HUMAN_DECISION_COMMITTED",
            idempotency_key=f"decision:{decision_id}",
            safe_payload={"decision_id": decision_id,
                          "decision": decision_input.decision.value},
            actor_id=principal.actor_id)
        return {"status": "success", "duplicate": False,
                "decision_id": decision_id, "candidate_state": state_after,
                "communication_required": row["communication_required"]}

    async def candidate_detail(self, *, principal: ActorPrincipal,
                               application_id: str) -> dict[str, Any]:
        application = await self.store.get("candidate_applications", application_id)
        if not application or application.get("workspace_id") != principal.workspace_id:
            return _error("application_not_found", "Application does not exist.", 404)
        gate = authorize(principal, "read_candidate", role_id=application["role_id"],
                         candidate_application_id=application_id)
        if gate.get("error"):
            return gate
        assessment = await self.store.get(
            "candidate_assessments", str(application.get("current_assessment_id") or ""))
        role = await self.store.get("hiring_roles", application["role_id"])
        policy = await self.store.get(
            "hiring_policy_versions", str((role or {}).get("current_policy_version_id") or ""))
        if assessment:
            assessment = dict(assessment)
            if (assessment.get("policy_version_id") != (role or {}).get(
                    "current_policy_version_id")
                    or assessment.get("policy_hash") != (role or {}).get(
                        "current_policy_hash")):
                assessment["staleness"] = "STALE_POLICY"
        events = await self.store.list(
            "run_events", filters={"run_id": application["run_id"]},
            order_by="sequence", limit=1000)
        return {"status": "success", "application": application,
                "assessment": assessment, "timeline": events,
                "approved_reason_codes": ((policy or {}).get("contract") or {}).get(
                    "approved_reason_codes", []),
                "identity_revealed": False}

    async def record_candidate_request(
            self, *, principal: ActorPrincipal, application_id: str,
            request_kind: str, safe_note: str, client_request_id: str,
            expected_application_version: int,
            crash_point: str = "") -> dict[str, Any]:
        """Record withdrawal/accommodation/human-contact without deciding."""
        if request_kind not in {"WITHDRAWAL", "ACCOMMODATION", "HUMAN_CONTACT"}:
            return _error("invalid_contract", "Unknown candidate request.", 400)
        application = await self.store.get("candidate_applications", application_id)
        if not application or application.get("workspace_id") != principal.workspace_id:
            return _error("application_not_found", "Application does not exist.", 404)
        gate = authorize(principal, "read_candidate", role_id=application["role_id"],
                         candidate_application_id=application_id)
        if gate.get("error") or principal.role not in {
                WorkspaceRole.OWNER, WorkspaceRole.HIRING_MANAGER}:
            return _error("operation_forbidden",
                          "Only the assigned hiring owner/manager may record this request.", 403)
        request_id = stable_id("candidate_request", principal.workspace_id,
                               client_request_id)
        request_hash = canonical_hash({
            "application_id": application_id, "request_kind": request_kind,
            "safe_note": safe_note[:500], "actor_id": principal.actor_id,
        })
        row = {
            "schema_version": 1, "candidate_request_id": request_id,
            "workspace_id": principal.workspace_id,
            "role_id": application["role_id"],
            "candidate_application_id": application_id,
            "run_id": application["run_id"], "request_kind": request_kind,
            "safe_note": safe_note[:500], "recorded_by_actor_id": principal.actor_id,
            "request_hash": request_hash,
            "commit_status": "PREPARED", "created_at": utc_now(),
            "synthetic": application["synthetic"],
            "synthetic_namespace": application["synthetic_namespace"],
            "fixture_id": application["fixture_id"], "version": 1,
        }
        created = await self.store.create("hiring_candidate_requests", request_id, row)
        existing = row if created else await self.store.get(
            "hiring_candidate_requests", request_id)
        if (not existing or existing.get("candidate_application_id") != application_id
                or existing.get("request_hash") != request_hash):
            return _error("idempotency_conflict", "Request id names other work.")
        if existing.get("commit_status") == "COMMITTED":
            return {"status": "success", "duplicate": True,
                    "candidate_request_id": request_id}
        projected_withdrawal = (
            request_kind == "WITHDRAWAL"
            and ((application.get("withdrawal") or {}).get(
                "candidate_request_id") == request_id))
        if projected_withdrawal:
            current = await self.store.get("hiring_candidate_requests", request_id)
            if current and current.get("commit_status") == "PREPARED":
                await self.store.compare_and_set(
                    "hiring_candidate_requests", request_id, int(current["version"]), {
                        "commit_status": "COMMITTED", "committed_at": utc_now()})
            await self.runtime.append_event(
                application["run_id"], event_kind="CANDIDATE_WITHDRAWAL_RECORDED",
                idempotency_key=f"candidate-request:{request_id}",
                safe_payload={"candidate_request_id": request_id,
                              "request_kind": request_kind}, actor_id=principal.actor_id)
            return {"status": "success", "duplicate": True,
                    "candidate_request_id": request_id,
                    "candidate_state": application["candidate_state"]}
        if int(application["version"]) != expected_application_version:
            return _error("version_conflict", "Application changed; reload the request.")
        if request_kind == "WITHDRAWAL":
            committed_app = await self.store.compare_and_set(
                "candidate_applications", application_id,
                expected_application_version, {
                    "candidate_state": CandidateState.WITHDRAWN.value,
                    "withdrawal": {"candidate_request_id": request_id,
                                   "recorded_at": utc_now()},
                    "updated_at": utc_now(),
                })
            if not committed_app:
                return _error("version_conflict", "Application changed concurrently.")
            if crash_point == "AFTER_WITHDRAWAL_PROJECTION":
                return _error("injected_crash",
                              "Synthetic crash injected after withdrawal projection.", 503)
        else:
            inbox_id = stable_id("hinbox", request_id, request_kind)
            await self.store.create("founder_inbox", inbox_id, {
                "schema_version": 2, "inbox_item_id": inbox_id,
                "workspace_id": principal.workspace_id,
                "founder_id": principal.workspace_id,
                "role_id": application["role_id"],
                "candidate_application_id": application_id,
                "kind": f"HIRING_{request_kind}_REQUEST", "status": "OPEN",
                "safe_reason": request_kind, "candidate_request_id": request_id,
                "created_at": utc_now(), "synthetic": application["synthetic"],
                "synthetic_namespace": application["synthetic_namespace"],
                "fixture_id": application["fixture_id"], "version": 1,
            })
        current = await self.store.get("hiring_candidate_requests", request_id)
        if current:
            await self.store.compare_and_set(
                "hiring_candidate_requests", request_id, int(current["version"]), {
                    "commit_status": "COMMITTED", "committed_at": utc_now()})
        await self.runtime.append_event(
            application["run_id"], event_kind=f"CANDIDATE_{request_kind}_RECORDED",
            idempotency_key=f"candidate-request:{request_id}",
            safe_payload={"candidate_request_id": request_id,
                          "request_kind": request_kind}, actor_id=principal.actor_id)
        return {"status": "success", "duplicate": not created,
                "candidate_request_id": request_id,
                "candidate_state": (CandidateState.WITHDRAWN.value
                                    if request_kind == "WITHDRAWAL"
                                    else application["candidate_state"])}


def _fixture_analyst_output(input_envelope: AnalystInput) -> dict[str, Any]:
    criteria = []
    for criterion in input_envelope.criteria:
        matching = [item for item in input_envelope.evidence
                    if criterion.criterion_id in item.criterion_ids
                    and item.content_risk is ContentRisk.CLEAR]
        citations = [{"evidence_id": item.evidence_id,
                      "evidence_hash": canonical_hash(item)} for item in matching]
        criteria.append(CriterionAssessment(
            criterion_id=criterion.criterion_id,
            status=(EvidenceStatus.SUPPORTED if citations else EvidenceStatus.UNKNOWN),
            citations=citations,
            contradictions=[], unknowns=([] if citations else [
                "No authorized job-related evidence was available for this criterion."]),
            summary=("Authorized evidence is cited below." if citations
                     else "This criterion remains unknown."),
        ).model_dump(mode="json"))
    return {
        "schema_version": 1, "invocation_id": input_envelope.invocation_id,
        "workspace_id": input_envelope.workspace_id,
        "role_id": input_envelope.role_id,
        "candidate_application_id": input_envelope.candidate_application_id,
        "policy_version_id": input_envelope.policy_version_id,
        "policy_hash": input_envelope.policy_hash, "criteria": criteria,
        "withheld_evidence_ids": [item.evidence_id for item in input_envelope.evidence
                                  if item.content_risk is not ContentRisk.CLEAR],
    }


def _state_for_decision(decision: DecisionKind) -> str:
    return {
        DecisionKind.ADVANCE: CandidateState.ADVANCED.value,
        DecisionKind.HOLD: CandidateState.HELD.value,
        DecisionKind.REQUEST_EVIDENCE: CandidateState.EVIDENCE_REQUESTED.value,
        DecisionKind.DECLINE: CandidateState.DECLINED.value,
        DecisionKind.REOPEN: CandidateState.AWAITING_HUMAN_DECISION.value,
        DecisionKind.CORRECT: CandidateState.AWAITING_HUMAN_DECISION.value,
    }[decision]


def datetime_from_iso(value: str):
    from datetime import datetime
    return datetime.fromisoformat(value)

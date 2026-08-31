"""Durable H5-H7 Hiring progression after an interview is booked.

The service owns facts and transitions, not hiring judgment.  It records
criterion-bound interview/reference evidence, appends Founder decisions,
binds an exact offer approval, accepts only a verified signature-adapter
event, and atomically creates one separate onboarding child run.  It never
scores, ranks, infers personality, provisions access, or treats email
sentiment as offer acceptance.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Any

from services.actor_identity import ActorPrincipal, authorize
from services.durable_store import AtomicMutation, DurableStore
from services.hiring_approval_service import request_approval
from services.hiring_contracts import (
    CandidateState,
    InterviewEvidenceInput,
    OfferApprovalInput,
    OfferDraftInput,
    OfferResponseKind,
    OfferSignatureEventInput,
    OnboardingItemResolutionInput,
    OnboardingPlanInput,
    OnboardingState,
    PostInterviewDecisionInput,
    PostInterviewDecisionKind,
    ReferenceEvidenceInput,
    ReferencePermissionInput,
    canonical_hash,
    stable_id,
    utc_now,
)
from services.hiring_evidence import redact_block
from services.hiring_workflow_adapter import (
    HiringWorkflowAdapter,
    founder_onboarding_provenance,
)
from services.workflow_contracts import RunKind
from services.workflow_runtime import WorkflowRuntime


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "http_status": http_status}


def _sha(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


class HiringPostInterviewService:
    """Implement H5-H7 with version-fenced, workspace-scoped records."""

    def __init__(self, store: DurableStore, runtime: WorkflowRuntime | None = None):
        self.store = store
        self.runtime = runtime or WorkflowRuntime(
            store, domain_adapter=HiringWorkflowAdapter())

    def release_projection(self) -> dict[str, Any]:
        """Return the executable H7 release posture without changing state."""
        cloud = bool(os.getenv("K_SERVICE"))
        enabled = os.getenv("HIRING_H5_H7_ENABLED", "0") == "1"
        kill = os.getenv("HIRING_H5_H7_KILL_SWITCH", "1") != "0"
        workspace = os.getenv("HIRING_H5_H7_WORKSPACE_ID", "")
        jurisdiction = os.getenv("HIRING_H5_H7_JURISDICTION_REVIEW_REF", "")
        signature = bool(os.getenv("HIRING_SIGNATURE_WEBHOOK_SECRET", ""))
        reviewers = os.getenv("HIRING_H5_H7_REVIEW_REF", "")
        blockers = []
        if cloud and not enabled:
            blockers.append("feature_disabled")
        if kill:
            blockers.append("kill_switch_active")
        if not workspace:
            blockers.append("workspace_not_allowlisted")
        if not jurisdiction:
            blockers.append("jurisdiction_review_missing")
        if not signature:
            blockers.append("signature_adapter_unconfigured")
        # The reviewed reference-contact resolver/outreach adapter is not in
        # this source yet.  Do not let an environment value manufacture a
        # production-ready claim for a missing executable boundary.
        blockers.append("reference_outreach_adapter_not_implemented")
        if not reviewers:
            blockers.append("qualified_review_missing")
        return {
            "status": "success", "stage": "H7_CONSTRAINED_PILOT",
            "enabled": enabled and not kill, "kill_switch": kill,
            "workspace_id": workspace, "blockers": blockers,
            "production_ready": cloud and not blockers,
            "forbidden": ["SCORING", "RANKING", "AUTO_DECLINE",
                          "BACKGROUND_CHECKS", "AUTO_PROVISIONING",
                          "BROAD_ATS_OR_JOB_BOARD_ROLLOUT"],
        }

    def _gate(self, principal: ActorPrincipal, operation: str = "read_candidate",
              *, fresh: bool = False) -> dict[str, Any]:
        allowed = authorize(principal, operation, require_fresh=fresh)
        if allowed.get("error"):
            return allowed
        if os.getenv("K_SERVICE"):
            release = self.release_projection()
            if not release["production_ready"]:
                return _error("h5_h7_not_released",
                              "Post-interview Hiring is not released for this workspace.",
                              403)
            if release["workspace_id"] != principal.workspace_id:
                return _error("workspace_not_allowlisted",
                              "Post-interview Hiring is not released for this workspace.",
                              403)
        return {"status": "success"}

    async def _application(self, principal: ActorPrincipal,
                           application_id: str) -> dict[str, Any] | None:
        row = await self.store.get("candidate_applications", application_id)
        if not row or row.get("workspace_id") != principal.workspace_id:
            return None
        return row

    async def _role_policy(self, application: dict[str, Any]) -> tuple[dict, dict] | None:
        role = await self.store.get("hiring_roles", str(application["role_id"]))
        policy = await self.store.get(
            "hiring_policy_versions", str((role or {}).get("current_policy_version_id") or ""))
        return (role, policy) if role and policy else None

    async def record_interview_evidence(
            self, *, principal: ActorPrincipal, application_id: str,
            payload: InterviewEvidenceInput) -> dict[str, Any]:
        gate = self._gate(principal, "submit_scorecard")
        if gate.get("error"):
            return gate
        app = await self._application(principal, application_id)
        if not app:
            return _error("application_not_found", "Application does not exist.", 404)
        if int(app["version"]) != payload.expected_application_version:
            return _error("version_conflict", "Application changed; reload first.")
        if app.get("candidate_state") not in {
                CandidateState.ADVANCED.value,
                CandidateState.WAITING_FOR_INTERVIEW.value}:
            return _error("interview_stage_invalid",
                          "Interview evidence requires an advanced candidate.")
        mandate_rows = await self.store.list(
            "hiring_coordination_mandates",
            filters={"workspace_id": principal.workspace_id,
                     "candidate_application_id": application_id}, limit=100)
        mandate = next((row for row in reversed(mandate_rows)
                        if str(row.get("current_event_id") or "") ==
                        payload.interview_event_id), None)
        if not mandate:
            return _error("interview_receipt_missing",
                          "Evidence must name the Hiring-owned interview receipt.")
        role_policy = await self._role_policy(app)
        if not role_policy:
            return _error("policy_missing", "Current role policy is unavailable.")
        role, policy = role_policy
        allowed = {str(item["criterion_id"])
                   for item in policy["contract"]["criteria"]}
        ids = [item.criterion_id for item in payload.criteria]
        if len(ids) != len(set(ids)) or not set(ids) <= allowed:
            return _error("criterion_scope_invalid",
                          "Interview evidence must use current approved criteria.", 400)
        if payload.transcript_artifact_id:
            artifact = await self.store.get(
                "hiring_candidate_artifacts", payload.transcript_artifact_id)
            if (not artifact or artifact.get("candidate_application_id") != application_id
                    or artifact.get("scope") != "HIRING_RESTRICTED"):
                return _error("transcript_scope_invalid",
                              "Consented transcript is not a restricted candidate artifact.")
        interview_id = stable_id("interview", application_id, payload.client_request_id)
        existing = await self.store.get("hiring_interviews", interview_id)
        request_hash = canonical_hash(payload)
        if existing:
            if existing.get("request_hash") != request_hash:
                return _error("idempotency_conflict", "Request id names different evidence.")
            return {"status": "success", "duplicate": True,
                    "interview_id": interview_id,
                    "candidate_state": existing["candidate_state_after"]}
        now = utc_now()
        row = {
            "schema_version": 1, "interview_id": interview_id,
            "workspace_id": principal.workspace_id,
            "role_id": app["role_id"], "candidate_application_id": application_id,
            "candidate_run_id": app["run_id"],
            "interview_event_id": payload.interview_event_id,
            "policy_version_id": policy["policy_version_id"],
            "policy_hash": policy["canonical_hash"],
            "criteria": [item.model_dump(mode="json") for item in payload.criteria],
            "founder_note": payload.founder_note,
            "transcript_consent": payload.transcript_consent,
            "transcript_artifact_id": payload.transcript_artifact_id,
            "forbidden_inferences": [], "score": None, "rank": None,
            "recommendation": None, "request_hash": request_hash,
            "actor_id": principal.actor_id,
            "candidate_state_after": CandidateState.AWAITING_INTERVIEW_DECISION.value,
            "created_at": now, "version": 1,
        }
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("hiring_interviews", interview_id, None, record=row),
            AtomicMutation("candidate_applications", application_id,
                           int(app["version"]), updates={
                               "candidate_state": CandidateState.AWAITING_INTERVIEW_DECISION.value,
                               "current_interview_id": interview_id,
                               "updated_at": now}),
        ))
        if not committed:
            return _error("version_conflict", "Interview evidence was not committed.")
        await self.runtime.append_event(
            str(app["run_id"]), event_kind="INTERVIEW_EVIDENCE_COMMITTED",
            idempotency_key=f"interview:{interview_id}",
            safe_payload={"interview_id": interview_id,
                          "criteria_count": len(payload.criteria)},
            actor_id=principal.actor_id)
        return {"status": "success", "duplicate": False,
                "interview_id": interview_id,
                "candidate_state": CandidateState.AWAITING_INTERVIEW_DECISION.value}

    async def record_post_interview_decision(
            self, *, principal: ActorPrincipal, application_id: str,
            payload: PostInterviewDecisionInput) -> dict[str, Any]:
        gate = self._gate(principal, "human_decision", fresh=True)
        if gate.get("error"):
            return gate
        app = await self._application(principal, application_id)
        if not app:
            return _error("application_not_found", "Application does not exist.", 404)
        if int(app["version"]) != payload.expected_application_version:
            return _error("version_conflict", "Application changed; reload first.")
        interview = await self.store.get("hiring_interviews", payload.interview_id)
        if not interview or interview.get("candidate_application_id") != application_id:
            return _error("interview_evidence_missing",
                          "A committed interview evidence record is required.")
        role_policy = await self._role_policy(app)
        if not role_policy:
            return _error("policy_missing", "Current role policy is unavailable.")
        _, policy = role_policy
        allowed_reasons = set(policy["contract"]["approved_reason_codes"])
        if not set(payload.reason_codes) <= allowed_reasons:
            return _error("reason_code_invalid", "Use an approved job-related reason.", 400)
        if payload.decision is PostInterviewDecisionKind.ADVANCE_TO_OFFER:
            reports = await self.store.list(
                "reference_checks", filters={"workspace_id": principal.workspace_id,
                                              "candidate_application_id": application_id},
                limit=100)
            if not any(row.get("status") == "REPORT_READY" for row in reports):
                return _error("reference_evidence_required",
                              "Final offer advancement requires reference evidence.")
        state = {
            PostInterviewDecisionKind.ADDITIONAL_INTERVIEW:
                CandidateState.WAITING_FOR_INTERVIEW.value,
            PostInterviewDecisionKind.ADVANCE_TO_REFERENCES:
                CandidateState.AWAITING_REFERENCE_PERMISSION.value,
            PostInterviewDecisionKind.ADVANCE_TO_OFFER:
                CandidateState.AWAITING_OFFER_APPROVAL.value,
            PostInterviewDecisionKind.HOLD: CandidateState.HELD.value,
            PostInterviewDecisionKind.DECLINE: CandidateState.DECLINED.value,
        }[payload.decision]
        decision_id = stable_id("decision", principal.workspace_id,
                                payload.client_request_id)
        existing = await self.store.get("hiring_decisions", decision_id)
        request_hash = canonical_hash(payload)
        if existing:
            if existing.get("request_hash") != request_hash:
                return _error("idempotency_conflict", "Request id names another decision.")
            return {"status": "success", "duplicate": True,
                    "decision_id": decision_id, "candidate_state": state}
        now = utc_now()
        row = {
            "schema_version": 2, "decision_id": decision_id,
            "workspace_id": principal.workspace_id, "role_id": app["role_id"],
            "candidate_application_id": application_id, "run_id": app["run_id"],
            "stage": "POST_INTERVIEW", "decision": payload.decision.value,
            "candidate_state_after": state,
            "policy_version_id": policy["policy_version_id"],
            "policy_hash": policy["canonical_hash"],
            "interview_id": payload.interview_id,
            "reason_codes": payload.reason_codes, "human_note": payload.note,
            "actor_id": principal.actor_id, "request_hash": request_hash,
            "commit_status": "COMMITTED", "created_at": now,
            "committed_at": now, "version": 1,
        }
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("hiring_decisions", decision_id, None, record=row),
            AtomicMutation("candidate_applications", application_id,
                           int(app["version"]), updates={
                               "candidate_state": state,
                               "current_decision_id": decision_id,
                               "updated_at": now}),
        ))
        if not committed:
            return _error("version_conflict", "Decision was not committed.")
        await self.runtime.append_event(
            str(app["run_id"]), event_kind="POST_INTERVIEW_DECISION_COMMITTED",
            idempotency_key=f"decision:{decision_id}",
            safe_payload={"decision_id": decision_id,
                          "decision": payload.decision.value},
            actor_id=principal.actor_id)
        return {"status": "success", "duplicate": False,
                "decision_id": decision_id, "candidate_state": state}

    async def record_reference_permission(
            self, *, principal: ActorPrincipal, application_id: str,
            payload: ReferencePermissionInput) -> dict[str, Any]:
        gate = self._gate(principal, "human_decision", fresh=True)
        if gate.get("error"):
            return gate
        app = await self._application(principal, application_id)
        if not app:
            return _error("application_not_found", "Application does not exist.", 404)
        if (app.get("candidate_state") !=
                CandidateState.AWAITING_REFERENCE_PERMISSION.value):
            return _error("reference_stage_invalid",
                          "References require a Founder decision and candidate permission.")
        if int(app["version"]) != payload.expected_application_version:
            return _error("version_conflict", "Application changed; reload first.")
        role_policy = await self._role_policy(app)
        if not role_policy:
            return _error("policy_missing", "Current role policy is unavailable.")
        _, policy = role_policy
        allowed = {str(item["criterion_id"])
                   for item in policy["contract"]["criteria"]}
        if not set(payload.criterion_ids) <= allowed:
            return _error("criterion_scope_invalid",
                          "Reference questions must use approved criteria.")
        reference_id = stable_id("reference", application_id,
                                 payload.client_request_id)
        response_token = secrets.token_urlsafe(32)
        row = {
            "schema_version": 1, "reference_check_id": reference_id,
            "workspace_id": principal.workspace_id, "role_id": app["role_id"],
            "candidate_application_id": application_id,
            "candidate_run_id": app["run_id"], "status": "AWAITING_RESPONSE",
            "permission_receipt_ref": payload.permission_receipt_ref,
            "permission_recorded_by": principal.actor_id,
            "reference_contact_ref": payload.reference_contact_ref,
            "reference_label": payload.reference_label,
            "approved_questions": payload.approved_questions,
            "criterion_ids": payload.criterion_ids,
            "response_token_sha256": _sha(response_token),
            "provider_thread_id": "", "report": None,
            "created_at": utc_now(), "version": 1,
        }
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("reference_checks", reference_id, None, record=row),
            AtomicMutation("candidate_applications", application_id,
                           int(app["version"]), updates={
                               "candidate_state": CandidateState.REFERENCES_IN_PROGRESS.value,
                               "current_reference_check_id": reference_id,
                               "updated_at": utc_now()}),
        ))
        if not committed:
            existing = await self.store.get("reference_checks", reference_id)
            if existing:
                return {"status": "success", "duplicate": True,
                        "reference_check_id": reference_id,
                        "response_token": None}
            return _error("version_conflict", "Reference permission was not committed.")
        return {"status": "success", "duplicate": False,
                "reference_check_id": reference_id,
                "response_token": response_token,
                "candidate_state": CandidateState.REFERENCES_IN_PROGRESS.value}

    async def record_reference_evidence(
            self, *, principal: ActorPrincipal | None, application_id: str,
            payload: ReferenceEvidenceInput) -> dict[str, Any]:
        check = await self.store.get("reference_checks", payload.reference_check_id)
        if principal:
            gate = self._gate(principal)
            if gate.get("error"):
                return gate
            app = await self._application(principal, application_id)
            workspace_id = principal.workspace_id
        else:
            app = await self.store.get("candidate_applications", application_id)
            workspace_id = str((app or {}).get("workspace_id") or "")
            if os.getenv("K_SERVICE"):
                release = self.release_projection()
                if (not release["production_ready"]
                        or release["workspace_id"] != workspace_id):
                    return _error("h5_h7_not_released",
                                  "Reference intake is not released.", 403)
        if (not app or not check
                or check.get("candidate_application_id") != application_id
                or check.get("workspace_id") != workspace_id):
            return _error("reference_check_not_found", "Reference check does not exist.", 404)
        if not hmac.compare_digest(str(check.get("response_token_sha256") or ""),
                                   _sha(payload.response_token)):
            return _error("reference_token_invalid", "Reference response is not authorized.", 403)
        if payload.criterion_id not in list(check.get("criterion_ids") or []):
            return _error("criterion_scope_invalid",
                          "Reference response is outside approved questions.")
        evidence_id = stable_id("ce", application_id,
                                payload.reference_check_id,
                                payload.client_request_id)
        if await self.store.get("candidate_evidence", evidence_id):
            return {"status": "success", "duplicate": True,
                    "evidence_id": evidence_id,
                    "candidate_state": CandidateState.AWAITING_FINAL_DECISION.value}
        redacted = redact_block(
            payload.claim,
            block_id=f"reference:{payload.reference_check_id}:{payload.client_request_id}",
        )
        source_hash = _sha(payload.claim)
        if redacted["content_risk"] != "CLEAR":
            committed = await self.store.compare_and_set(
                "reference_checks", payload.reference_check_id,
                int(check["version"]), {
                    "status": "RESPONSE_WITHHELD",
                    "withheld_reason": redacted["content_risk"],
                    "responded_at": utc_now(),
                })
            if not committed:
                return _error("version_conflict", "Reference response changed concurrently.")
            return _error(
                "reference_response_withheld",
                "The reference response needs human review before it can become evidence.",
                422,
            )
        evidence = {
            "schema_version": 1, "evidence_id": evidence_id,
            "workspace_id": workspace_id, "role_id": app["role_id"],
            "candidate_application_id": application_id,
            "source_artifact_id": payload.reference_check_id,
            "source_kind": "REFERENCE", "criterion_ids": [payload.criterion_id],
            "locator": {"page": None, "block": payload.source_locator},
            "quote": redacted["safe_text"], "normalized_fact": "",
            "authority": "REFERENCE_CLAIM", "verification": "UNVERIFIED",
            "content_risk": redacted["content_risk"], "source_sha256": source_hash,
            "redaction_policy_version": 1, "created_at": utc_now(),
            "evidence_hash": "", "version": 1,
        }
        evidence["evidence_hash"] = canonical_hash({
            key: value for key, value in evidence.items()
            if key not in {"evidence_hash", "version"}})
        report = {
            "criterion_id": payload.criterion_id,
            "evidence_id": evidence_id,
            "unknowns": payload.unknowns,
            "contradictions": payload.contradictions,
            "summary": "A permitted reference response is cited.",
        }
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("candidate_evidence", evidence_id, None, record=evidence),
            AtomicMutation("reference_checks", payload.reference_check_id,
                           int(check["version"]), updates={
                               "status": "REPORT_READY", "report": report,
                               "responded_at": utc_now()}),
            AtomicMutation("candidate_applications", application_id,
                           int(app["version"]), updates={
                               "candidate_state": CandidateState.AWAITING_FINAL_DECISION.value,
                               "updated_at": utc_now()}),
        ))
        if not committed:
            return _error("version_conflict", "Reference report was not committed.")
        return {"status": "success", "duplicate": False,
                "evidence_id": evidence_id,
                "candidate_state": CandidateState.AWAITING_FINAL_DECISION.value}

    async def prepare_offer(self, *, principal: ActorPrincipal, application_id: str,
                            payload: OfferDraftInput) -> dict[str, Any]:
        gate = self._gate(principal, "human_decision", fresh=True)
        if gate.get("error"):
            return gate
        app = await self._application(principal, application_id)
        if not app:
            return _error("application_not_found", "Application does not exist.", 404)
        if app.get("candidate_state") != CandidateState.AWAITING_OFFER_APPROVAL.value:
            return _error("offer_stage_invalid",
                          "A final Founder ADVANCE_TO_OFFER decision is required.")
        if int(app["version"]) != payload.expected_application_version:
            return _error("version_conflict", "Application changed; reload first.")
        artifact = await self.store.get("hiring_candidate_artifacts",
                                        payload.document_artifact_id)
        if artifact and (artifact.get("candidate_application_id") != application_id
                         or artifact.get("scope") != "HIRING_RESTRICTED"
                         or artifact.get("content_sha256") not in {
                             None, payload.document_sha256}):
            return _error("offer_document_invalid",
                          "Offer document must be a restricted candidate artifact.")
        if not artifact:
            created = await self.store.create(
                "hiring_candidate_artifacts", payload.document_artifact_id, {
                    "schema_version": 1,
                    "artifact_id": payload.document_artifact_id,
                    "workspace_id": principal.workspace_id,
                    "role_id": app["role_id"],
                    "candidate_application_id": application_id,
                    "scope": "HIRING_RESTRICTED",
                    "sensitivity": "HIRING_RESTRICTED",
                    "source_kind": "OFFER_DOCUMENT",
                    "content_type": "application/vnd.cofounder.offer+json",
                    "content_sha256": payload.document_sha256,
                    "created_at": utc_now(), "version": 1,
                })
            if not created:
                return _error("offer_document_invalid",
                              "Offer document changed while preparing.")
        offer_id = stable_id("offer", application_id, payload.client_request_id)
        offer_hash = canonical_hash(payload)
        existing = await self.store.get("offers", offer_id)
        if existing:
            if existing.get("offer_sha256") != offer_hash:
                return _error("idempotency_conflict", "Request id names another offer.")
            return {"status": "success", "duplicate": True,
                    "offer_id": offer_id, "approval_id": existing.get("approval_id")}
        role_policy = await self._role_policy(app)
        if not role_policy:
            return _error("policy_missing", "Current role policy is unavailable.")
        _, policy = role_policy
        exact = {
            "candidate_application_id": application_id,
            "offer_id": offer_id, "offer_sha256": offer_hash,
            "document_artifact_id": payload.document_artifact_id,
            "document_sha256": payload.document_sha256,
            "title": payload.title, "start_date": payload.start_date,
            "compensation": payload.compensation,
            "employment_terms": payload.employment_terms,
        }
        approval = await request_approval(
            principal=principal, run_id=str(app["run_id"]),
            role_id=str(app["role_id"]),
            policy_version_id=str(policy["policy_version_id"]),
            action_kind="HIRING_SEND_OFFER", exact_action=exact,
            client_request_id=f"offer_approval:{payload.client_request_id}",
            store=self.store)
        if approval.get("error"):
            return approval
        row = {
            "schema_version": 1, "offer_id": offer_id,
            "workspace_id": principal.workspace_id, "role_id": app["role_id"],
            "candidate_application_id": application_id,
            "candidate_run_id": app["run_id"], "status": "AWAITING_APPROVAL",
            "offer_version": 1, "offer_sha256": offer_hash,
            "exact_terms": exact, "approval_id": approval["approval_id"],
            "signature_event_id": None, "created_at": utc_now(), "version": 1,
        }
        if not await self.store.create("offers", offer_id, row):
            return _error("idempotency_conflict", "Offer changed while preparing.")
        return {"status": "success", "duplicate": False,
                "offer_id": offer_id, "offer_sha256": offer_hash,
                "approval_id": approval["approval_id"],
                "approval_status": approval["approval_status"]}

    async def approve_offer(self, *, principal: ActorPrincipal, application_id: str,
                            payload: OfferApprovalInput) -> dict[str, Any]:
        gate = self._gate(principal, "human_decision", fresh=True)
        if gate.get("error"):
            return gate
        app = await self._application(principal, application_id)
        offer = await self.store.get("offers", payload.offer_id)
        approval = await self.store.get("approvals", payload.approval_id)
        if (not app or not offer or offer.get("candidate_application_id") != application_id
                or not approval or approval.get("status") != "GRANTED"
                or approval.get("approval_id") != offer.get("approval_id")
                or (approval.get("exact_action") or {}).get("offer_sha256") !=
                offer.get("offer_sha256")):
            return _error("offer_approval_invalid",
                          "A fresh exact granted offer approval is required.")
        if offer.get("status") == "WAITING_FOR_RESPONSE":
            return {"status": "success", "duplicate": True,
                    "offer_id": payload.offer_id}
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("offers", payload.offer_id, int(offer["version"]), updates={
                "status": "WAITING_FOR_RESPONSE", "approved_at": utc_now(),
                "approved_by": principal.actor_id}),
            AtomicMutation("candidate_applications", application_id,
                           int(app["version"]), updates={
                               "candidate_state": CandidateState.WAITING_FOR_OFFER_RESPONSE.value,
                               "current_offer_id": payload.offer_id,
                               "updated_at": utc_now()}),
        ))
        if not committed:
            return _error("version_conflict", "Offer approval was not committed.")
        return {"status": "success", "duplicate": False,
                "offer_id": payload.offer_id,
                "candidate_state": CandidateState.WAITING_FOR_OFFER_RESPONSE.value}

    async def record_signature_event(
            self, *, principal: ActorPrincipal | None, application_id: str,
            payload: OfferSignatureEventInput, adapter_verified: bool) -> dict[str, Any]:
        if not adapter_verified:
            return _error("signature_event_unverified",
                          "Offer response requires a verified signature adapter event.", 403)
        offer = await self.store.get("offers", payload.offer_id)
        if principal:
            gate = self._gate(principal)
            if gate.get("error"):
                return gate
            app = await self._application(principal, application_id)
            workspace_id = principal.workspace_id
            actor_id = principal.actor_id
        else:
            app = await self.store.get("candidate_applications", application_id)
            workspace_id = str((app or {}).get("workspace_id") or "")
            actor_id = "signature_adapter"
            if os.getenv("K_SERVICE"):
                release = self.release_projection()
                if (not release["production_ready"]
                        or release["workspace_id"] != workspace_id):
                    return _error("h5_h7_not_released",
                                  "Offer response intake is not released.", 403)
        if (not app or not offer or offer.get("candidate_application_id") != application_id
                or offer.get("offer_sha256") != payload.offer_sha256):
            return _error("offer_event_invalid",
                          "Signature event does not match the current approved offer.")
        existing_event = str(offer.get("signature_event_id") or "")
        if existing_event:
            if existing_event == payload.provider_event_id:
                return {"status": "success", "duplicate": True,
                        "offer_id": payload.offer_id,
                        "candidate_state": app["candidate_state"],
                        "onboarding_run_id": app.get("onboarding_run_id")}
            return _error("offer_already_terminal", "Offer already has a response.")
        if offer.get("status") != "WAITING_FOR_RESPONSE":
            return _error("offer_event_invalid",
                          "Signature event does not match the current approved offer.")
        if payload.response is OfferResponseKind.DECLINED:
            committed = await self.store.atomic_compare_and_set((
                AtomicMutation("offers", payload.offer_id, int(offer["version"]), updates={
                    "status": "DECLINED", "signature_event_id": payload.provider_event_id,
                    "provider_envelope_sha256": payload.provider_envelope_sha256,
                    "responded_at": payload.occurred_at}),
                AtomicMutation("candidate_applications", application_id,
                               int(app["version"]), updates={
                                   "candidate_state": CandidateState.OFFER_DECLINED.value,
                                   "updated_at": utc_now()}),
            ))
            if not committed:
                return _error("version_conflict", "Offer response was not committed.")
            return {"status": "success", "duplicate": False,
                    "candidate_state": CandidateState.OFFER_DECLINED.value}
        onboarding_id = stable_id("onboarding", application_id, payload.offer_id)
        parent_run = await self.store.get("workflow_runs", str(app["run_id"]))
        if not parent_run:
            return _error("candidate_run_missing",
                          "Candidate workflow run does not exist.", 404)
        prepared = await self.runtime.prepare_run_creation(
            workspace_id=workspace_id,
            journey_id=str(parent_run["journey_id"]),
            run_kind=RunKind.ONBOARDING,
            idempotency_key=f"offer-accepted:{payload.offer_id}",
            domain_ref=onboarding_id,
            parent_run_id=str(app["run_id"]),
            originating_actor_id=actor_id,
            workflow_kind="hiring_onboarding:v1",
            provenance=founder_onboarding_provenance())
        if prepared.get("error"):
            return prepared
        onboarding_run_id = str(prepared["run_id"])
        onboarding = {
            "schema_version": 1, "onboarding_id": onboarding_id,
            "workspace_id": workspace_id, "role_id": app["role_id"],
            "candidate_application_id": application_id,
            "candidate_run_id": app["run_id"],
            "onboarding_run_id": onboarding_run_id,
            "offer_id": payload.offer_id,
            "state": OnboardingState.ONBOARDING_INTAKE.value,
            "start_date": offer["exact_terms"]["start_date"],
            "plan_id": None, "permissions_transferred": False,
            "created_at": utc_now(), "version": 1,
        }
        mutations = tuple(prepared["mutations"]) + (
            AtomicMutation("onboarding_runs", onboarding_id, None,
                           record=onboarding),
            AtomicMutation("offers", payload.offer_id, int(offer["version"]), updates={
                "status": "ACCEPTED", "signature_event_id": payload.provider_event_id,
                "provider_envelope_sha256": payload.provider_envelope_sha256,
                "responded_at": payload.occurred_at,
                "onboarding_run_id": onboarding_run_id}),
            AtomicMutation("candidate_applications", application_id,
                           int(app["version"]), updates={
                               "candidate_state": CandidateState.OFFER_ACCEPTED.value,
                               "onboarding_run_id": onboarding_run_id,
                               "onboarding_id": onboarding_id,
                               "updated_at": utc_now()}),
        )
        committed = await self.store.atomic_compare_and_set(mutations)
        if not committed:
            current = await self.store.get("candidate_applications", application_id)
            if current and current.get("onboarding_id") == onboarding_id:
                return {"status": "success", "duplicate": True,
                        "candidate_state": CandidateState.OFFER_ACCEPTED.value,
                        "onboarding_run_id": current.get("onboarding_run_id")}
            return _error("version_conflict", "Offer acceptance was not committed.")
        await self.runtime.publish_created_run(
            committed[("workflow_runs", onboarding_run_id)])
        return {"status": "success", "duplicate": False,
                "candidate_state": CandidateState.OFFER_ACCEPTED.value,
                "onboarding_id": onboarding_id,
                "onboarding_run_id": onboarding_run_id}

    async def prepare_onboarding_plan(
            self, *, principal: ActorPrincipal,
            payload: OnboardingPlanInput) -> dict[str, Any]:
        gate = self._gate(principal, "human_decision", fresh=True)
        if gate.get("error"):
            return gate
        rows = await self.store.list(
            "onboarding_runs", filters={"workspace_id": principal.workspace_id,
                                        "onboarding_run_id": payload.onboarding_run_id},
            limit=2)
        if len(rows) != 1:
            return _error("onboarding_not_found", "Onboarding run does not exist.", 404)
        onboarding = rows[0]
        if int(onboarding["version"]) != payload.expected_onboarding_version:
            return _error("version_conflict", "Onboarding changed; reload first.")
        plan_id = stable_id("onboarding_plan", payload.onboarding_run_id,
                            payload.client_request_id)
        exact = {"onboarding_run_id": payload.onboarding_run_id,
                 "plan_id": plan_id, "start_date": payload.start_date,
                 "items": [item.model_dump(mode="json") for item in payload.items]}
        approval = await request_approval(
            principal=principal, run_id=payload.onboarding_run_id,
            role_id=str(onboarding["onboarding_id"]), policy_version_id="onboarding_v1",
            action_kind="HIRING_APPROVE_ONBOARDING_PLAN", exact_action=exact,
            client_request_id=f"onboarding_approval:{payload.client_request_id}",
            store=self.store)
        if approval.get("error"):
            return approval
        mutations: list[AtomicMutation] = [
            AtomicMutation("onboarding_runs", str(onboarding["onboarding_id"]),
                           int(onboarding["version"]), updates={
                               "state": OnboardingState.AWAITING_PLAN_APPROVAL.value,
                               "plan_id": plan_id, "plan": exact,
                               "approval_id": approval["approval_id"],
                               "updated_at": utc_now()}),
        ]
        for item in payload.items:
            record = {
                **item.model_dump(mode="json"), "schema_version": 1,
                "workspace_id": principal.workspace_id,
                "onboarding_id": onboarding["onboarding_id"],
                "onboarding_run_id": payload.onboarding_run_id,
                "candidate_application_id": onboarding["candidate_application_id"],
                "plan_id": plan_id, "status": "PENDING",
                "external_effect_authorized": False,
                "created_at": utc_now(), "version": 1,
            }
            mutations.append(AtomicMutation("onboarding_items", item.item_id,
                                            None, record=record))
        committed = await self.store.atomic_compare_and_set(tuple(mutations))
        if not committed:
            return _error("version_conflict", "Onboarding plan was not committed.")
        return {"status": "success", "duplicate": False,
                "plan_id": plan_id, "approval_id": approval["approval_id"],
                "state": OnboardingState.AWAITING_PLAN_APPROVAL.value}

    async def approve_onboarding_plan(
            self, *, principal: ActorPrincipal, onboarding_run_id: str,
            approval_id: str) -> dict[str, Any]:
        gate = self._gate(principal, "human_decision", fresh=True)
        if gate.get("error"):
            return gate
        rows = await self.store.list(
            "onboarding_runs", filters={"workspace_id": principal.workspace_id,
                                        "onboarding_run_id": onboarding_run_id}, limit=2)
        approval = await self.store.get("approvals", approval_id)
        if (len(rows) != 1 or not approval or approval.get("status") != "GRANTED"
                or rows[0].get("approval_id") != approval_id
                or (approval.get("exact_action") or {}).get("plan_id") !=
                rows[0].get("plan_id")):
            return _error("onboarding_approval_invalid",
                          "A fresh exact onboarding-plan approval is required.")
        row = rows[0]
        committed = await self.store.compare_and_set(
            "onboarding_runs", str(row["onboarding_id"]), int(row["version"]), {
                "state": OnboardingState.PRE_START.value,
                "onboarding_scope_activated": True,
                "permissions_transferred": False,
                "approved_at": utc_now(), "approved_by": principal.actor_id})
        if not committed:
            return _error("version_conflict", "Onboarding plan was not approved.")
        return {"status": "success", "state": OnboardingState.PRE_START.value}

    async def resolve_onboarding_item(
            self, *, principal: ActorPrincipal,
            payload: OnboardingItemResolutionInput) -> dict[str, Any]:
        gate = self._gate(principal, "human_decision", fresh=True)
        if gate.get("error"):
            return gate
        item = await self.store.get("onboarding_items", payload.item_id)
        if (not item or item.get("workspace_id") != principal.workspace_id
                or item.get("onboarding_run_id") != payload.onboarding_run_id):
            return _error("onboarding_item_not_found", "Onboarding item does not exist.", 404)
        if int(item["version"]) != payload.expected_item_version:
            return _error("version_conflict", "Onboarding item changed; reload first.")
        if item.get("owner") == "NEW_HIRE":
            return _error("owner_action_required",
                          "The new hire must complete or waive this item.")
        committed = await self.store.compare_and_set(
            "onboarding_items", payload.item_id, int(item["version"]), {
                "status": payload.resolution, "resolution_note": payload.note,
                "resolved_by": principal.actor_id, "resolved_at": utc_now()})
        if not committed:
            return _error("version_conflict", "Onboarding item was not resolved.")
        return {"status": "success", "item_id": payload.item_id,
                "resolution": payload.resolution}

    async def projection(self, *, principal: ActorPrincipal,
                         application_id: str) -> dict[str, Any]:
        gate = self._gate(principal)
        if gate.get("error") and gate.get("error_code") != "h5_h7_not_released":
            return gate
        app = await self._application(principal, application_id)
        if not app:
            return _error("application_not_found", "Application does not exist.", 404)
        filters = {"workspace_id": principal.workspace_id,
                   "candidate_application_id": application_id}
        interviews = await self.store.list("hiring_interviews", filters=filters, limit=50)
        references = await self.store.list("reference_checks", filters=filters, limit=50)
        offers = await self.store.list("offers", filters=filters, limit=50)
        onboarding = await self.store.list("onboarding_runs", filters=filters, limit=10)
        items = await self.store.list("onboarding_items", filters=filters, limit=200)
        return {"status": "success", "candidate_state": app["candidate_state"],
                "interviews": interviews, "references": references,
                "offers": offers, "onboarding": onboarding,
                "onboarding_items": items,
                "release": self.release_projection()}


def verify_signature_webhook(raw_body: bytes, supplied: str) -> bool:
    """Verify the configured HMAC signature adapter; fail closed if absent."""
    secret = os.getenv("HIRING_SIGNATURE_WEBHOOK_SECRET", "")
    if not secret or not supplied:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied.removeprefix("sha256="))

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
from datetime import datetime, timedelta, timezone
from typing import Any

from services.actor_identity import ActorPrincipal, authorize
from services.capability_registry import require_controlled_action
from services.durable_store import AtomicMutation, DurableStore
from services.hiring_approval_service import (
    request_approval,
    validate_approval_claim,
)
from services.hiring_contracts import (
    CandidateState,
    InterviewEvidenceInput,
    OfferApprovalInput,
    OfferDraftInput,
    OfferResponseKind,
    OfferSignatureEventInput,
    OnboardingItemResolutionInput,
    OnboardingPlanInput,
    OnboardingProgressInput,
    OnboardingState,
    PostInterviewDecisionInput,
    PostInterviewDecisionKind,
    ReferenceContactInput,
    ReferenceEvidenceInput,
    ReferenceOutreachExecutionInput,
    ReferencePermissionInput,
    canonical_hash,
    stable_id,
    utc_now,
    verified_jurisdiction_binding,
)
from services.hiring_coordination import (
    GoogleHiringProviderAdapter,
    HiringProviderAdapter,
)
from services.hiring_evidence import redact_block
from services.hiring_reference_contacts import (
    ReferenceContactVault,
    production_reference_resolver_configured,
    reference_contact_vault,
    reference_response_secret,
    reference_response_token,
)
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


REFERENCE_COMPLETED_PRE_OFFER = "COMPLETED_PRE_OFFER"
REFERENCE_WAIVED_BY_FOUNDER = "WAIVED_BY_FOUNDER"
REFERENCE_REQUIRED_BEFORE_START = "REQUIRED_BEFORE_START"


class HiringPostInterviewService:
    """Implement H5-H7 with version-fenced, workspace-scoped records."""

    def __init__(self, store: DurableStore, runtime: WorkflowRuntime | None = None,
                 *, reference_vault: ReferenceContactVault | None = None,
                 provider_adapter: HiringProviderAdapter | None = None):
        self.store = store
        self.runtime = runtime or WorkflowRuntime(
            store, domain_adapter=HiringWorkflowAdapter())
        self.reference_vault = reference_vault or reference_contact_vault(store)
        self.provider_adapter = provider_adapter or GoogleHiringProviderAdapter()

    def release_projection(self) -> dict[str, Any]:
        """Return the executable H7 release posture without changing state."""
        cloud = bool(os.getenv("K_SERVICE"))
        enabled = os.getenv("HIRING_H5_H7_ENABLED", "0") == "1"
        kill = os.getenv("HIRING_H5_H7_KILL_SWITCH", "1") != "0"
        signature = bool(os.getenv("HIRING_SIGNATURE_WEBHOOK_SECRET", ""))
        reviewers = os.getenv("HIRING_H5_H7_REVIEW_REF", "")
        base_url = os.getenv("HIRING_PUBLIC_BASE_URL", "").rstrip("/")
        blockers = []
        if cloud and not enabled:
            blockers.append("feature_disabled")
        if kill:
            blockers.append("kill_switch_active")
        if not signature:
            blockers.append("signature_adapter_unconfigured")
        if not production_reference_resolver_configured():
            blockers.append("reference_contact_kms_unconfigured")
        if reference_response_secret(production_only=True) is None:
            blockers.append("reference_response_signing_unconfigured")
        if not base_url.startswith("https://"):
            blockers.append("public_base_url_unconfigured")
        if not reviewers:
            blockers.append("qualified_review_missing")
        return {
            "status": "success", "stage": "H7_CONSTRAINED_PILOT",
            "enabled": enabled and not kill, "kill_switch": kill,
            "workspace_admission": "AUTHENTICATED_ACTIVE_FOUNDER_MEMBERSHIP",
            "jurisdiction_source": "APPROVED_ROLE_PACKAGE",
            "jurisdiction_is_legal_advice": False,
            "jurisdiction_proves_legal_review": False,
            "blockers": blockers,
            "reference_outreach_adapter": "alex_mail_v1",
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

    async def _has_ready_reference(self, application: dict[str, Any]) -> bool:
        reports = await self.store.list(
            "reference_checks",
            filters={"workspace_id": application["workspace_id"],
                     "candidate_application_id":
                         application["candidate_application_id"]},
            limit=100,
        )
        return any(row.get("status") == "REPORT_READY" for row in reports)

    @staticmethod
    def _conditional_reference_open(application: dict[str, Any]) -> bool:
        return (
            application.get("reference_disposition") ==
            REFERENCE_REQUIRED_BEFORE_START
            and application.get("reference_stage") in {
                "AWAITING_PERMISSION", "REFERENCES_IN_PROGRESS"}
        )

    @classmethod
    def _reference_intake_allowed(cls, application: dict[str, Any]) -> bool:
        return (
            application.get("candidate_state") ==
            CandidateState.AWAITING_REFERENCE_PERMISSION.value
            or (
                cls._conditional_reference_open(application)
                and application.get("candidate_state") in {
                    CandidateState.AWAITING_OFFER_APPROVAL.value,
                    CandidateState.WAITING_FOR_OFFER_RESPONSE.value,
                    CandidateState.OFFER_ACCEPTED.value,
                }
            )
        )

    async def application_release_projection(
            self, application: dict[str, Any]) -> dict[str, Any]:
        """Return the exact package/workspace H7 binding for one application."""
        role_policy = await self._role_policy(application)
        binding = (verified_jurisdiction_binding(
            role=role_policy[0], policy=role_policy[1], application=application)
            if role_policy else None)
        blockers: list[str] = []
        if application.get("synthetic") is not False:
            blockers.append("real_application_required")
        if not role_policy:
            blockers.append("approved_role_package_missing")
        elif not binding:
            blockers.append("jurisdiction_binding_stale")
        return {
            "status": "success",
            "ready": not blockers,
            "workspace_admission": "AUTHENTICATED_ACTIVE_FOUNDER_MEMBERSHIP",
            "workspace_id": str(application.get("workspace_id") or ""),
            "jurisdiction_source": "APPROVED_ROLE_PACKAGE",
            "operating_jurisdiction": (
                binding["operating_jurisdiction"] if binding else None),
            "jurisdiction_binding_sha256": (
                binding["binding_sha256"] if binding else None),
            "policy_version_id": (
                binding["policy_version_id"] if binding else None),
            "legal_advice": False,
            "legal_review_claimed": False,
            "blockers": blockers,
        }

    async def _application_release_gate(
            self, application: dict[str, Any]) -> dict[str, Any]:
        if not os.getenv("K_SERVICE"):
            return {"status": "success"}
        projection = await self.application_release_projection(application)
        if not projection["ready"]:
            return _error(
                "h7_application_binding_invalid",
                "Post-interview Hiring requires the current approved role package.",
                403)
        return {"status": "success", "release": projection}

    async def _onboarding_release_gate(
            self, record: dict[str, Any]) -> dict[str, Any]:
        application = await self.store.get(
            "candidate_applications",
            str(record.get("candidate_application_id") or ""))
        if not application:
            return _error(
                "application_not_found",
                "The onboarding run is not linked to an application.", 404)
        return await self._application_release_gate(application)

    async def record_interview_evidence(
            self, *, principal: ActorPrincipal, application_id: str,
            payload: InterviewEvidenceInput) -> dict[str, Any]:
        gate = self._gate(principal, "submit_scorecard")
        if gate.get("error"):
            return gate
        app = await self._application(principal, application_id)
        if not app:
            return _error("application_not_found", "Application does not exist.", 404)
        application_gate = await self._application_release_gate(app)
        if application_gate.get("error"):
            return application_gate
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
        application_gate = await self._application_release_gate(app)
        if application_gate.get("error"):
            return application_gate
        if int(app["version"]) != payload.expected_application_version:
            return _error("version_conflict", "Application changed; reload first.")
        interview = await self.store.get("hiring_interviews", payload.interview_id)
        if not interview or interview.get("candidate_application_id") != application_id:
            return _error("interview_evidence_missing",
                          "A committed interview evidence record is required.")
        initial_decisions = {
            PostInterviewDecisionKind.ADDITIONAL_INTERVIEW,
            PostInterviewDecisionKind.ADVANCE_TO_REFERENCES,
            PostInterviewDecisionKind.ADVANCE_TO_OFFER_WITH_REFERENCE_WAIVER,
            PostInterviewDecisionKind.PREPARE_CONDITIONAL_OFFER,
            PostInterviewDecisionKind.HOLD,
            PostInterviewDecisionKind.DECLINE,
        }
        final_decisions = {
            PostInterviewDecisionKind.ADVANCE_TO_OFFER,
            PostInterviewDecisionKind.HOLD,
            PostInterviewDecisionKind.DECLINE,
        }
        current_state = str(app.get("candidate_state") or "")
        if not (
            (current_state == CandidateState.AWAITING_INTERVIEW_DECISION.value
             and payload.decision in initial_decisions)
            or (current_state == CandidateState.AWAITING_FINAL_DECISION.value
                and payload.decision in final_decisions)
        ):
            return _error(
                "post_interview_stage_invalid",
                "This Founder decision is not available in the current stage.",
            )
        role_policy = await self._role_policy(app)
        if not role_policy:
            return _error("policy_missing", "Current role policy is unavailable.")
        _, policy = role_policy
        allowed_reasons = set(policy["contract"]["approved_reason_codes"])
        if not set(payload.reason_codes) <= allowed_reasons:
            return _error("reason_code_invalid", "Use an approved job-related reason.", 400)
        if payload.decision is PostInterviewDecisionKind.ADVANCE_TO_OFFER:
            if not await self._has_ready_reference(app):
                return _error("reference_evidence_required",
                              "Final offer advancement requires reference evidence.")
        state = {
            PostInterviewDecisionKind.ADDITIONAL_INTERVIEW:
                CandidateState.WAITING_FOR_INTERVIEW.value,
            PostInterviewDecisionKind.ADVANCE_TO_REFERENCES:
                CandidateState.AWAITING_REFERENCE_PERMISSION.value,
            PostInterviewDecisionKind.ADVANCE_TO_OFFER_WITH_REFERENCE_WAIVER:
                CandidateState.AWAITING_OFFER_APPROVAL.value,
            PostInterviewDecisionKind.PREPARE_CONDITIONAL_OFFER:
                CandidateState.AWAITING_OFFER_APPROVAL.value,
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
        reference_disposition = {
            PostInterviewDecisionKind.ADVANCE_TO_REFERENCES: "PENDING_PRE_OFFER",
            PostInterviewDecisionKind.ADVANCE_TO_OFFER_WITH_REFERENCE_WAIVER:
                REFERENCE_WAIVED_BY_FOUNDER,
            PostInterviewDecisionKind.PREPARE_CONDITIONAL_OFFER:
                REFERENCE_REQUIRED_BEFORE_START,
            PostInterviewDecisionKind.ADVANCE_TO_OFFER:
                REFERENCE_COMPLETED_PRE_OFFER,
        }.get(payload.decision)
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
            "reference_disposition": reference_disposition,
            "actor_id": principal.actor_id, "request_hash": request_hash,
            "commit_status": "COMMITTED", "created_at": now,
            "committed_at": now, "version": 1,
        }
        application_updates: dict[str, Any] = {
            "candidate_state": state,
            "current_decision_id": decision_id,
            "updated_at": now,
        }
        if reference_disposition:
            application_updates["reference_disposition"] = reference_disposition
        if payload.decision is PostInterviewDecisionKind.ADVANCE_TO_REFERENCES:
            application_updates["reference_stage"] = "AWAITING_PERMISSION"
        elif payload.decision is PostInterviewDecisionKind.PREPARE_CONDITIONAL_OFFER:
            application_updates["reference_stage"] = "AWAITING_PERMISSION"
        elif payload.decision in {
                PostInterviewDecisionKind.ADVANCE_TO_OFFER_WITH_REFERENCE_WAIVER,
                PostInterviewDecisionKind.ADVANCE_TO_OFFER}:
            application_updates["reference_stage"] = (
                "WAIVED" if payload.decision is
                PostInterviewDecisionKind.ADVANCE_TO_OFFER_WITH_REFERENCE_WAIVER
                else "REPORT_READY"
            )
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("hiring_decisions", decision_id, None, record=row),
            AtomicMutation("candidate_applications", application_id,
                           int(app["version"]), updates=application_updates),
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
                "decision_id": decision_id, "candidate_state": state,
                "reference_disposition": reference_disposition}

    async def record_reference_permission(
            self, *, principal: ActorPrincipal, application_id: str,
            payload: ReferencePermissionInput) -> dict[str, Any]:
        gate = self._gate(principal, "human_decision", fresh=True)
        if gate.get("error"):
            return gate
        app = await self._application(principal, application_id)
        if not app:
            return _error("application_not_found", "Application does not exist.", 404)
        application_gate = await self._application_release_gate(app)
        if application_gate.get("error"):
            return application_gate
        if not self._reference_intake_allowed(app):
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
        contact = await self.store.get(
            "hiring_reference_contacts", payload.reference_contact_ref)
        if (not contact or contact.get("workspace_id") != principal.workspace_id
                or contact.get("role_id") != app.get("role_id")
                or contact.get("candidate_application_id") != application_id
                or contact.get("retention_status") != "ACTIVE"):
            return _error(
                "reference_contact_not_found",
                "Store the candidate-provided reference contact before approval.", 404)
        reference_id = stable_id("reference", application_id,
                                 payload.client_request_id)
        token_nonce = secrets.token_urlsafe(18)
        response_token = reference_response_token(reference_id, token_nonce)
        if not response_token:
            return _error(
                "reference_response_signing_unavailable",
                "Reference response authorization is unavailable.", 503)
        base_url = os.getenv("HIRING_PUBLIC_BASE_URL", "").rstrip("/")
        if not base_url and not os.getenv("K_SERVICE"):
            base_url = "http://127.0.0.1:8090"
        if not base_url:
            return _error("public_base_url_unconfigured",
                          "Reference response URL is unavailable.", 503)
        response_path = f"/hiring/reference-response/{reference_id}"
        exact = {
            "schema_version": 1, "workspace_id": principal.workspace_id,
            "role_id": app["role_id"],
            "candidate_application_id": application_id,
            "candidate_run_id": app["run_id"],
            "policy_version_id": policy["policy_version_id"],
            "action_kind": "HIRING_SEND_REFERENCE_REQUEST",
            "connector_id": "alex_mail",
            "reference_check_id": reference_id,
            "reference_contact_ref": payload.reference_contact_ref,
            "masked_recipient": contact["masked_email"],
            "destination_ids": [payload.reference_contact_ref],
            "reference_label": payload.reference_label,
            "approved_questions": payload.approved_questions,
            "criterion_ids": payload.criterion_ids,
            "response_path": response_path,
            "response_token_sha256": _sha(response_token),
            "subject": "Reference request from Ruhu",
            "body_template": "reference_request_v1",
        }
        approval = await request_approval(
            principal=principal, run_id=str(app["run_id"]),
            role_id=str(app["role_id"]),
            policy_version_id=str(policy["policy_version_id"]),
            action_kind="HIRING_SEND_REFERENCE_REQUEST", exact_action=exact,
            client_request_id=f"reference_outreach:{payload.client_request_id}",
            store=self.store)
        if approval.get("error"):
            return approval
        row = {
            "schema_version": 1, "reference_check_id": reference_id,
            "workspace_id": principal.workspace_id, "role_id": app["role_id"],
            "candidate_application_id": application_id,
            "candidate_run_id": app["run_id"],
            "status": "AWAITING_OUTREACH_APPROVAL",
            "permission_receipt_ref": payload.permission_receipt_ref,
            "permission_recorded_by": principal.actor_id,
            "reference_contact_ref": payload.reference_contact_ref,
            "reference_label": payload.reference_label,
            "approved_questions": payload.approved_questions,
            "criterion_ids": payload.criterion_ids,
            "response_token_sha256": _sha(response_token),
            "response_token_nonce": token_nonce,
            "response_token_expires_at": (
                datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "outreach_exact": exact,
            "approval_id": approval["approval_id"],
            "action_id": None, "provider_thread_id": "",
            "provider_message_id": "", "report": None,
            "created_at": utc_now(), "version": 1,
        }
        conditional = (
            app.get("reference_disposition") == REFERENCE_REQUIRED_BEFORE_START
        )
        application_updates: dict[str, Any] = {
            "current_reference_check_id": reference_id,
            "reference_stage": "REFERENCES_IN_PROGRESS",
            "updated_at": utc_now(),
        }
        if not conditional:
            application_updates["candidate_state"] = (
                CandidateState.REFERENCES_IN_PROGRESS.value
            )
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("reference_checks", reference_id, None, record=row),
            AtomicMutation("candidate_applications", application_id,
                           int(app["version"]), updates=application_updates),
        ))
        if not committed:
            existing = await self.store.get("reference_checks", reference_id)
            if existing:
                return {"status": "success", "duplicate": True,
                        "reference_check_id": reference_id,
                        "approval_id": existing.get("approval_id"),
                        "response_token": None}
            return _error("version_conflict", "Reference permission was not committed.")
        return {"status": "success", "duplicate": False,
                "reference_check_id": reference_id,
                "approval_id": approval["approval_id"],
                "approval_status": approval["approval_status"],
                "response_token": None,
                "candidate_state": (
                    app["candidate_state"] if conditional else
                    CandidateState.REFERENCES_IN_PROGRESS.value),
                "reference_stage": "REFERENCES_IN_PROGRESS"}

    async def store_reference_contact(
            self, *, principal: ActorPrincipal, application_id: str,
            payload: ReferenceContactInput) -> dict[str, Any]:
        """Encrypt a candidate-provided reference identity for exact outreach."""
        gate = self._gate(principal, "human_decision", fresh=True)
        if gate.get("error"):
            return gate
        app = await self._application(principal, application_id)
        if not app:
            return _error("application_not_found", "Application does not exist.", 404)
        application_gate = await self._application_release_gate(app)
        if application_gate.get("error"):
            return application_gate
        if not self._reference_intake_allowed(app):
            return _error("reference_stage_invalid",
                          "References require a Founder decision and candidate permission.")
        if not self.reference_vault:
            return _error("reference_contact_encryption_unavailable",
                          "Reference contact encryption is unavailable.", 503)
        return await self.reference_vault.store_contact(
            principal=principal, application=app, name=payload.name,
            email=payload.email, label=payload.label,
            client_request_id=payload.client_request_id)

    async def execute_reference_outreach(
            self, *, principal: ActorPrincipal, application_id: str,
            payload: ReferenceOutreachExecutionInput) -> dict[str, Any]:
        """Consume one exact approval and send one idempotent reference request."""
        gate = self._gate(principal, "resolve_approval", fresh=True)
        if gate.get("error"):
            return gate
        app = await self._application(principal, application_id)
        check = await self.store.get("reference_checks", payload.reference_check_id)
        if (not app or not check
                or check.get("candidate_application_id") != application_id
                or check.get("workspace_id") != principal.workspace_id):
            return _error("reference_check_not_found", "Reference check does not exist.", 404)
        application_gate = await self._application_release_gate(app)
        if application_gate.get("error"):
            return application_gate
        if check.get("status") == "AWAITING_RESPONSE":
            return {"status": "success", "duplicate": True,
                    "reference_check_id": payload.reference_check_id,
                    "action_id": check.get("action_id")}
        if check.get("status") in {"OUTREACH_UNCERTAIN", "EXECUTING"}:
            return _error("reference_outreach_reconciliation_required",
                          "Reconcile the existing reference outreach; do not retry.")
        if check.get("status") != "AWAITING_OUTREACH_APPROVAL":
            return _error("reference_outreach_not_ready",
                          "Reference outreach is not awaiting approval.")
        exact = dict(check.get("outreach_exact") or {})
        validated = await validate_approval_claim(
            principal=principal, approval_id=payload.approval_id,
            run_id=str(app["run_id"]),
            policy_version_id=str(exact.get("policy_version_id") or ""),
            action_kind="HIRING_SEND_REFERENCE_REQUEST", exact_action=exact,
            store=self.store, require_fresh=True)
        if validated.get("error"):
            return validated
        if not self.reference_vault:
            return _error("reference_contact_encryption_unavailable",
                          "Reference contact encryption is unavailable.", 503)
        resolved = await self.reference_vault.reveal_for_outreach(
            principal=principal,
            contact_id=str(check["reference_contact_ref"]), application=app)
        if resolved.get("error"):
            return resolved
        preflight = await self.provider_adapter.preflight(
            workspace_id=principal.workspace_id, connector_id="alex_mail")
        if preflight.get("status") != "success":
            return preflight
        capability = require_controlled_action(
            "HIRING_SEND_REFERENCE_REQUEST", "alex_mail")
        action_id = stable_id("hiringaction", principal.workspace_id,
                              canonical_hash(exact))
        current = await self.store.get("external_actions", action_id)
        if current:
            return await self._existing_reference_action(check, current)
        token = reference_response_token(
            payload.reference_check_id, str(check["response_token_nonce"]))
        if not token or _sha(token) != check.get("response_token_sha256"):
            return _error("reference_response_signing_unavailable",
                          "Reference response authorization is unavailable.", 503)
        base_url = os.getenv("HIRING_PUBLIC_BASE_URL", "").rstrip("/")
        if not base_url and not os.getenv("K_SERVICE"):
            base_url = "http://127.0.0.1:8090"
        response_url = (
            f"{base_url}{exact['response_path']}?token={token}"
            if base_url else "")
        if not response_url:
            return _error("public_base_url_unconfigured",
                          "Reference response URL is unavailable.", 503)
        contact = resolved["contact"]
        question_lines = "\n".join(
            f"{index}. {question}" for index, question in enumerate(
                check["approved_questions"], start=1))
        transient_exact = {
            **exact, "recipients": [contact["email"]],
            "payload": {
                "subject": exact["subject"],
                "body": (
                    f"Hello {contact['name']},\n\n"
                    "The candidate named you as a professional reference and "
                    "permitted this job-related request. Please answer only the "
                    "approved questions below.\n\n"
                    f"{question_lines}\n\nSecure response: {response_url}\n\n"
                    "Alex\nAI co-founder, Ruhu"),
            },
        }
        now = utc_now()
        approval = validated["approval"]
        provider_request_id = stable_id("providerrequest", action_id, "1")
        action_row = {
            "schema_version": 2, "action_id": action_id,
            "workspace_id": principal.workspace_id,
            "founder_id": principal.workspace_id, "actor_id": principal.actor_id,
            "approval_domain": "HIRING", "action_domain": "HIRING",
            "application_id": application_id,
            "candidate_application_id": application_id,
            "session_id": str(app["run_id"]), "run_id": str(app["run_id"]),
            "role_id": str(app["role_id"]), "domain_ref": application_id,
            "action_kind": "HIRING_SEND_REFERENCE_REQUEST",
            "connector_id": "alex_mail",
            "connection_id": preflight.get("connection_id"),
            "capability_id": capability.capability_id,
            "capability_version": capability.semantic_version,
            "idempotency_key": action_id, "request_hash": canonical_hash(exact),
            "approval_id": payload.approval_id,
            "claim_id": stable_id("claim", payload.approval_id, action_id),
            "authorization_kind": "EXACT_FOUNDER_APPROVAL",
            "reference_check_id": payload.reference_check_id,
            "exact_action": exact, "status": "PREPARED",
            "provider_started_at": None,
            "provider_request_id": provider_request_id,
            "provider_effect_id": None, "result_ref": {},
            "error_code": None, "uncertainty_reason": None,
            "synthetic": False, "created_at": now, "updated_at": now,
            "version": 1,
        }
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("external_actions", action_id, None, record=action_row),
            AtomicMutation("reference_checks", payload.reference_check_id,
                           int(check["version"]), updates={
                               "status": "APPROVED", "action_id": action_id,
                               "approved_at": now, "updated_at": now}),
            AtomicMutation("approvals", payload.approval_id,
                           int(approval["version"]), updates={
                               "status": "CLAIMED",
                               "claim_id": action_row["claim_id"],
                               "claimed_action_id": action_id,
                               "claimed_by_actor_id": principal.actor_id,
                               "claimed_at": now, "updated_at": now}),
        ))
        if not committed:
            return _error("concurrency_conflict",
                          "Reference approval changed concurrently.")
        action = committed[("external_actions", action_id)]
        approval_now = committed[("approvals", payload.approval_id)]
        check_now = committed[("reference_checks", payload.reference_check_id)]
        started_at = utc_now()
        started = await self.store.atomic_compare_and_set((
            AtomicMutation("external_actions", action_id, int(action["version"]),
                           updates={"status": "EXECUTING",
                                    "provider_started_at": started_at,
                                    "updated_at": started_at}),
            AtomicMutation("reference_checks", payload.reference_check_id,
                           int(check_now["version"]),
                           updates={"status": "EXECUTING",
                                    "updated_at": started_at}),
            AtomicMutation("approvals", payload.approval_id,
                           int(approval_now["version"]), updates={
                               "status": "CONSUMED", "consumed_at": started_at,
                               "terminal_action_id": action_id,
                               "updated_at": started_at}),
        ))
        if not started:
            return _error("concurrency_conflict",
                          "Reference outreach start changed concurrently.")
        action = started[("external_actions", action_id)]
        try:
            result = await self.provider_adapter.execute(
                workspace_id=principal.workspace_id,
                action_kind="HIRING_SEND_REFERENCE_REQUEST",
                exact_action=transient_exact, action_id=action_id,
                provider_request_id=provider_request_id)
        except Exception:
            result = {"status": "uncertain", "error": True,
                      "uncertainty_reason": "provider_call_interrupted"}
        return await self._finish_reference_action(
            check_id=payload.reference_check_id, action=action, result=result)

    async def reconcile_reference_outreach(
            self, *, principal: ActorPrincipal, application_id: str,
            reference_check_id: str) -> dict[str, Any]:
        """Resolve one uncertain send from provider evidence without retrying it."""
        gate = self._gate(principal, "resolve_approval", fresh=True)
        if gate.get("error"):
            return gate
        app = await self._application(principal, application_id)
        check = await self.store.get("reference_checks", reference_check_id)
        action = await self.store.get(
            "external_actions", str((check or {}).get("action_id") or ""))
        if (not app or not check or not action
                or check.get("candidate_application_id") != application_id
                or action.get("workspace_id") != principal.workspace_id
                or action.get("status") not in {"EXECUTING", "UNCERTAIN"}):
            return _error("reference_outreach_reconciliation_required",
                          "No uncertain reference outreach exists.", 404)
        application_gate = await self._application_release_gate(app)
        if application_gate.get("error"):
            return application_gate
        result = await self.provider_adapter.reconcile(
            workspace_id=principal.workspace_id, action=action)
        if result.get("status") not in {"success", "failed"}:
            return _error("reference_outreach_reconciliation_required",
                          "Provider evidence remains inconclusive.", 503)
        return await self._finish_reference_action(
            check_id=reference_check_id, action=action, result=result)

    async def _existing_reference_action(
            self, check: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        if action.get("request_hash") != canonical_hash(check.get("outreach_exact") or {}):
            return _error("idempotency_conflict", "Reference action binding changed.")
        if action.get("status") == "SUCCEEDED":
            return {"status": "success", "duplicate": True,
                    "reference_check_id": check["reference_check_id"],
                    "action_id": action["action_id"]}
        if action.get("status") in {"EXECUTING", "UNCERTAIN"}:
            return _error("reference_outreach_reconciliation_required",
                          "Reconcile the existing reference outreach; do not retry.")
        return _error("reference_outreach_failed",
                      "The exact reference outreach failed and needs a new approval.")

    async def _finish_reference_action(
            self, *, check_id: str, action: dict[str, Any],
            result: dict[str, Any]) -> dict[str, Any]:
        status = str(result.get("status") or "")
        terminal = ("SUCCEEDED" if status == "success" else
                    "FAILED" if status == "failed" or (
                        result.get("error") and status != "uncertain") else
                    "UNCERTAIN")
        check_status = {
            "SUCCEEDED": "AWAITING_RESPONSE",
            "FAILED": "OUTREACH_FAILED",
            "UNCERTAIN": "OUTREACH_UNCERTAIN",
        }[terminal]
        current_action = await self.store.get("external_actions", action["action_id"])
        current_check = await self.store.get("reference_checks", check_id)
        if not current_action or not current_check:
            return _error("reference_outreach_not_found", "Reference outreach vanished.")
        result_ref = dict(result.get("result_ref") or {})
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("external_actions", action["action_id"],
                           int(current_action["version"]), updates={
                               "status": terminal,
                               "provider_effect_id": result.get("provider_effect_id"),
                               "result_ref": result_ref,
                               "error_code": result.get("error_code"),
                               "uncertainty_reason": result.get("uncertainty_reason"),
                               "updated_at": utc_now()}),
            AtomicMutation("reference_checks", check_id,
                           int(current_check["version"]), updates={
                               "status": check_status,
                               "provider_message_id": result.get("provider_effect_id") or "",
                               "provider_thread_id": result_ref.get(
                                   "provider_thread_id", ""),
                               "outreach_sent_at": utc_now() if terminal == "SUCCEEDED" else None,
                               "updated_at": utc_now()}),
        ))
        if not committed:
            return _error("concurrency_conflict",
                          "Reference outreach receipt changed concurrently.")
        return {
            "status": "success" if terminal == "SUCCEEDED" else "error",
            "error": terminal != "SUCCEEDED", "terminal_status": terminal,
            "reference_check_id": check_id, "action_id": action["action_id"],
            "error_code": (None if terminal == "SUCCEEDED" else
                           result.get("error_code") or
                           "reference_outreach_reconciliation_required"),
        }

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
                application_release = (
                    await self.application_release_projection(app) if app else None)
                if (not release["production_ready"]
                        or not application_release
                        or not application_release["ready"]):
                    return _error("h5_h7_not_released",
                                  "Reference intake is not released.", 403)
        if (not app or not check
                or check.get("candidate_application_id") != application_id
                or check.get("workspace_id") != workspace_id):
            return _error("reference_check_not_found", "Reference check does not exist.", 404)
        application_gate = await self._application_release_gate(app)
        if application_gate.get("error"):
            return application_gate
        if check.get("status") not in {"AWAITING_RESPONSE", "REPORT_READY"}:
            return _error("reference_outreach_incomplete",
                          "Reference evidence requires a completed outreach receipt.", 409)
        if str(check.get("response_token_expires_at") or "") <= utc_now():
            return _error("reference_token_expired",
                          "Reference response authorization has expired.", 403)
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
                    "candidate_state": app["candidate_state"],
                    "reference_stage": app.get("reference_stage")}
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
        conditional = (
            app.get("reference_disposition") == REFERENCE_REQUIRED_BEFORE_START
        )
        application_updates: dict[str, Any] = {
            "reference_stage": "REPORT_READY",
            "updated_at": utc_now(),
        }
        if conditional:
            application_updates["reference_condition_satisfied_at"] = utc_now()
        else:
            application_updates.update({
                "candidate_state": CandidateState.AWAITING_FINAL_DECISION.value,
                "reference_disposition": REFERENCE_COMPLETED_PRE_OFFER,
            })
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("candidate_evidence", evidence_id, None, record=evidence),
            AtomicMutation("reference_checks", payload.reference_check_id,
                           int(check["version"]), updates={
                               "status": "REPORT_READY", "report": report,
                               "responded_at": utc_now()}),
            AtomicMutation("candidate_applications", application_id,
                           int(app["version"]), updates=application_updates),
        ))
        if not committed:
            return _error("version_conflict", "Reference report was not committed.")
        return {"status": "success", "duplicate": False,
                "evidence_id": evidence_id,
                "candidate_state": (
                    app["candidate_state"] if conditional else
                    CandidateState.AWAITING_FINAL_DECISION.value),
                "reference_stage": "REPORT_READY"}

    async def reference_response_projection(
            self, *, reference_check_id: str, response_token: str) -> dict[str, Any]:
        """Return only approved questions after validating the opaque bearer token."""
        check = await self.store.get("reference_checks", reference_check_id)
        if not check or check.get("status") not in {"AWAITING_RESPONSE", "REPORT_READY"}:
            return _error("reference_check_not_found",
                          "Reference request does not exist.", 404)
        application = await self.store.get(
            "candidate_applications",
            str(check.get("candidate_application_id") or ""))
        application_gate = await self._application_release_gate(application or {})
        if application_gate.get("error"):
            return application_gate
        if str(check.get("response_token_expires_at") or "") <= utc_now():
            return _error("reference_token_expired",
                          "Reference response authorization has expired.", 403)
        if not hmac.compare_digest(
                str(check.get("response_token_sha256") or ""),
                _sha(response_token)):
            return _error("reference_token_invalid",
                          "Reference response is not authorized.", 403)
        return {
            "status": "success", "reference_check_id": reference_check_id,
            "reference_label": check.get("reference_label"),
            "approved_questions": list(check.get("approved_questions") or []),
            "criterion_ids": list(check.get("criterion_ids") or []),
            "response_status": check.get("status"),
        }

    async def prepare_offer(self, *, principal: ActorPrincipal, application_id: str,
                            payload: OfferDraftInput) -> dict[str, Any]:
        gate = self._gate(principal, "human_decision", fresh=True)
        if gate.get("error"):
            return gate
        app = await self._application(principal, application_id)
        if not app:
            return _error("application_not_found", "Application does not exist.", 404)
        application_gate = await self._application_release_gate(app)
        if application_gate.get("error"):
            return application_gate
        if app.get("candidate_state") != CandidateState.AWAITING_OFFER_APPROVAL.value:
            return _error("offer_stage_invalid",
                          "A final Founder ADVANCE_TO_OFFER decision is required.")
        if int(app["version"]) != payload.expected_application_version:
            return _error("version_conflict", "Application changed; reload first.")
        expected_reference_condition = str(
            app.get("reference_disposition") or REFERENCE_COMPLETED_PRE_OFFER
        )
        if expected_reference_condition not in {
                REFERENCE_COMPLETED_PRE_OFFER,
                REFERENCE_WAIVED_BY_FOUNDER,
                REFERENCE_REQUIRED_BEFORE_START}:
            return _error(
                "reference_disposition_invalid",
                "The Founder must choose how references relate to this offer.",
            )
        if payload.reference_condition != expected_reference_condition:
            return _error(
                "offer_reference_condition_mismatch",
                "The offer must preserve the committed Founder reference decision.",
            )
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
            "reference_condition": payload.reference_condition,
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
            "reference_condition": payload.reference_condition,
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
                or not approval or approval.get("approval_id") != offer.get("approval_id")):
            return _error("offer_approval_invalid",
                          "A fresh exact granted offer approval is required.")
        application_gate = await self._application_release_gate(app)
        if application_gate.get("error"):
            return application_gate
        if (offer.get("status") == "WAITING_FOR_RESPONSE"
                and approval.get("status") == "CONSUMED"
                and app.get("current_offer_id") == payload.offer_id):
            return {"status": "success", "duplicate": True,
                    "offer_id": payload.offer_id}
        claim = await validate_approval_claim(
            principal=principal,
            approval_id=payload.approval_id,
            run_id=str(app["run_id"]),
            policy_version_id=str(approval.get("policy_version_id") or ""),
            action_kind="HIRING_SEND_OFFER",
            exact_action=dict(offer.get("exact_terms") or {}),
            store=self.store,
            require_fresh=True,
        )
        if claim.get("error"):
            return _error("offer_approval_invalid",
                          "A fresh exact granted offer approval is required.")
        now = utc_now()
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("offers", payload.offer_id, int(offer["version"]), updates={
                "status": "WAITING_FOR_RESPONSE", "approved_at": now,
                "approved_by": principal.actor_id}),
            AtomicMutation("candidate_applications", application_id,
                           int(app["version"]), updates={
                               "candidate_state": CandidateState.WAITING_FOR_OFFER_RESPONSE.value,
                               "current_offer_id": payload.offer_id,
                               "updated_at": now}),
            AtomicMutation("approvals", payload.approval_id,
                           int(approval["version"]), updates={
                               "status": "CONSUMED",
                               "consumed_by_actor_id": principal.actor_id,
                               "consumed_at": now, "updated_at": now}),
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
                application_release = (
                    await self.application_release_projection(app) if app else None)
                if (not release["production_ready"]
                        or not application_release
                        or not application_release["ready"]):
                    return _error("h5_h7_not_released",
                                  "Offer response intake is not released.", 403)
        if (not app or not offer or offer.get("candidate_application_id") != application_id
                or offer.get("offer_sha256") != payload.offer_sha256):
            return _error("offer_event_invalid",
                          "Signature event does not match the current approved offer.")
        application_gate = await self._application_release_gate(app)
        if application_gate.get("error"):
            return application_gate
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
            "reference_condition": offer.get(
                "reference_condition", REFERENCE_COMPLETED_PRE_OFFER),
            "reference_condition_satisfied": (
                offer.get("reference_condition") != REFERENCE_REQUIRED_BEFORE_START
                or await self._has_ready_reference(app)
            ),
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
        application_gate = await self._onboarding_release_gate(onboarding)
        if application_gate.get("error"):
            return application_gate
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
        if (len(rows) != 1 or not approval or rows[0].get("approval_id") != approval_id):
            return _error("onboarding_approval_invalid",
                          "A fresh exact onboarding-plan approval is required.")
        row = rows[0]
        application_gate = await self._onboarding_release_gate(row)
        if application_gate.get("error"):
            return application_gate
        if (row.get("state") == OnboardingState.PRE_START.value
                and approval.get("status") == "CONSUMED"):
            return {"status": "success", "duplicate": True,
                    "state": OnboardingState.PRE_START.value}
        claim = await validate_approval_claim(
            principal=principal,
            approval_id=approval_id,
            run_id=onboarding_run_id,
            policy_version_id="onboarding_v1",
            action_kind="HIRING_APPROVE_ONBOARDING_PLAN",
            exact_action=dict(row.get("plan") or {}),
            store=self.store,
            require_fresh=True,
        )
        if claim.get("error"):
            return _error("onboarding_approval_invalid",
                          "A fresh exact onboarding-plan approval is required.")
        now = utc_now()
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("onboarding_runs", str(row["onboarding_id"]),
                           int(row["version"]), updates={
                               "state": OnboardingState.PRE_START.value,
                               "onboarding_scope_activated": True,
                               "permissions_transferred": False,
                               "approved_at": now,
                               "approved_by": principal.actor_id}),
            AtomicMutation("approvals", approval_id, int(approval["version"]), updates={
                "status": "CONSUMED",
                "consumed_by_actor_id": principal.actor_id,
                "consumed_at": now, "updated_at": now}),
        ))
        if not committed:
            return _error("version_conflict", "Onboarding plan was not approved.")
        return {"status": "success", "duplicate": False,
                "state": OnboardingState.PRE_START.value}

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
        application_gate = await self._onboarding_release_gate(item)
        if application_gate.get("error"):
            return application_gate
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

    async def progress_onboarding(
            self, *, principal: ActorPrincipal,
            payload: OnboardingProgressInput) -> dict[str, Any]:
        """Advance one approved onboarding run without inferring completion.

        Start-date transitions are server-derived from the approved ISO date.
        First-day/week completion and final completion remain explicit Founder
        acts.  Final completion is refused until every checklist item has an
        authoritative completion or waiver and every related external action
        is terminal and reconciled.
        """
        gate = self._gate(principal, "human_decision", fresh=True)
        if gate.get("error"):
            return gate
        rows = await self.store.list(
            "onboarding_runs",
            filters={"workspace_id": principal.workspace_id,
                     "onboarding_run_id": payload.onboarding_run_id},
            limit=2)
        if len(rows) != 1:
            return _error("onboarding_not_found", "Onboarding run does not exist.", 404)
        row = rows[0]
        application_gate = await self._onboarding_release_gate(row)
        if application_gate.get("error"):
            return application_gate
        if int(row["version"]) != payload.expected_onboarding_version:
            return _error("version_conflict", "Onboarding run changed; reload first.")
        current = str(row.get("state") or "")
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        transition = payload.transition
        target = ""
        reference_condition_cleared = False
        if transition == "SYNC_START_DATE":
            if current not in {
                    OnboardingState.PRE_START.value,
                    OnboardingState.WAITING_FOR_START_DATE.value}:
                return _error("onboarding_transition_invalid",
                              "Start-date sync is not valid in the current state.")
            if row.get("reference_condition") == REFERENCE_REQUIRED_BEFORE_START:
                application = await self.store.get(
                    "candidate_applications",
                    str(row.get("candidate_application_id") or ""),
                )
                if not application or not await self._has_ready_reference(application):
                    return _error(
                        "references_required_before_start",
                        "The conditional offer requires completed reference evidence "
                        "before the start date can activate.",
                    )
                reference_condition_cleared = True
            target = (OnboardingState.FIRST_DAY.value
                      if today >= str(row.get("start_date") or "")
                      else OnboardingState.WAITING_FOR_START_DATE.value)
        elif transition == "COMPLETE_FIRST_DAY":
            if current != OnboardingState.FIRST_DAY.value:
                return _error("onboarding_transition_invalid",
                              "First-day completion requires the first-day state.")
            target = OnboardingState.FIRST_WEEK.value
        elif transition == "REQUEST_COMPLETION_REVIEW":
            if current != OnboardingState.FIRST_WEEK.value:
                return _error("onboarding_transition_invalid",
                              "Completion review follows the first-week state.")
            target = OnboardingState.AWAITING_COMPLETION_REVIEW.value
        elif transition == "COMPLETE_ONBOARDING":
            if current != OnboardingState.AWAITING_COMPLETION_REVIEW.value:
                return _error("onboarding_transition_invalid",
                              "Final completion requires Founder completion review.")
            items = await self.store.list(
                "onboarding_items",
                filters={"workspace_id": principal.workspace_id,
                         "onboarding_run_id": payload.onboarding_run_id},
                limit=200)
            unresolved = [str(item["item_id"]) for item in items
                          if item.get("status") not in {"COMPLETE", "WAIVED"}]
            if unresolved:
                return _error("onboarding_items_incomplete",
                              "Every onboarding item must be completed or waived.")
            actions = await self.store.list(
                "external_actions",
                filters={"workspace_id": principal.workspace_id}, limit=1000)
            open_actions = [str(action["action_id"]) for action in actions
                            if action.get("onboarding_run_id") ==
                            payload.onboarding_run_id
                            and action.get("status") not in {
                                "SUCCEEDED", "FAILED", "CANCELLED"}]
            if open_actions:
                return _error("onboarding_actions_unreconciled",
                              "Onboarding external actions must be reconciled.")
            target = OnboardingState.COMPLETE.value
        else:  # pragma: no cover - Pydantic closes this input.
            return _error("onboarding_transition_invalid",
                          "Onboarding transition is not supported.")
        request_hash = canonical_hash(payload.model_dump(mode="json"))
        existing_hash = str(row.get("last_progress_request_hash") or "")
        if existing_hash == request_hash:
            return {"status": "success", "duplicate": True,
                    "onboarding_run_id": payload.onboarding_run_id,
                    "state": current}
        updates: dict[str, Any] = {
            "state": target, "last_progress_request_hash": request_hash,
            "last_progress_note": payload.note,
            "last_progressed_by": principal.actor_id,
            "last_progressed_at": now.isoformat(), "updated_at": now.isoformat(),
        }
        if reference_condition_cleared:
            updates.update({
                "reference_condition_satisfied": True,
                "reference_condition_satisfied_at": now.isoformat(),
            })
        if target == OnboardingState.WAITING_FOR_START_DATE.value:
            updates["start_date_wait_active"] = True
        elif target == OnboardingState.FIRST_DAY.value:
            updates.update({"start_date_wait_active": False,
                            "first_day_started_at": now.isoformat()})
        elif target == OnboardingState.COMPLETE.value:
            updates.update({"completed_at": now.isoformat(),
                            "completion_reviewed_by": principal.actor_id,
                            "retention_transition_at": now.isoformat(),
                            "retention_status": "POST_ONBOARDING"})
        committed = await self.store.compare_and_set(
            "onboarding_runs", str(row["onboarding_id"]),
            int(row["version"]), updates)
        if not committed:
            return _error("version_conflict", "Onboarding progress was not committed.")
        return {"status": "success", "duplicate": False,
                "onboarding_run_id": payload.onboarding_run_id,
                "state": target}

    async def projection(self, *, principal: ActorPrincipal,
                         application_id: str) -> dict[str, Any]:
        gate = self._gate(principal)
        if gate.get("error") and gate.get("error_code") != "h5_h7_not_released":
            return gate
        app = await self._application(principal, application_id)
        if not app:
            return _error("application_not_found", "Application does not exist.", 404)
        application_release = await self.application_release_projection(app)
        filters = {"workspace_id": principal.workspace_id,
                   "candidate_application_id": application_id}
        interviews = await self.store.list("hiring_interviews", filters=filters, limit=50)
        references = await self.store.list("reference_checks", filters=filters, limit=50)
        offers = await self.store.list("offers", filters=filters, limit=50)
        onboarding = await self.store.list("onboarding_runs", filters=filters, limit=10)
        items = await self.store.list("onboarding_items", filters=filters, limit=200)
        return {"status": "success", "candidate_state": app["candidate_state"],
                "reference_disposition": app.get("reference_disposition"),
                "reference_stage": app.get("reference_stage"),
                "reference_condition_satisfied_at":
                    app.get("reference_condition_satisfied_at"),
                "interviews": interviews, "references": references,
                "offers": offers, "onboarding": onboarding,
                "onboarding_items": items,
                "release": {
                    **self.release_projection(),
                    "application": application_release,
                }}


def verify_signature_webhook(raw_body: bytes, supplied: str) -> bool:
    """Verify the configured HMAC signature adapter; fail closed if absent."""
    secret = os.getenv("HIRING_SIGNATURE_WEBHOOK_SECRET", "")
    if not secret or not supplied:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied.removeprefix("sha256="))

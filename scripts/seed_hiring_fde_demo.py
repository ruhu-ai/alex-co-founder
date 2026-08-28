"""Seed Ruhu's durable synthetic Forward Deployment Engineer walkthrough.

This is an operator-run demo seeder, not a production candidate-import path.
It requires the deployment-owned synthetic fixture allowlist and reuses the
same H0-H3 services/records as the founder UI. It never sends email, opens a
browser, or deletes candidate data.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from typing import Any

from services import hiring_policy_service
from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_store import production_store
from services.hiring_activation import require_synthetic
from services.hiring_approval_service import request_approval, resolve_approval
from services.hiring_contracts import DecisionKind, HumanDecisionInput, RoleContract
from services.hiring_data_rights import HiringDataRightsService
from services.hiring_identity_vault import CandidateIdentityVault, fixture_key_wrapper
from services.hiring_mailbox import HiringMailboxService
from services.hiring_service import HiringService
from services.hiring_workflow_adapter import HiringWorkflowAdapter
from services.workflow_runtime import WorkflowRuntime

FIXTURE_ID = "fixture_ruhu_fde_walkthrough"
NAMESPACE = "synthetic_hiring_ruhu_fde"
WORKSPACE_ID = os.environ.get("HIRING_WORKSPACE_ID", "workspace_founder")
ACTOR_ID = os.environ.get("HIRING_OWNER_ACTOR_ID", "member_owner")


def _guard() -> dict[str, Any]:
    return {"synthetic": True, "synthetic_namespace": NAMESPACE,
            "fixture_id": FIXTURE_ID}


def _contract() -> RoleContract:
    return RoleContract.model_validate({
        "schema_version": 1, "company_name": "Ruhu",
        "role_title": "Forward Deployment Engineer",
        "role_summary": (
            "Lead secure customer deployments from discovery through durable "
            "production outcomes, including incident learning."),
        "headcount_target": 1, "target_date": "2027-01-30",
        "location_envelope": ["Lagos", "Remote in Nigeria"],
        "compensation_envelope": "Founder-reviewed band",
        "criteria": [
            {"criterion_id": "criterion_customer_deployment",
             "label": "Customer deployment delivery",
             "description": "Led a real customer deployment from discovery to launch with accountable delivery scope.",
             "evidence_examples": ["Named deployment and direct responsibilities"],
             "approved_question_ids": ["question_customer_deployment"]},
            {"criterion_id": "criterion_incident_response",
             "label": "Incident response",
             "description": "Handled a material customer incident constructively.",
             "evidence_examples": ["Specific incident, actions and learning"],
             "approved_question_ids": ["question_incident_response"]},
        ],
        "interview_plan": [
            {"question_id": "question_customer_deployment",
             "criterion_id": "criterion_customer_deployment",
             "text": "Describe one deployment and your direct responsibilities.",
             "rubric": ["Concrete scope", "Outcome evidence"]},
            {"question_id": "question_incident_response",
             "criterion_id": "criterion_incident_response",
             "text": "Describe a customer incident and what you did.",
             "rubric": ["Direct actions", "Learning"]},
        ],
        "public_job_description": (
            "Join Ruhu to deliver secure customer deployments from discovery "
            "through launch. Apply by email."),
        "approved_reason_codes": ["CRITERION_EVIDENCE_SUFFICIENT",
                                  "MORE_JOB_EVIDENCE_REQUIRED"],
        "prohibited_criteria": ["Protected characteristics", "Culture fit", "Prestige"],
        "notice_policy_id": "notice_synthetic_v1",
        "retention_policy_id": "retention_synthetic_v1",
        "jurisdiction_policy_id": "jurisdiction_synthetic_v1",
    })


def _fixture_message(message_id: str, *, route_id: str, labels: list[str],
                     authentic: bool, raw_to: str,
                     blocks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "message_id": message_id, "thread_id": f"thread_{message_id}",
        "connection_id": "ruhu_fde_mail_connection",
        "provider_internal_date": "2026-08-26T08:00:00+00:00",
        "provider_label_ids": labels, "provider_route_id": route_id,
        "delivery_authentic": authentic, "raw_recipient_header": raw_to,
        "identity": {"name": "Synthetic FDE Candidate", "email": "fde.candidate@example.test"},
        "blocks": blocks or [],
        "source_sha256": "sha256:" + hashlib.sha256(message_id.encode()).hexdigest(),
    }


async def _principal(store) -> ActorPrincipal:
    members = await store.list(
        "workspace_members",
        filters={"actor_id": ACTOR_ID, "workspace_id": WORKSPACE_ID}, limit=2)
    member = members[0] if len(members) == 1 else None
    if (not member or member.get("workspace_id") != WORKSPACE_ID
            or member.get("role") != WorkspaceRole.OWNER.value):
        raise SystemExit(
            "Provision an OWNER membership first with scripts/seed_hiring_membership.py.")
    return ActorPrincipal(
        actor_id=ACTOR_ID, workspace_id=WORKSPACE_ID, role=WorkspaceRole.OWNER,
        role_grants=frozenset(), candidate_assignments=frozenset(),
        interview_assignments=frozenset(), session_auth_time=int(time.time()),
        membership_version=int(member.get("version", 1)))


async def _grant_if_pending(principal: ActorPrincipal, approval_id: str, store) -> None:
    approval = await store.get("approvals", approval_id)
    if approval and approval.get("status") == "PENDING":
        result = await resolve_approval(
            principal=principal, approval_id=approval_id, decision="GRANT", store=store)
        if result.get("error"):
            raise RuntimeError(result["message"])


async def main() -> None:
    if os.environ.get("HIRING_ENABLE_SYNTHETIC_DEMO") != "1":
        raise SystemExit("Set HIRING_ENABLE_SYNTHETIC_DEMO=1 for this synthetic-only seeder.")
    allowed = {value.strip() for value in os.environ.get(
        "HIRING_SYNTHETIC_FIXTURE_IDS", "").split(",") if value.strip()}
    if FIXTURE_ID not in allowed or require_synthetic(_guard()).get("error"):
        raise SystemExit(f"Allowlist {FIXTURE_ID!r} before running the demo seeder.")
    raw_key = os.environ.get("HIRING_SYNTHETIC_ENCRYPTION_KEY", "")
    if not raw_key:
        raise SystemExit("HIRING_SYNTHETIC_ENCRYPTION_KEY is required for the fixture vault.")
    store = production_store()
    principal = await _principal(store)
    key = hashlib.sha256(raw_key.encode()).digest()
    wrap, unwrap = fixture_key_wrapper(key)
    vault = CandidateIdentityVault(wrap_key=wrap, unwrap_key=unwrap,
                                   dedup_key=key, store=store)
    runtime = WorkflowRuntime(store, domain_adapter=HiringWorkflowAdapter())
    hiring = HiringService(store=store, identity_vault=vault, runtime=runtime)
    mailbox = HiringMailboxService(hiring, store=store)
    contract = _contract()

    created = await hiring.create_role(
        principal=principal, contract=contract, client_request_id="ruhu_fde_demo_role",
        synthetic_guard=_guard())
    if created.get("error"):
        raise RuntimeError(created["message"])
    role = created["role"]
    proposed = await hiring_policy_service.propose_policy(
        principal=principal, role_id=role["role_id"], contract=contract,
        change_reason="Reviewed synthetic Ruhu FDE demo policy.",
        client_request_id="ruhu_fde_demo_policy", store=store)
    if proposed.get("error"):
        raise RuntimeError(proposed["message"])
    exact = {"policy_version_id": proposed["policy_version_id"],
             "policy_hash": proposed["canonical_hash"]}
    approval = await request_approval(
        principal=principal, run_id=role["run_id"], role_id=role["role_id"],
        policy_version_id=proposed["policy_version_id"],
        action_kind="ACTIVATE_ROLE_POLICY", exact_action=exact,
        client_request_id="ruhu_fde_demo_policy_approval", store=store)
    if approval.get("error"):
        raise RuntimeError(approval["message"])
    await _grant_if_pending(principal, approval["approval_id"], store)
    role = await store.get("hiring_roles", role["role_id"])
    activated = await hiring_policy_service.approve_policy(
        principal=principal, role_id=role["role_id"],
        policy_version_id=proposed["policy_version_id"],
        expected_role_version=role["version"], approval_id=approval["approval_id"], store=store)
    if activated.get("error"):
        raise RuntimeError(activated["message"])

    role = await store.get("hiring_roles", role["role_id"])
    publication = await hiring.record_publication(
        principal=principal, role_id=role["role_id"], destination="LINKEDIN",
        public_url="https://www.linkedin.com/jobs/view/ruhu-fde-synthetic",
        expected_version=role["version"], client_request_id="ruhu_fde_demo_publication",
        attestation="Founder manually published the exact approved Ruhu FDE package.")
    if publication.get("error"):
        raise RuntimeError(publication["message"])
    role = await store.get("hiring_roles", role["role_id"])
    binding = await mailbox.configure_binding(
        role_id=role["role_id"], connection_id="ruhu_fde_mail_connection",
        provider_route_id="ruhu_fde_trusted_route", provider_label_id="Label_ruhu_fde",
        expected_role_version=role["version"], synthetic_guard=_guard())
    if binding.get("error"):
        raise RuntimeError(binding["message"])
    alias = role["publication_package"]["application_address"]
    for message in (
        _fixture_message("ruhu_fde_positive_probe", route_id="ruhu_fde_trusted_route",
                         labels=["Label_ruhu_fde"], authentic=True, raw_to=alias),
        _fixture_message("ruhu_fde_forged_probe", route_id="forged_route",
                         labels=[], authentic=True, raw_to=alias),
    ):
        result = await mailbox.seed_fixture_message(message=message, synthetic_guard=_guard())
        if result.get("error"):
            raise RuntimeError(result["message"])
    role = await store.get("hiring_roles", role["role_id"])
    for probe_kind, message_id in (("POSITIVE", "ruhu_fde_positive_probe"),
                                   ("NEGATIVE_FORGED_HEADER", "ruhu_fde_forged_probe")):
        probe = await mailbox.record_probe(role_id=role["role_id"], fixture_message_id=message_id,
                                           probe_kind=probe_kind,
                                           expected_role_version=role["version"])
        if probe.get("error"):
            raise RuntimeError(probe["message"])
        role = await store.get("hiring_roles", role["role_id"])

    application_message = _fixture_message(
        "ruhu_fde_application", route_id="ruhu_fde_trusted_route",
        labels=["Label_ruhu_fde", "ARCHIVED"], authentic=True, raw_to=alias,
        blocks=[
            {"block_id": "fde_delivery", "source_kind": "RESUME",
             "text": "Led an enterprise customer deployment from discovery through launch.",
             "criterion_ids": ["criterion_customer_deployment"]},
            {"block_id": "fde_incident", "source_kind": "RESUME",
             "text": "Coordinated a production incident and documented the customer follow-up.",
             "criterion_ids": ["criterion_incident_response"]},
            {"block_id": "fde_protected", "source_kind": "RESUME",
             "text": "Date of birth: 1990-01-01", "criterion_ids": ["criterion_incident_response"]},
        ])
    forged = _fixture_message("ruhu_fde_forged_mail", route_id="forged_route",
                              labels=["Label_ruhu_fde"], authentic=False, raw_to=alias)
    for message in (application_message, forged):
        result = await mailbox.seed_fixture_message(message=message, synthetic_guard=_guard())
        if result.get("error"):
            raise RuntimeError(result["message"])
    state = await store.get("hiring_mailbox_state", "ruhu_fde_mail_connection")
    batch = await mailbox.create_fetch_batch(
        connection_id="ruhu_fde_mail_connection", old_cursor=str(state["cursor"]),
        proposed_cursor=f"{state['cursor']}:ruhu_fde_demo",
        message_ids=["ruhu_fde_application", "ruhu_fde_forged_mail"],
        batch_key="ruhu_fde_demo_delivery", synthetic_guard=_guard())
    if batch.get("error"):
        raise RuntimeError(batch["message"])
    processed = await mailbox.process_batch(batch["batch_id"])
    if processed.get("error"):
        raise RuntimeError(processed["message"])
    applications = await store.list("candidate_applications", filters={
        "workspace_id": WORKSPACE_ID, "role_id": role["role_id"]}, limit=10)
    application = next(item for item in applications
                       if item.get("provider_message_id") == "ruhu_fde_application")
    if not application.get("current_decision_id"):
        evidence = await store.list("candidate_evidence", filters={
            "workspace_id": WORKSPACE_ID,
            "candidate_application_id": application["candidate_application_id"]}, limit=500)
        decision = await hiring.record_human_decision(
            principal=principal, application_id=application["candidate_application_id"],
            decision_input=HumanDecisionInput(
                decision=DecisionKind.HOLD,
                reason_codes=["MORE_JOB_EVIDENCE_REQUIRED"],
                evidence_ids_reviewed=[item["evidence_id"] for item in evidence],
                assessment_id=application["current_assessment_id"],
                expected_application_version=application["version"],
                client_request_id="ruhu_fde_demo_hold",
                note="Founder requests a concrete incident-response example."))
        if decision.get("error"):
            raise RuntimeError(decision["message"])
    application = await store.get("candidate_applications", application["candidate_application_id"])
    if application.get("candidate_state") != "WITHDRAWN":
        withdrawal = await hiring.record_candidate_request(
            principal=principal, application_id=application["candidate_application_id"],
            request_kind="WITHDRAWAL", safe_note="Synthetic candidate withdrew by email.",
            client_request_id="ruhu_fde_demo_withdrawal",
            expected_application_version=application["version"])
        if withdrawal.get("error"):
            raise RuntimeError(withdrawal["message"])
    application = await store.get("candidate_applications", application["candidate_application_id"])
    rights = HiringDataRightsService(identity_vault=vault, store=store)
    plan = await rights.deletion_plan(principal=principal,
                                      application_id=application["candidate_application_id"])
    if plan.get("error"):
        raise RuntimeError(plan["message"])
    await runtime.append_event(
        application["run_id"], event_kind="DEMO_DATA_RIGHTS_DRY_RUN",
        idempotency_key="ruhu-fde-demo-data-rights",
        safe_payload={"delete_count": plan["delete_count"],
                      "inventory_hash": plan["inventory_hash"]}, actor_id=ACTOR_ID)
    print({"status": "success", "role_id": role["role_id"],
           "candidate_application_id": application["candidate_application_id"],
           "candidate_state": application["candidate_state"],
           "data_rights_delete_count": plan["delete_count"]})


if __name__ == "__main__":
    asyncio.run(main())

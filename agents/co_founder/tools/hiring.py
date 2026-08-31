"""Safe normal-chat and Live entry into internal Hiring role drafts."""

from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext

from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_store import production_store
from services.hiring_contracts import canonical_hash, stable_id
from services.hiring_role_writer import write_structured_founder_role_package

from .. import state_schema as ss
from ._common import actor_id, workspace_id


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


def _session_id(tool_context: ToolContext) -> str:
    session = getattr(tool_context, "session", None)
    return str(getattr(session, "id", "")
               or getattr(tool_context, "session_id", "") or "")


async def _scoped_founder(tool_context: ToolContext) -> ActorPrincipal | dict[str, Any]:
    """Re-read membership; projected identities never supply role authority."""
    workspace = workspace_id(tool_context)
    actor = actor_id(tool_context)
    if not workspace or not actor:
        return _error(
            "interactive_founder_required",
            "A verified Founder session is required to create a hiring draft.")
    rows = await production_store().list(
        "workspace_members",
        filters={"workspace_id": workspace, "actor_id": actor,
                 "status": "ACTIVE"},
        limit=2,
    )
    if len(rows) != 1 or rows[0].get("role") != WorkspaceRole.FOUNDER.value:
        return _error(
            "interactive_founder_required",
            "The active workspace membership does not authorize this draft.")
    member = rows[0]
    return ActorPrincipal(
        actor_id=actor,
        workspace_id=workspace,
        role=WorkspaceRole.FOUNDER,
        session_auth_time=0,
        membership_version=int(member.get("version") or 1),
        principal_kind="AGENT_SESSION",
        membership_id=str(member.get("membership_id") or member.get("id") or ""),
    )


async def prepare_hiring_role_brief(
        company_name: str, role_title: str, role_summary: str,
        headcount_target: int, target_date: str, location: str,
        work_arrangement: str, employment_type: str,
        compensation_envelope: str, required_criteria: list[str],
        responsibilities: list[str], success_outcomes: list[str],
        preferred_criteria: list[str], relevant_experience: list[str],
        benefits: list[str], hiring_process: list[str],
        application_instructions: str,
        equal_opportunity_statement: str, accessibility_statement: str,
        public_job_description: str,
        tool_context: ToolContext) -> dict[str, Any]:
    """Prepare an exact internal role package without creating a role or run.

    Collect missing facts from the founder before calling. Present every field
    returned in ``role_description`` plus the scorecard and interview plan, then
    ask whether to create exactly this internal DRAFT. Call create_hiring_draft
    only after a later explicit confirmation. Never infer inputs from memory,
    attachments, visual content, another workflow, or candidate data.

    Args:
        company_name: Founder-confirmed company name.
        role_title: Founder-confirmed job title.
        role_summary: Plain-language role purpose/overview.
        headcount_target: Number of hires, from 1 to 50.
        target_date: Founder-confirmed target date in YYYY-MM-DD format.
        location: Founder-confirmed location or geographic envelope.
        work_arrangement: On-site, hybrid, remote, or a precise combination.
        employment_type: Full-time, part-time, contract, internship, or other
            founder-confirmed employment relationship.
        compensation_envelope: Exact Founder-supplied band, an explicit
            "to be discussed" statement, or an empty string. Never invent it.
        required_criteria: Must-have job-related qualifications only.
        responsibilities: Candidate-facing responsibilities for the role.
        success_outcomes: Founder-confirmed outcomes expected in the role.
        preferred_criteria: Founder-confirmed preferred, non-gating
            qualifications. At least one is required before the package can be
            reviewed for approval.
        relevant_experience: Material job-related experience expectations.
        benefits: Founder-supplied benefits only; otherwise an empty list.
        hiring_process: Founder-supplied stages, or an empty list to use the
            safe internal default (criteria review, structured interview,
            Founder decision).
        application_instructions: Exact Founder-supplied candidate application
            channel/instructions. This is required before review; never invent
            an email address or URL.
        equal_opportunity_statement: Founder-confirmed, legally reviewed
            candidate wording, or an empty string. Never fabricate a claim.
        accessibility_statement: Founder-confirmed candidate accommodation
            wording, or an empty string. Never fabricate a promise.
        public_job_description: Optional Founder-confirmed introduction or
            candidate-facing wording to preserve. The server assembles the
            complete untruncated post from all structured fields.

    Returns:
        An exact bounded proposal, scorecard, interview plan, readable role
        description, contract hash, and confirmation prompt. No role, run,
        publication, contact, ranking, decision, or provider effect is created.
    """
    built = await write_structured_founder_role_package(
        company_name=company_name,
        role_title=role_title,
        role_summary=role_summary,
        headcount_target=headcount_target,
        target_date=target_date,
        location=location,
        work_arrangement=work_arrangement,
        employment_type=employment_type,
        compensation_envelope=compensation_envelope,
        required_criteria=required_criteria,
        responsibilities=responsibilities,
        success_outcomes=success_outcomes,
        preferred_criteria=preferred_criteria,
        relevant_experience=relevant_experience,
        benefits=benefits,
        hiring_process=hiring_process,
        application_instructions=application_instructions,
        equal_opportunity_statement=equal_opportunity_statement,
        accessibility_statement=accessibility_statement,
        public_job_description=public_job_description,
        company_context=None,
    )
    if built.get("error") or built.get("status") != "success":
        return built
    workspace = workspace_id(tool_context)
    session = _session_id(tool_context)
    actor = actor_id(tool_context)
    if not workspace or not session or not actor:
        return _error(
            "interactive_founder_required",
            "A verified Founder conversation is required to prepare this brief.")
    contract = built["contract"]
    contract_digest = canonical_hash(contract)
    proposal_id = stable_id(
        "hiringproposal", workspace, session, contract_digest)
    tool_context.state[ss.K_HIRING_ROLE_PROPOSAL] = {
        "proposal_id": proposal_id,
        "contract_hash": contract_digest,
        "contract": contract.model_dump(mode="json"),
        "role_description": built["role_description"],
        "status": "PRESENTED",
    }
    return {
        "status": "success",
        "ready_for_confirmation": True,
        "created": False,
        "proposal_id": proposal_id,
        "contract_hash": contract_digest,
        "role_description": built["role_description"],
        "role_brief": {
            "company_name": contract.company_name,
            "role_title": contract.role_title,
            "headcount_target": contract.headcount_target,
            "target_date": contract.target_date,
            "compensation_envelope": contract.compensation_envelope,
        },
        "scorecard": [item.model_dump(mode="json") for item in contract.criteria],
        "interview_plan": [
            item.model_dump(mode="json") for item in contract.interview_plan],
        "confirmation_prompt": (
            "Create exactly this internal DRAFT role and run? This does not "
            "approve, publish, email, source, rank, or decide anything."),
    }


async def create_hiring_draft(
        proposal_id: str, contract_hash: str, founder_confirmed: bool,
        tool_context: ToolContext) -> dict[str, Any]:
    """Create only the exact previously presented internal Hiring DRAFT.

    Use only after the founder explicitly confirms the proposal returned by
    prepare_hiring_role_brief. A voice confirmation can create this reversible
    internal draft only; it cannot activate or approve the role, publish the job
    post, contact or source candidates, process evidence, rank, decide, or use a
    provider. Those capabilities are not exposed by this tool.

    Args:
        proposal_id: Exact proposal id returned by prepare_hiring_role_brief.
        contract_hash: Exact contract hash returned with the displayed package.
        founder_confirmed: True only after explicit founder confirmation.

    Returns:
        The DRAFT role/run receipt or a bounded error. External actions is
        always an empty list.
    """
    if founder_confirmed is not True:
        return _error(
            "founder_confirmation_required",
            "Present the exact role package and wait for explicit confirmation.")
    proposal = dict(tool_context.state.get(ss.K_HIRING_ROLE_PROPOSAL) or {})
    if (proposal.get("status") not in {"PRESENTED", "CREATED"}
            or proposal.get("proposal_id") != proposal_id
            or proposal.get("contract_hash") != contract_hash):
        return _error(
            "hiring_proposal_stale",
            "The exact presented role package is missing or changed. Prepare it again.")
    from app import hiring_routes
    from services import hiring_policy_service
    from services.hiring_contracts import RoleContract

    principal = await _scoped_founder(tool_context)
    if isinstance(principal, dict):
        return principal
    services = hiring_routes._services()
    if not services:
        return _error(
            "hiring_draft_unavailable",
            "Hiring draft storage is not configured in this environment.")
    contract = RoleContract.model_validate(proposal.get("contract") or {})
    if canonical_hash(contract) != contract_hash:
        return _error(
            "hiring_proposal_stale",
            "The displayed role package changed. Prepare and confirm it again.")
    request_id = f"chat:{proposal_id}"
    created = await services[0].create_founder_draft_role(
        principal=principal,
        contract=contract,
        role_description=dict(proposal.get("role_description") or {}),
        client_request_id=request_id,
    )
    if created.get("error"):
        return created
    role = created["role"]
    proposed = await hiring_policy_service.propose_policy(
        principal=principal,
        role_id=role["role_id"],
        contract=contract,
        role_description=dict(proposal.get("role_description") or {}),
        change_reason="Founder-confirmed role package from Alex conversation.",
        client_request_id=f"{request_id}:role-package",
    )
    if proposed.get("error"):
        return {**proposed, "role_id": role["role_id"],
                "role_state": role["role_state"]}
    proposal["status"] = "CREATED"
    proposal["role_id"] = role["role_id"]
    tool_context.state[ss.K_HIRING_ROLE_PROPOSAL] = proposal
    return {
        "status": "success",
        "duplicate": bool(created.get("duplicate")),
        "role_id": role["role_id"],
        "run_id": role["run_id"],
        "role_code": role["role_code"],
        "role_state": role["role_state"],
        "role_title": role["role_title"],
        "company_name": role["company_name"],
        "policy_status": proposed["policy_status"],
        "external_actions": [],
        "message": (
            "The role and run are internal DRAFTS. Review the exact package in "
            "Hiring Operations; nothing was approved, published, or sent."),
    }

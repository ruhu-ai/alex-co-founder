"""Pipeline service (docs/03, 05, 08) — board, scoring writes, application
lifecycle. State transitions guarded by the docs/03 table (can_transition).
"""

from __future__ import annotations

import hashlib
import re

from agents.co_founder.state_schema import ApplicationStep as Step
from agents.co_founder.state_schema import ChecklistStatus, OpportunityState, can_transition
from services import firestore

_URGENCY_TIERS = ((3, "CRITICAL"), (14, "URGENT"))


def normalize_string_list(value) -> list[str]:
    """Normalize an extractor-owned list field at the domain boundary.

    Discovery intentionally stores unknown fields as ``null``. Business logic
    must therefore never assume a schema-declared list is present merely
    because the key exists in Firestore.
    """
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def canonical_opportunity_name(name: str | None) -> str:
    """Stable, conservative identity for founder-facing duplicate collapse.

    Sources frequently append audience labels ("for founders") to the same
    programme name. Removing only those known trailing labels avoids merging
    genuinely different programmes while catching the observed duplicate.
    """
    value = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    return re.sub(r"\s+(?:for\s+)?founders?$", "", value).strip()


def compute_urgency(deadline: str | None, required_materials: list[str] | None) -> dict:
    """Deadline sentinel tiers (docs/08): OVERDUE | CRITICAL(≤3d) | URGENT(≤14d) | NORMAL | ROLLING.

    "Today" is UTC, explicitly — Cloud Run's server-local date IS UTC, but
    date.today() made that an accident of deployment. Extraction sometimes
    yields datetime-shaped strings ("2026-11-30T23:59:00Z"); the date prefix
    parses instead of silently degrading a due-tomorrow grant to NORMAL."""
    if not deadline:
        return {"tier": "ROLLING", "days_left": None, "note": "rolling deadline"}
    try:
        from datetime import date as _date
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        days = (_date.fromisoformat(str(deadline)[:10])
                - _dt.now(_tz.utc).date()).days
    except ValueError:
        return {"tier": "NORMAL", "days_left": None, "note": f"unparsed deadline {deadline!r}"}
    tier = "OVERDUE" if days < 0 else next((t for lim, t in _URGENCY_TIERS if days <= lim), "NORMAL")
    workload = f"needs {len(required_materials)} materials" if required_materials else "no materials listed"
    note = f"closes in {days} days, {workload}" + (" — start now" if tier in ("CRITICAL", "URGENT") else "")
    return {"tier": tier, "days_left": days, "note": note}


def dedup_hash(name: str | None, application_url: str | None) -> str:
    canonical_name = canonical_opportunity_name(name)
    return hashlib.sha256(
        f"{canonical_name}|{(application_url or '').strip().lower()}".encode()
    ).hexdigest()


def initial_checklist(required_materials: list[str] | None) -> list[dict]:
    items = [
        {"key": "eligibility_check", "label": "Eligibility check"},
        {"key": "interview", "label": "Guided interview"},
        {"key": "draft_sections", "label": "Draft sections"},
        {"key": "review", "label": "Founder review"},
        {"key": "form_fill", "label": "Form pre-fill"},
        {"key": "approval", "label": "Submission approval"},
        {"key": "submit", "label": "Submit"},
        {"key": "followup", "label": "Follow up"},
    ]
    items += [{"key": f"material:{m}", "label": f"Material: {m}"}
              for m in normalize_string_list(required_materials)]
    return [{**item, "status": ChecklistStatus.PENDING, "section_id": None} for item in items]


async def board(founder_id: str, limit: int = 40) -> dict:
    opportunities = await firestore.list_opportunities(
        limit=limit, founder_id=founder_id)
    applications = await firestore.list_inflight_applications(founder_id)
    by_id = {o.get("id"): o for o in opportunities}
    for app in applications:  # committed programs: name + deadline for the UI
        opp = by_id.get(app.get("opportunity_id")) or \
            await firestore.get_opportunity(
                app.get("opportunity_id", ""), founder_id)
        if opp:
            app["opportunity_name"] = opp.get("name")
            app["deadline"] = opp.get("deadline")
    grouped: dict[str, list[dict]] = {"SHORTLISTED": [], "DISCOVERED": [], "ARCHIVED": []}
    # Do not show two cards for the same programme merely because one source
    # appended "for founders" or omitted the application URL. This is a
    # non-destructive projection: application references to either durable
    # record remain valid.
    seen: set[tuple[str, str]] = set()
    for opp in sorted(
            opportunities,
            key=lambda item: sum(bool(item.get(key)) for key in (
                "application_url", "source_url", "description",
                "required_materials", "raw_excerpt")),
            reverse=True):
        cycle = str(opp.get("deadline") or opp.get("created_at") or "")[:4]
        identity = (canonical_opportunity_name(opp.get("name")), cycle)
        if identity[0] and identity in seen:
            continue
        seen.add(identity)
        grouped.setdefault(opp.get("state", "DISCOVERED"), []).append(opp)
    grouped["SHORTLISTED"].sort(
        key=lambda o: (o.get("urgency") or {}).get("days_left") if (o.get("urgency") or {}).get("days_left") is not None else 9999
    )
    return {"status": "success", "opportunities": grouped, "applications": applications}


async def shortlist(opportunity_id: str, rationale: str, urgency_note: str,
                    fit_score: int, founder_id: str = "") -> dict:
    if not isinstance(fit_score, int) or not 70 <= fit_score <= 100:
        return {"status": "error", "error": True,
                "message": "shortlist fit_score must be an integer from 70 to 100"}
    opp = await firestore.get_opportunity(opportunity_id, founder_id)
    if not opp:
        return {"status": "error", "error": True, "message": f"opportunity {opportunity_id} not found"}
    if opp["state"] != OpportunityState.DISCOVERED:
        return {"status": "error", "error": True,
                "message": f"cannot shortlist from state {opp['state']} (guard: DISCOVERED only)"}
    urgency = compute_urgency(opp.get("deadline"), opp.get("required_materials", []))
    urgency["note"] = urgency_note or urgency["note"]
    await firestore.set_opportunity_state(
        opportunity_id, OpportunityState.SHORTLISTED,
        founder_id=founder_id,
        fit_score=fit_score, fit_rationale=rationale[:280], urgency=urgency,
    )
    await firestore.audit("agent:matchmaker", "shortlist", f"opportunities/{opportunity_id}", "success",
                          f"fit={fit_score}: {rationale[:160]}")
    return {"status": "success", "opportunity_id": opportunity_id, "state": OpportunityState.SHORTLISTED}


async def archive(opportunity_id: str, reason: str, fit_score: int,
                  founder_id: str = "") -> dict:
    if not reason.strip():
        return {"status": "error", "error": True, "message": "archive reason must be specific and non-empty"}
    if not isinstance(fit_score, int) or not 0 <= fit_score < 70:
        return {"status": "error", "error": True,
                "message": "archive fit_score must be an integer from 0 to 69"}
    opp = await firestore.get_opportunity(opportunity_id, founder_id)
    if not opp:
        return {"status": "error", "error": True, "message": f"opportunity {opportunity_id} not found"}
    if opp["state"] != OpportunityState.DISCOVERED:
        return {"status": "error", "error": True,
                "message": f"cannot archive from state {opp['state']} (guard: DISCOVERED only)"}
    await firestore.set_opportunity_state(
        opportunity_id, OpportunityState.ARCHIVED, founder_id=founder_id,
        archive_reason=reason, fit_score=fit_score,
    )
    await firestore.audit("agent:matchmaker", "archive", f"opportunities/{opportunity_id}", "success",
                          f"fit={fit_score}: {reason[:160]}")
    return {"status": "success", "opportunity_id": opportunity_id, "state": OpportunityState.ARCHIVED}


async def choose_opportunity(founder_id: str, opportunity_id: str) -> dict:
    """TRIAGE → INTERVIEWING, idempotently, across sessions and retries."""
    opp = await firestore.get_opportunity(opportunity_id, founder_id)
    if not opp:
        return {"status": "error", "error": True, "message": f"opportunity {opportunity_id} not found"}
    if opp["state"] != OpportunityState.SHORTLISTED:
        return {"status": "error", "error": True,
                "message": f"can only apply to SHORTLISTED opportunities (state is {opp['state']})"}
    materials = normalize_string_list(opp.get("required_materials"))
    checklist = initial_checklist(materials)
    existing = await firestore.find_application_by_founder_opportunity(
        founder_id, opportunity_id)
    created = False
    if existing:
        application_id = existing["id"]
        application = existing
    else:
        claimed = await firestore.get_or_create_application(
            founder_id, opportunity_id, checklist)
        application_id = claimed["application_id"]
        application = claimed["application"]
        created = bool(claimed["created"])

    current_step = application.get("state", Step.INTERVIEWING)
    committed_checklist = application.get("checklist") or checklist
    if created:
        await firestore.audit(
            "agent:orchestrator", "state_transition", f"applications/{application_id}",
            "success", "TRIAGE → INTERVIEWING")
    from services.workflow_projection_service import WorkflowProjectionService, shadow_enabled
    workflow_run_id = str(application.get("workflow_run_id") or "")
    if shadow_enabled() and not workflow_run_id:
        shadow = await WorkflowProjectionService().ensure_grant_application(
            workspace_id=founder_id, application_id=application_id,
            originating_actor_id=founder_id)
        if not shadow.get("error"):
            workflow_run_id = shadow["run_id"]
            await firestore.update_application(
                application_id, workflow_run_id=workflow_run_id,
                workflow_plan_hash=shadow.get("plan_hash"),
                workflow_plan_version=shadow.get("plan_version"),
                workflow_shadow_status="MATCHED")
    missing_metadata = [
        label for value, label in (
            (opp.get("application_url"), "verified application URL"),
            (materials, "required materials"),
        ) if not value
    ]
    return {
        "status": "success",
        "application_id": application_id,
        **({"workflow_run_id": workflow_run_id} if workflow_run_id else {}),
        "current_step": current_step,
        "already_active": not created and current_step != Step.CLOSED,
        "already_exists": not created,
        "active_program_requirements": materials,
        "readiness": {
            "ready_for_portal": not missing_metadata,
            "missing": missing_metadata,
            "message": (
                "Application started, but the opportunity record still needs "
                + " and ".join(missing_metadata)
                + ". Drafting may continue from cited programme evidence; portal "
                  "work must wait for verified metadata."
                if missing_metadata else "Opportunity metadata is application-ready."
            ),
        },
        "checklist_status": {
            item["key"]: item["status"] for item in committed_checklist},
    }


async def advance_application(application_id: str, to_step: str, actor: str, *,
                              founder_id: str, **fields) -> dict:
    """Guarded application transition — the only way application state moves."""
    app = await firestore.get_application(application_id, founder_id)
    if not app:
        return {"status": "error", "error": True, "message": f"application {application_id} not found"}
    if not can_transition(app["state"], to_step):
        return {"status": "error", "error": True,
                "message": f"illegal transition {app['state']} → {to_step}"}
    transition = await firestore.guarded_application_transition(
        application_id, app["state"], to_step, **fields)
    if transition.get("status") != "success":
        return transition
    await firestore.audit(actor, "state_transition", f"applications/{application_id}",
                          "success", f"{app['state']} → {to_step}")
    return {"status": "success", "application_id": application_id, "current_step": to_step}


def derive_submit_key(founder_id: str, application_id: str) -> str:
    """Derived idempotency key (docs/05): a retry of the SAME submit recomputes
    the SAME key. Keyed on founder+application so it survives across sessions."""
    return hashlib.sha256(f"{founder_id}:{application_id}:submit".encode()).hexdigest()[:24]

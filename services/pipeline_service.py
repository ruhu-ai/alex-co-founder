"""Pipeline service (docs/03, 05, 08) — board, scoring writes, application
lifecycle. State transitions guarded by the docs/03 table (can_transition).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from agents.co_founder.state_schema import ApplicationStep as Step
from agents.co_founder.state_schema import ChecklistStatus, OpportunityState, can_transition
from services import firestore

_URGENCY_TIERS = ((3, "CRITICAL"), (14, "URGENT"))


def compute_urgency(deadline: str | None, required_materials: list[str]) -> dict:
    """Deadline sentinel tiers (docs/08): OVERDUE | CRITICAL(≤3d) | URGENT(≤14d) | NORMAL | ROLLING."""
    if not deadline:
        return {"tier": "ROLLING", "days_left": None, "note": "rolling deadline"}
    try:
        from datetime import date as _date

        days = (_date.fromisoformat(deadline) - _date.today()).days
    except ValueError:
        return {"tier": "NORMAL", "days_left": None, "note": f"unparsed deadline {deadline!r}"}
    tier = "OVERDUE" if days < 0 else next((t for lim, t in _URGENCY_TIERS if days <= lim), "NORMAL")
    workload = f"needs {len(required_materials)} materials" if required_materials else "no materials listed"
    note = f"closes in {days} days, {workload}" + (" — start now" if tier in ("CRITICAL", "URGENT") else "")
    return {"tier": tier, "days_left": days, "note": note}


def dedup_hash(name: str, application_url: str) -> str:
    return hashlib.sha256(
        f"{(name or '').strip().lower()}|{(application_url or '').strip().lower()}".encode()
    ).hexdigest()


def initial_checklist(required_materials: list[str]) -> list[dict]:
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
    items += [{"key": f"material:{m}", "label": f"Material: {m}"} for m in required_materials]
    return [{**item, "status": ChecklistStatus.PENDING, "section_id": None} for item in items]


async def board(founder_id: str, limit: int = 40) -> dict:
    opportunities = await firestore.list_opportunities(limit=limit)
    applications = await firestore.list_inflight_applications(founder_id)
    by_id = {o.get("id"): o for o in opportunities}
    for app in applications:  # committed programs: name + deadline for the UI
        opp = by_id.get(app.get("opportunity_id")) or \
            await firestore.get_opportunity(app.get("opportunity_id", ""))
        if opp:
            app["opportunity_name"] = opp.get("name")
            app["deadline"] = opp.get("deadline")
    grouped: dict[str, list[dict]] = {"SHORTLISTED": [], "DISCOVERED": [], "ARCHIVED": []}
    for opp in opportunities:
        grouped.setdefault(opp.get("state", "DISCOVERED"), []).append(opp)
    grouped["SHORTLISTED"].sort(
        key=lambda o: (o.get("urgency") or {}).get("days_left") if (o.get("urgency") or {}).get("days_left") is not None else 9999
    )
    return {"status": "success", "opportunities": grouped, "applications": applications}


async def shortlist(opportunity_id: str, rationale: str, urgency_note: str, fit_score: int) -> dict:
    opp = await firestore.get_opportunity(opportunity_id)
    if not opp:
        return {"status": "error", "error": True, "message": f"opportunity {opportunity_id} not found"}
    if opp["state"] != OpportunityState.DISCOVERED:
        return {"status": "error", "error": True,
                "message": f"cannot shortlist from state {opp['state']} (guard: DISCOVERED only)"}
    urgency = compute_urgency(opp.get("deadline"), opp.get("required_materials", []))
    urgency["note"] = urgency_note or urgency["note"]
    await firestore.set_opportunity_state(
        opportunity_id, OpportunityState.SHORTLISTED,
        fit_score=fit_score, fit_rationale=rationale[:280], urgency=urgency,
    )
    await firestore.audit("agent:matchmaker", "shortlist", f"opportunities/{opportunity_id}", "success",
                          f"fit={fit_score}: {rationale[:160]}")
    return {"status": "success", "opportunity_id": opportunity_id, "state": OpportunityState.SHORTLISTED}


async def archive(opportunity_id: str, reason: str, fit_score: int) -> dict:
    if not reason.strip():
        return {"status": "error", "error": True, "message": "archive reason must be specific and non-empty"}
    opp = await firestore.get_opportunity(opportunity_id)
    if not opp:
        return {"status": "error", "error": True, "message": f"opportunity {opportunity_id} not found"}
    await firestore.set_opportunity_state(
        opportunity_id, OpportunityState.ARCHIVED, archive_reason=reason, fit_score=fit_score,
    )
    await firestore.audit("agent:matchmaker", "archive", f"opportunities/{opportunity_id}", "success",
                          f"fit={fit_score}: {reason[:160]}")
    return {"status": "success", "opportunity_id": opportunity_id, "state": OpportunityState.ARCHIVED}


async def choose_opportunity(founder_id: str, opportunity_id: str) -> dict:
    """TRIAGE → INTERVIEWING: create the application + checklist (docs/03 #3)."""
    opp = await firestore.get_opportunity(opportunity_id)
    if not opp:
        return {"status": "error", "error": True, "message": f"opportunity {opportunity_id} not found"}
    if opp["state"] != OpportunityState.SHORTLISTED:
        return {"status": "error", "error": True,
                "message": f"can only apply to SHORTLISTED opportunities (state is {opp['state']})"}
    checklist = initial_checklist(opp.get("required_materials", []))
    application_id = await firestore.create_application(founder_id, opportunity_id, checklist)
    await firestore.audit("agent:orchestrator", "state_transition", f"applications/{application_id}",
                          "success", "TRIAGE → INTERVIEWING")
    return {
        "status": "success",
        "application_id": application_id,
        "current_step": Step.INTERVIEWING,
        "active_program_requirements": opp.get("required_materials", []),
        "checklist_status": {item["key"]: item["status"] for item in checklist},
    }


async def advance_application(application_id: str, to_step: str, actor: str, **fields) -> dict:
    """Guarded application transition — the only way application state moves."""
    app = await firestore.get_application(application_id)
    if not app:
        return {"status": "error", "error": True, "message": f"application {application_id} not found"}
    if not can_transition(app["state"], to_step):
        return {"status": "error", "error": True,
                "message": f"illegal transition {app['state']} → {to_step}"}
    if to_step == Step.APPROVED and not app.get("submit_idempotency_key"):
        fields["submit_idempotency_key"] = uuid.uuid4().hex
    await firestore.update_application(application_id, state=to_step, **fields)
    await firestore.audit(actor, "state_transition", f"applications/{application_id}",
                          "success", f"{app['state']} → {to_step}")
    return {"status": "success", "application_id": application_id, "current_step": to_step}


def derive_submit_key(founder_id: str, application_id: str) -> str:
    """Derived idempotency key (docs/05): a retry of the SAME submit recomputes
    the SAME key. Keyed on founder+application so it survives across sessions."""
    return hashlib.sha256(f"{founder_id}:{application_id}:submit".encode()).hexdigest()[:24]

"""Approval gate service (docs/12). The token exists only in Firestore —
never in the browser, never in the model's context.
"""

from __future__ import annotations

import os

from services import firestore

_TTL = int(os.environ.get("APPROVAL_TTL_MINUTES", "30"))


async def request_approval(application_id: str, gate: str = "submit_application") -> dict:
    if gate != "submit_application":
        return {"status": "error", "error": True, "message": f"unknown gate {gate!r}"}
    approval_id = await firestore.create_approval(application_id, gate, _TTL)
    await firestore.audit("agent:form_filler", "request_approval",
                          f"applications/{application_id}", "success", f"gate={gate}")
    return {"status": "success", "approval_id": approval_id}  # never the token


async def resolve(approval_id: str, decision: str, founder_id: str) -> dict:
    approval = await firestore.get_approval(approval_id)
    if not approval:
        return {"status": "error", "error": True, "message": f"approval {approval_id} not found"}
    if approval["status"] != "PENDING":
        return {"status": "error", "error": True,
                "message": f"approval already {approval['status']}"}
    if decision == "grant":
        await firestore.grant_approval(approval_id, founder_id)
    elif decision == "deny":
        await firestore.deny_approval(approval_id)
    else:
        return {"status": "error", "error": True, "message": "decision must be grant|deny"}
    await firestore.audit(f"founder:{founder_id}", f"approval_{decision}",
                          f"approvals/{approval_id}", "success")
    return {"status": "success", "approval_id": approval_id, "decision": decision}


async def resolve_for_submit(application_id: str) -> dict:
    """Server-side gate for submit_form (docs/12 step 3). No token argument —
    the tool resolves the approval itself."""
    approval = await firestore.find_valid_approval(application_id)
    if not approval:
        await firestore.audit("agent:form_filler", "submit", f"applications/{application_id}",
                              "refused", "no GRANTED, unexpired, unconsumed approval")
        return {"status": "error", "error": True,
                "message": "Submission blocked: no founder approval at the gate."}
    return {"status": "success", "approval_id": approval["id"]}


async def consume(approval_id: str) -> None:
    await firestore.consume_approval(approval_id)

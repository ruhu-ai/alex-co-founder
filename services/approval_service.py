"""Approval gate service (docs/12). The token exists only in Firestore —
never in the browser, never in the model's context.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from services import firestore

_TTL = int(os.environ.get("APPROVAL_TTL_MINUTES", "30"))

# Gates the founder can grant. submit_application: form submission.
# send_email: outbound mail from alex@ruhu.ai (adr/001).
# book_meeting: calendar event + emailed invites (adr/002).
GATES = {"submit_application", "send_email", "book_meeting", "create_portal_account"}


async def request_approval(application_id: str, gate: str = "submit_application",
                           details: dict | None = None, founder_id: str = "",
                           session_id: str = "") -> dict:
    if gate not in GATES:
        return {"status": "error", "error": True, "message": f"unknown gate {gate!r}"}
    if not founder_id or not session_id:
        return {"status": "error", "error": True,
                "message": "approval requests require a founder-bound session"}
    pending = await firestore.find_pending_approval(
        application_id, gate=gate, session_id=session_id, founder_id=founder_id)
    if pending:
        return {"status": "success", "approval_id": pending["id"], "existing": True}
    approval_id = await firestore.create_approval(
        application_id, gate, _TTL, details=details,
        founder_id=founder_id, session_id=session_id)
    await firestore.audit("agent:form_filler", "request_approval",
                          f"applications/{application_id}", "success", f"gate={gate}")
    return {"status": "success", "approval_id": approval_id}  # never the token


async def resolve(approval_id: str, decision: str, founder_id: str,
                  session_id: str) -> dict:
    approval = await firestore.get_approval(approval_id)
    if not approval:
        return {"status": "error", "error": True, "message": f"approval {approval_id} not found"}
    if approval["status"] != "PENDING":
        return {"status": "error", "error": True,
                "message": f"approval already {approval['status']}"}
    if approval.get("expires_at", "") <= datetime.now(timezone.utc).isoformat():
        return {"status": "error", "error": True,
                "message": "approval request expired; request a new one"}
    if not session_id or approval.get("session_id") != session_id:
        await firestore.audit(f"founder:{founder_id}", "approval_refused",
                              f"approvals/{approval_id}", "refused",
                              "approval does not belong to this session")
        return {"status": "error", "error": True,
                "message": "approval does not belong to this founder session"}
    if approval.get("founder_id") != founder_id:
        return {"status": "error", "error": True,
                "message": "approval does not belong to this founder"}
    if decision == "grant":
        await firestore.grant_approval(approval_id, founder_id)
    elif decision == "deny":
        await firestore.deny_approval(approval_id)
    else:
        return {"status": "error", "error": True, "message": "decision must be grant|deny"}
    await firestore.audit(f"founder:{founder_id}", f"approval_{decision}",
                          f"approvals/{approval_id}", "success")
    return {"status": "success", "approval_id": approval_id, "decision": decision,
            "gate": approval.get("gate", ""),
            "application_id": approval.get("application_id", ""),
            "session_id": approval.get("session_id", "")}


async def claim_for_action(application_id: str, gate: str, founder_id: str = "",
                           session_id: str = "") -> dict:
    """Find a matching GRANTED, unexpired, unconsumed approval.

    The action implementation must atomically claim it before the first
    irreversible provider call.  Provider timeouts are outcome-ambiguous, so
    releasing an approval after an error could authorize a duplicate action.
    """
    if not founder_id or not session_id:
        return {"status": "error", "error": True,
                "message": "Action blocked: approval requires a founder-bound session."}
    approval = await firestore.find_valid_approval(
        application_id, gate=gate, founder_id=founder_id, session_id=session_id)
    if not approval:
        await firestore.audit("agent:gate", gate, application_id, "refused",
                              "no GRANTED, unexpired, unconsumed approval")
        return {"status": "error", "error": True,
                "message": "Action blocked: no founder approval at the gate."}
    return {"status": "success", "approval_id": approval["id"]}


async def resolve_for_submit(application_id: str, founder_id: str = "",
                             session_id: str = "") -> dict:
    """Server-side gate for submit_form (docs/12 step 3). No token argument —
    the tool resolves the approval itself."""
    return await claim_for_action(
        application_id, "submit_application", founder_id, session_id)


async def consume(approval_id: str) -> None:
    await firestore.consume_approval(approval_id)

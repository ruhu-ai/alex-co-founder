"""Approval gate service (docs/12). The token exists only in Firestore —
never in the browser, never in the model's context.

This module also owns the *binding* between a submit approval and the exact
thing the founder approved (docs/02 `approvals.subject_hash`, docs/12 §Approval
tokens, docs/22 §Fill Stop and restart recovery). Without it the gate answers
"did the founder approve something for this application?" when the only safe
question is "did the founder approve *this form filled with these answers*?".
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from services import firestore

_TTL = int(os.environ.get("APPROVAL_TTL_MINUTES", "30"))

# Gates the founder can grant. submit_application: form submission.
# send_email: outbound mail from alex@ruhu.ai (adr/001).
# book_meeting: calendar event + emailed invites (adr/002).
GATES = {"submit_application", "send_email", "book_meeting", "create_portal_account"}

# Canonical-form version tag. It is mixed into every digest so a future change
# to the canonical form can never accidentally compare equal to an old one —
# an old hash simply stops matching and the founder re-approves (fail closed).
_BINDING_VERSION = "submit-binding-v1"
_UNIT = "\x1f"    # name/value separator inside one record
_RECORD = "\x1e"  # record separator


def mapping_hash(mapping: dict | None) -> str:
    """Canonical hash of the *intended* fill mapping (docs/09 §Fill report).

    Field names are sorted so dict ordering cannot change the hash, and each
    value contributes a domain-separated digest — ``sha256(name || NUL ||
    value)`` — rather than the value itself.

    Why the values are represented as digests: the mapping is the founder's
    approved answers (company details, financials, personal narrative). The
    fill report and the approval row are both read back by the UI and quoted in
    the audit tail, so any plaintext — or a prefix, or even a length — would
    publish PII into surfaces that are explicitly "no secrets, no PII beyond
    refs" (docs/02 §audit). A per-field SHA-256 is one-way, is still sensitive
    to *any* change of *any* single intended value (which is all the gate
    needs), and the per-field domain separation means one precomputed table of
    common short answers cannot be reused across fields.
    """
    parts = []
    for name in sorted((mapping or {}), key=str):
        raw = (mapping or {})[name]
        text = "" if raw is None else str(raw)
        digest = hashlib.sha256(f"{name}\x00{text}".encode()).hexdigest()
        parts.append(f"{name}{_UNIT}{digest}")
    return "sha256:" + hashlib.sha256(
        (_BINDING_VERSION + _RECORD + _RECORD.join(parts)).encode()).hexdigest()


def submit_subject_hash(application_id: str, portal_state_hash: str,
                        fill_mapping_hash: str) -> str:
    """The immutable identity of what a submit approval covers.

    ``hash(application_id + portal_state_hash + mapping_hash)`` — the live form
    the founder saw AND the answers that were going into it. Returns ``""`` when
    any component is missing: an unbindable subject must refuse, never pass.
    """
    if not application_id or not portal_state_hash or not fill_mapping_hash:
        return ""
    return "sha256:" + hashlib.sha256(_RECORD.join([
        _BINDING_VERSION, "submit_application", application_id,
        portal_state_hash, fill_mapping_hash,
    ]).encode()).hexdigest()


def action_subject_hash(gate: str, target: str, details: dict | None) -> str:
    """The immutable identity of what a non-submit approval covers.

    Same construction as `submit_subject_hash`, generalized: the gate, the
    action target, and a canonical digest of the exact payload the founder was
    shown (recipient, subject, body / attendees, time, title).

    Without this, `send_email` and `book_meeting` bound only
    (target, gate, founder, session): any GRANTED approval for the same target
    satisfied any later call, and the adapters "resolved" the difference by
    silently substituting the approved details and auditing `drift_ignored` —
    so a request to send message B under an approval for message A sent A and
    reported success. A changed payload must now REFUSE, not substitute.

    Returns "" when the payload is empty: an unbindable subject refuses.
    """
    if not gate or not target or not details:
        return ""
    return "sha256:" + hashlib.sha256(_RECORD.join([
        _BINDING_VERSION, gate, target, mapping_hash(details),
    ]).encode()).hexdigest()


def subject_hash_for_application(application_id: str,
                                 application: dict | None) -> str:
    """Derive the expected subject hash from the durable fill report.

    `applications.form_fill_report` is the authority (docs/22 §Fill Stop);
    a report without both hashes yields ``""`` → the gate refuses.
    """
    report = (application or {}).get("form_fill_report") or {}
    if not isinstance(report, dict):
        return ""
    return submit_subject_hash(application_id,
                               report.get("portal_state_hash") or "",
                               report.get("mapping_hash") or "")


async def request_approval(application_id: str, gate: str = "submit_application",
                           details: dict | None = None, founder_id: str = "",
                           session_id: str = "", subject_hash: str = "") -> dict:
    if gate not in GATES:
        return {"status": "error", "error": True, "message": f"unknown gate {gate!r}"}
    if not founder_id or not session_id:
        return {"status": "error", "error": True,
                "message": "approval requests require a founder-bound session"}
    if gate == "submit_application" and not subject_hash:
        # Derive from durable truth so no caller can arm the submit gate with an
        # unbound approval — including the model-facing request_approval tool.
        subject_hash = subject_hash_for_application(
            application_id, await firestore.get_application(application_id))
        if not subject_hash:
            # Refuse rather than mint a request the founder could grant and the
            # submit gate could never honour (it fails closed on the binding).
            return {"status": "error", "error": True,
                    "error_code": "approval_binding_missing",
                    "message": ("Cannot arm the submit gate: this application "
                                "has no fill report recording the portal state "
                                "and the answers to submit. Fill the form first, "
                                "then request approval for that fill.")}
    if subject_hash:
        # A fill that materially changed the portal form or the intended
        # mapping invalidates everything the founder approved before it: a
        # stale PENDING request would be granted against the wrong subject, and
        # a stale GRANTED token would submit it (docs/22 §Fill Stop).
        for stale_id in await firestore.expire_stale_approvals(
                application_id, gate, subject_hash):
            await firestore.audit(
                "agent:form_filler", "approval_expired", f"approvals/{stale_id}",
                "success",
                "portal form or intended answers changed since this approval")
    pending = await firestore.find_pending_approval(
        application_id, gate=gate, session_id=session_id, founder_id=founder_id)
    if pending and (pending.get("subject_hash") or "") == (subject_hash or ""):
        return {"status": "success", "approval_id": pending["id"], "existing": True}
    approval_id = await firestore.create_approval(
        application_id, gate, _TTL, details=details,
        founder_id=founder_id, session_id=session_id, subject_hash=subject_hash)
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
                           session_id: str = "",
                           expected_subject_hash: str = "") -> dict:
    """Find a matching GRANTED, unexpired, unconsumed approval.

    The action implementation must atomically claim it before the first
    irreversible provider call.  Provider timeouts are outcome-ambiguous, so
    releasing an approval after an error could authorize a duplicate action.

    `expected_subject_hash` binds the lookup to the exact subject the founder
    approved. When it is supplied, an approval carrying no `subject_hash` — or a
    different one — never satisfies the gate: it is refused, with the grant left
    untouched so nothing is silently consumed.
    """
    if not founder_id or not session_id:
        return {"status": "error", "error": True,
                "message": "Action blocked: approval requires a founder-bound session."}
    approval = await firestore.find_valid_approval(
        application_id, gate=gate, founder_id=founder_id, session_id=session_id,
        subject_hash=expected_subject_hash or None)
    if not approval:
        code = "approval_missing"
        detail = "no GRANTED, unexpired, unconsumed approval"
        message = "Action blocked: no founder approval at the gate."
        if expected_subject_hash:
            # Distinguish "nothing granted" from "granted, but for a different
            # form/answers" — the founder needs to know which one to fix.
            unbound = await firestore.find_valid_approval(
                application_id, gate=gate, founder_id=founder_id,
                session_id=session_id)
            if unbound:
                bound = bool(unbound.get("subject_hash"))
                code = ("approval_binding_mismatch" if bound
                        else "approval_binding_missing")
                detail = (f"approval {unbound.get('id', '')} does not cover the "
                          f"current form and answers ({code})")
                message = (
                    "Action blocked: the founder's approval does not cover the "
                    "form and answers as they stand now. Re-fill the form and ask "
                    "the founder to approve that fill before submitting.")
        await firestore.audit("agent:gate", gate, application_id, "refused", detail)
        return {"status": "error", "error": True, "error_code": code,
                "message": message}
    return {"status": "success", "approval_id": approval["id"],
            "subject_hash": approval.get("subject_hash") or ""}


async def resolve_for_submit(application_id: str, founder_id: str = "",
                             session_id: str = "",
                             expected_subject_hash: str = "") -> dict:
    """Server-side gate for submit_form (docs/12 step 3). No token argument —
    the tool resolves the approval itself.

    Fails closed: without a subject hash derived from the durable fill report
    (and an approval carrying the same one) this refuses rather than submitting
    under an approval that may have been issued for a different form state.
    """
    if not expected_subject_hash:
        expected_subject_hash = subject_hash_for_application(
            application_id, await firestore.get_application(application_id))
    if not expected_subject_hash:
        await firestore.audit(
            "agent:gate", "submit_application", application_id, "refused",
            "fill report carries no portal_state_hash/mapping_hash binding")
        return {
            "status": "error", "error": True,
            "error_code": "approval_binding_missing",
            "message": ("Action blocked: this application has no fill report bound "
                        "to a portal state and an answer mapping, so no approval can "
                        "prove what the founder saw. Re-fill the form and ask the "
                        "founder to approve the new fill before submitting."),
        }
    return await claim_for_action(
        application_id, "submit_application", founder_id, session_id,
        expected_subject_hash=expected_subject_hash)


async def invalidate_submit_approvals(application_id: str,
                                      subject_hash: str = "",
                                      reason: str = "") -> list[str]:
    """Expire every open submit approval that does not cover `subject_hash`.

    Passing ``""`` expires them all — used when the live portal no longer
    matches the report the grant was bound to (docs/12: a mismatch expires the
    grant and requires a new fill report and approval).
    """
    expired = await firestore.expire_stale_approvals(
        application_id, "submit_application", subject_hash)
    for approval_id in expired:
        await firestore.audit(
            "agent:form_filler", "approval_expired", f"approvals/{approval_id}",
            "success", (reason or "subject of the approval no longer matches")[:300])
    return expired


async def consume(approval_id: str) -> None:
    await firestore.consume_approval(approval_id)

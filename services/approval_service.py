"""Approval gate service (docs/12). The token exists only in Firestore —
never in the browser, never in the model's context.

This module also owns the *binding* between a submit approval and the exact
thing the founder approved (docs/02 `approvals.subject_hash`, docs/12 §Approval
tokens, docs/22 §Fill Stop and restart recovery). Without it the gate answers
"did the founder approve something for this application?" when the only safe
question is "did the founder approve *this form filled with these answers*?".
"""

from __future__ import annotations

import os

from services import firestore
from services.actor_identity import ActorPrincipal, authorize
from services.canonical import canonical_hash, text_hash

_TTL = int(os.environ.get("APPROVAL_TTL_MINUTES", "30"))

# Gates the founder can grant. submit_application: form submission.
# send_email: outbound mail from alex@ruhu.ai (adr/001).
# book_meeting: calendar event + emailed invites (adr/002).
GATES = {"submit_application", "send_email", "book_meeting",
         "create_portal_account", "export_drive_file",
         "export_alex_drive_file"}

# Canonical-form version tag. It is mixed into every digest so a future change
# to the canonical form can never accidentally compare equal to an old one —
# an old hash simply stops matching and the founder re-approves (fail closed).
_BINDING_VERSION = "approval-binding-v2"


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
    fields = {}
    for name in sorted((mapping or {}), key=str):
        raw = (mapping or {})[name]
        text = "" if raw is None else str(raw)
        fields[str(name)] = text_hash(
            text, domain=f"approval-field:{str(name)[:64]}")
    return canonical_hash(
        {"binding_version": _BINDING_VERSION, "fields": fields},
        domain="approval-mapping")


def submit_subject_hash(application_id: str, portal_state_hash: str,
                        fill_mapping_hash: str) -> str:
    """The immutable identity of what a submit approval covers.

    ``hash(application_id + portal_state_hash + mapping_hash)`` — the live form
    the founder saw AND the answers that were going into it. Returns ``""`` when
    any component is missing: an unbindable subject must refuse, never pass.
    """
    if not application_id or not portal_state_hash or not fill_mapping_hash:
        return ""
    return canonical_hash({
        "binding_version": _BINDING_VERSION,
        "gate": "submit_application", "application_id": application_id,
        "portal_state_hash": portal_state_hash,
        "fill_mapping_hash": fill_mapping_hash,
    }, domain="approval-subject")


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
    return canonical_hash({
        "binding_version": _BINDING_VERSION, "gate": gate,
        "target": target, "mapping_hash": mapping_hash(details),
    }, domain="approval-subject")


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
                           session_id: str = "", subject_hash: str = "",
                           requested_by_actor_id: str = "") -> dict:
    if gate not in GATES:
        return {"status": "error", "error": True, "message": f"unknown gate {gate!r}"}
    direct_click = gate in {
        "export_drive_file", "export_alex_drive_file"
    } and bool(requested_by_actor_id)
    if not founder_id or (not session_id and not direct_click):
        return {"status": "error", "error": True,
                "message": "approval requests require a founder-bound session"}
    if gate == "submit_application" and not subject_hash:
        # Derive from durable truth so no caller can arm the submit gate with an
        # unbound approval — including the model-facing request_approval tool.
        subject_hash = subject_hash_for_application(
            application_id, await firestore.get_application(
                application_id, founder_id))
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
    connector_by_gate = {
        "submit_application": "browser",
        "create_portal_account": "browser",
        "send_email": "alex_mail",
        "book_meeting": "calendar",
        "export_drive_file": "drive",
        "export_alex_drive_file": "alex_drive",
    }
    action_by_gate = {
        "submit_application": "submit_application",
        "create_portal_account": "create_portal_account",
        "send_email": "send_email",
        "book_meeting": "create_calendar_event",
        "export_drive_file": "export_drive_file",
        "export_alex_drive_file": "export_alex_drive_file",
    }
    connector_id = connector_by_gate[gate]
    action_kind = action_by_gate[gate]
    from services.capability_registry import require_external_action

    try:
        capability = require_external_action(action_kind, connector_id)
    except ValueError:
        return {"status": "error", "error": True,
                "error_code": "capability_disabled",
                "message": "This consequence is not in the reviewed manifest."}
    # ``application_id`` is the historical name of the approval target.  It is
    # a real application id for submit, but email/calendar/account approvals
    # deliberately use identities such as ``email:general`` or
    # ``portal:example.org``.  Only the submit gate may infer domain authority
    # from it.
    application = (await firestore.get_application(application_id, founder_id)
                   if gate == "submit_application" else None)
    if gate == "submit_application" and not application:
        return {"status": "error", "error": True,
                "error_code": "owner_mismatch", "message": "Application not found."}
    target_hash = canonical_hash(
        {"target": application_id},
        domain="approval-target")
    # ``subject_hash`` is already the exact, versioned digest of the reviewed
    # recipient/form/payload.  Bind the common ledger to that identity rather
    # than re-hashing presentation details that an adapter may format
    # differently at execution time.
    payload_hash = canonical_hash(
        {"subject_hash": subject_hash}, domain="approval-payload")
    bindings = {
        "run_id": (application or {}).get("workflow_run_id"),
        "plan_hash": (application or {}).get("workflow_plan_hash"),
        "step_id": gate,
        "capability_id": capability.capability_id,
        "capability_version": capability.semantic_version,
        "action_kind": action_kind,
        "target_hash": target_hash,
        "normalized_payload_hash": payload_hash,
        "policy_id": capability.approval_policy_id,
        "policy_version": "1",
        "domain_ref": application_id,
        "domain_version": int((application or {}).get("version") or 1),
        "connector_id": connector_id,
        "connector_binding_version": "connection-v1",
    }
    if requested_by_actor_id:
        # Platform-v2 approvals are anchored to a real immutable run and step.
        # The general external-consequence workflow covers standalone email,
        # calendar, and portal actions without pretending they belong to a
        # grant-application plan.
        from services.platform_approval_service import PlatformApprovalService
        from services.workflow_projection_service import WorkflowProjectionService

        projection = WorkflowProjectionService()
        if gate == "submit_application":
            run = await projection.ensure_grant_application(
                workspace_id=founder_id, application_id=application_id,
                originating_actor_id=requested_by_actor_id)
        else:
            run = await projection.ensure_external_consequence(
                workspace_id=founder_id, domain_ref=application_id,
                originating_actor_id=requested_by_actor_id)
        if run.get("error"):
            return run
        step = await projection.runtime.create_step(
            run["run_id"],
            step_key=f"human_approval:{action_kind}:{subject_hash[:24]}",
            idempotency_key=f"approval:{gate}:{subject_hash}")
        if step.get("error"):
            return step
        if application is not None:
            await firestore.update_application(
                application_id, workflow_run_id=run["run_id"],
                workflow_plan_hash=run["plan_hash"])
        requested = await PlatformApprovalService().request(
            workspace_id=founder_id,
            requested_by_actor_id=requested_by_actor_id,
            run_id=run["run_id"], plan_hash=run["plan_hash"],
            step_id=step["step_id"],
            capability_id=capability.capability_id,
            capability_version=capability.semantic_version,
            action_kind=action_kind, target={"target": application_id},
            payload={"subject_hash": subject_hash},
            policy_id=capability.approval_policy_id, policy_version="1",
            domain_ref=application_id,
            domain_version=int((application or {}).get("version") or 1),
            connector_id=connector_id,
            connector_binding_version="connection-v1",
            client_request_id=canonical_hash({
                "session_id": session_id, "gate": gate,
                "target": application_id, "subject_hash": subject_hash,
            }, domain="approval-request-id"),
            ttl_minutes=_TTL, origin_session_id=session_id,
            legacy_target=application_id, legacy_gate=gate,
            presentation_details=details,
            approval_domain=("GRANT_APPLICATION" if gate == "submit_application"
                             else "PLATFORM_CONSEQUENCE"))
        if requested.get("error"):
            return requested
        approval = requested["approval"]
        await firestore.audit(
            "agent:approval", "request_approval",
            f"approvals/{approval['approval_id']}", "success", f"gate={gate}")
        return {"status": "success",
                "approval_id": approval["approval_id"],
                "existing": bool(requested.get("duplicate"))}
    if os.environ.get("K_SERVICE"):
        return {"status": "error", "error": True,
                "error_code": "interactive_actor_required",
                "message": "Approval requests require the signed-in actor."}
    approval_id = await firestore.create_approval(
        application_id, gate, _TTL, details=details,
        founder_id=founder_id, session_id=session_id, subject_hash=subject_hash,
        bindings=bindings)
    await firestore.audit("agent:form_filler", "request_approval",
                          f"applications/{application_id}", "success", f"gate={gate}")
    return {"status": "success", "approval_id": approval_id}  # never the token


async def resolve(approval_id: str, decision: str, founder_id: str,
                  session_id: str) -> dict:
    return await firestore.resolve_approval_decision(
        approval_id, decision, founder_id, session_id)


async def resolve_for_principal(*, principal: ActorPrincipal,
                                approval_id: str, decision: str,
                                session_id: str) -> dict:
    """Decide as the current interactive human; initiator may equal approver."""
    gate = authorize(principal, "resolve_approval", require_fresh=True)
    if gate.get("error"):
        return gate
    if decision not in {"grant", "deny"}:
        return {"status": "error", "error": True,
                "error_code": "invalid_contract",
                "message": "Decision must be grant or deny."}
    try:
        approval = await firestore.get_approval_for_workspace(
            principal.workspace_id, approval_id)
    except Exception:
        # The schema discriminator is authority-bearing: if it cannot be
        # loaded, never guess that a v2 approval is a legacy row and route it
        # through the weaker compatibility resolver.
        return {"status": "error", "error": True,
                "error_code": "authoritative_state_unavailable",
                "message": "Approval state could not be verified."}
    if (approval and int(approval.get("schema_version") or 0) == 2
            and approval.get("approval_id") == approval_id
            and approval.get("version") is not None):
        from services.platform_approval_service import PlatformApprovalService

        return await PlatformApprovalService().decide(
            principal=principal, approval_id=approval_id,
            decision="GRANT" if decision == "grant" else "DENY")
    return await firestore.resolve_approval_decision(
        approval_id, decision, principal.workspace_id, session_id,
        deciding_actor_id=principal.actor_id)


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
            application_id, await firestore.get_application(
                application_id, founder_id))
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

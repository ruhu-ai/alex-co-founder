"""Browser tools (docs/05, 09). Errors as data: tools never raise to the model.

Guards (G2/G3) and the staleness fence live in code here and in the
before_tool_callback — never in prompts (docs/README principle 7).

Page sessions are resolved through the central browser runtime supervisor.
"""

import logging
import os
from urllib.parse import urlparse

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import add_pending_signal, failed, run


def _session_key(tool_context: ToolContext) -> dict[str, str]:
    session = getattr(tool_context, "session", None)
    return {
        "app_name": getattr(session, "app_name", "") or "co_founder",
        "user_id": getattr(tool_context, "user_id", "")
        or getattr(session, "user_id", "")
        or tool_context.state.get(ss.K_USER_PROFILE_ID, "founder"),
        "session_id": getattr(session, "id", "")
        or getattr(session, "session_id", ""),
    }


def _remember_questions(app_id: str, fields: list[dict]) -> None:
    """Persist the questions the form asks onto the application.

    `inspect()` has always read every field's label; the result was used for a
    count and a page signature and then dropped. The drafter therefore had no
    idea what the form asked and answered only the sections that happened to
    exist — 10 of 16 in the observed run. The questions are the specification
    for the document, so they have to outlive the browser session.
    """
    if not app_id or not fields:
        return
    from services import firestore

    questions = [{"name": f.get("name", ""), "label": f.get("label", "").strip(),
                  "type": f.get("type", ""), "required": bool(f.get("required"))}
                 for f in fields if f.get("name")]
    try:
        run(firestore.update_application(app_id, form_questions=questions))
    except Exception as exc:  # noqa: BLE001 — never break the fill over a side effect
        logging.getLogger(__name__).warning(
            "could not persist form questions for %s: %s", app_id, exc)


def _register_fill(result: dict, app_id: str, tool_context: ToolContext) -> dict:
    from services import browser_service

    # browser_service created/projected the durable fill run before credentials
    # were entered. Do not register a second page owner here.
    registered = {
        "status": "success",
        "run_id": result["run_id"],
        "screenshot_artifact": result.get("screenshot_artifact"),
    }
    tool_context.state[ss.K_BROWSER_STATUS] = run(
        browser_service.browser_status_projection(_session_key(tool_context)))
    return registered


def _portal_session(app_id: str) -> dict | None:
    """Resolve the live fill page from the single owning supervisor."""
    from services import browser_service

    return browser_service.fill_session_for_application(app_id)


def _remember_signature(app_id: str, signature: str) -> None:
    from services import browser_service

    browser_service.set_fill_signature(app_id, signature)


def _close_result(result: dict, reason: str = "agent_close") -> None:
    """Close a fill through durable run ordering, never through raw context."""
    from services import browser_service

    run_id = result.get("run_id")
    if run_id:
        run(browser_service.close_run(run_id, reason, "agent:form_filler"))


def _mock_mailbox_url(portal_url: str, email: str) -> str:
    """Mock portal seam (docs/17): its /_mailbox endpoint stands in for the
    real inbox during offline registration tests."""
    parsed = urlparse(portal_url)
    host = parsed.netloc
    configured_mock = urlparse(os.environ.get("MOCK_PORTAL_URL", "")).netloc
    if host.startswith(("127.0.0.1", "localhost")) or (
            configured_mock and host == configured_mock):
        return f"{parsed.scheme or 'http'}://{host}/_mailbox/{email}"
    return ""


def _creds() -> tuple[str, str] | None:
    """Portal creds: Secret Manager in prod (docs/12); env fallback for local dev."""
    try:
        import json

        from services import secrets

        raw = secrets.get(os.environ.get("PORTAL_SECRET_NAME", "mock-portal-creds"))
        data = json.loads(raw)
        return data["username"], data["password"]
    except Exception:
        if os.environ.get("K_SERVICE"):
            return None
        # Local-dev fallback bypasses secrets.get(), which is what normally
        # registers a credential with the scrubber — do it here too so dev logs
        # and error data redact it the same way production does.
        from services import log_scrub

        username = os.environ.get("MOCK_PORTAL_USERNAME", "demo-founder")
        password = os.environ.get("MOCK_PORTAL_PASSWORD", "demo-pass-2026")
        log_scrub.register_secret(password)
        return (username, password)


def register_account(portal_url: str, email: str, tool_context: ToolContext) -> dict:
    """Create an account on a program portal as Alex (docs/17).

    Args:
        portal_url: The portal's base URL, e.g. "https://flagship.aplica.500.co".
        email: Alex's address for the account (alex@ruhu.ai) — never the
            founder's personal address.

    Returns:
        dict with status. On success: account created, email verification
        handled automatically (verification link read from Alex's mailbox),
        credentials stored in Secret Manager (never returned). Bot protection
        or SSO-only pages return a clean blocker — never worked around. Every
        registration is audited.
    """
    from services import alex_mailbox, approval_service, browser_service, firestore, portal_accounts

    host = urlparse(portal_url).netloc
    application_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    if not application_id:
        return {"status": "error", "error": True,
                "message": "Portal registration requires an active application so browser work can be observed and recovered."}
    identity = _session_key(tool_context)
    existing = portal_accounts.get_credential(host)
    if existing and existing.get("verified", True):
        return {"status": "success",
                "note": f"an account for {host} already exists — use sign_in"}
    if existing:
        mail = run(alex_mailbox.wait_for_email(
            from_contains=host.split(":")[0], timeout_s=0, poll_s=0,
            mailbox_url=_mock_mailbox_url(portal_url, email)))
        if mail.get("status") != "success":
            add_pending_signal(tool_context, "portal_verification")
            return {"status": "waiting", "verified": False,
                    "pending_signal": "portal_verification",
                    "message": "Still waiting for the portal verification email event."}
        if not mail.get("link"):
            return {"status": "blocked", "error": True, "verified": False,
                    "message": "The portal sent a verification code that needs the founder to enter it."}
        verified_result = run(browser_service.verify_registration_link(
            mail["link"], host, session_key=identity,
            application_id=application_id))
        if verified_result.get("status") != "success":
            return verified_result
        stored = portal_accounts.mark_verified(host)
        if stored.get("status") != "success":
            return stored
        run(firestore.complete_portal_registration(host))
        tool_context.state[ss.K_PENDING_SIGNALS] = [
            signal for signal in tool_context.state.get(ss.K_PENDING_SIGNALS, [])
            if signal != "portal_verification"
        ]
        run(firestore.audit(
            "agent:form_filler", "register_account", f"portal:{host}", "success",
            f"account verification completed for {email}"))
        return {"status": "success", "host": host, "verified": True,
                "note": "account verified; credentials remain stored server-side"}
    target = f"portal:{host}"
    approval = run(firestore.find_valid_approval(
        target, gate="create_portal_account", founder_id=identity["user_id"],
        session_id=identity["session_id"]))
    if failed(approval):  # a lookup ERROR is not "no approval" — surface it
        return approval
    if not approval:
        requested = run(approval_service.request_approval(
            target, gate="create_portal_account",
            details={"portal_url": portal_url, "email": email},
            founder_id=identity["user_id"], session_id=identity["session_id"]))
        run(firestore.audit(
            "agent:form_filler", "register_account", target, "refused",
            "no GRANTED approval — requested founder approval"))
        return {
            "status": "needs_approval",
            "error": True,
            "approval_id": requested.get("approval_id"),
            "message": "Creating this portal account requires founder approval.",
        }
    claimed = run(firestore.claim_approval(approval["id"]))
    if failed(claimed):  # claim ERROR must never read as "claimed" — the
        return claimed   # single-use approval would leak unconsumed
    if not claimed:
        return {"status": "error", "error": True,
                "message": "Portal-account approval was already used or expired."}
    password = portal_accounts.generate_password()
    result = run(browser_service.register(
        portal_url, email, password, session_key=identity,
        application_id=application_id))
    if result.get("status") != "success":
        return result
    body = result.get("body", "")
    if "already exists" in body.lower():
        # Benign collision: the portal already has an account for this host. That
        # is the end state create_portal_account was after, so treat it as an
        # idempotent success rather than burning the founder's single-use
        # approval on a "failure". Close the context so the browser doesn't leak.
        _close_result(result)
        run(firestore.audit(
            "agent:form_filler", "register_account", target, "success",
            f"account already existed for {email}; no new account created"))
        return {"status": "success", "host": host, "verified": False,
                "already_existed": True,
                "note": f"{host} already has an account for {email}; if the founder "
                        "has the credential, share it once so I can sign in"}
    verified = True
    if "check your email" in body.lower() or "verify" in body.lower():
        # One event-time check only. A delayed message wakes the dormant agent;
        # this tool never holds a worker in a mailbox polling loop.
        mail = run(alex_mailbox.wait_for_email(
            from_contains=host.split(":")[0],
            timeout_s=0, poll_s=0,
            mailbox_url=_mock_mailbox_url(portal_url, email)))
        if mail.get("status") != "success":
            stored = portal_accounts.store_credential(
                host, email, password, verified=False, portal_url=portal_url,
                session_id=identity["session_id"])
            _close_result(result)
            if stored.get("status") != "success":
                return stored
            run(firestore.save_pending_portal_registration(
                host, identity["user_id"], identity["session_id"],
                portal_url, email, application_id))
            add_pending_signal(tool_context, "portal_verification")
            run(firestore.audit(
                "agent:form_filler", "register_account", target, "waiting",
                f"account created for {email}; waiting for verification event"))
            return {"status": "waiting", "verified": False,
                    "pending_signal": "portal_verification",
                    "message": "Account created; waiting for the verification email event."}
        if mail.get("link"):
            verified_result = run(browser_service.verify_registration_link(
                mail["link"], host, session_key=identity,
                application_id=application_id))
            if verified_result.get("status") != "success":
                _close_result(result)
                return verified_result
        elif mail.get("code"):
            page = result["page"]
            # Register the OTP with the log scrubber before it is typed: a
            # Playwright failure below surfaces the selector AND the value in
            # its exception string, which is returned as error data.
            from services import log_scrub

            log_scrub.register_secret(str(mail["code"]))
            # run() turns a Playwright failure into an error dict (never raises),
            # so an unchecked fill/click would silently store verified=True on a
            # code entry that never happened. Gate verified on both succeeding.
            filled = run(page.fill("input[name='code'], input[type='text']", mail["code"]))
            if failed(filled):
                _close_result(result)
                return filled
            clicked = run(page.click("button[type='submit']"))
            if failed(clicked):
                _close_result(result)
                return clicked
        verified = True
    stored = portal_accounts.store_credential(
        host, email, password, verified=verified, portal_url=portal_url,
        session_id=identity["session_id"])
    if stored.get("status") != "success":
        _close_result(result)
        return stored
    run(firestore.complete_portal_registration(host))
    _close_result(result)

    run(firestore.audit(
        actor="agent:form_filler", action="register_account", target=host,
        result="success",
        detail=f"account created for {email}; verified={verified}"))
    return {"status": "success", "host": host, "verified": verified,
            "note": "account created and verified; credentials stored server-side"}


def sign_in(portal_url: str, tool_context: ToolContext) -> dict:
    """Sign in to a portal with the stored per-portal credential (docs/17).

    Args:
        portal_url: The portal's URL. The credential is looked up by host —
            never supplied by the model.

    Returns:
        dict with status, page title, and field count after landing. Error when
        no account exists (register_account first).
    """
    from services import browser_service, portal_accounts

    host = urlparse(portal_url).netloc
    cred = portal_accounts.get_credential(host)
    if not cred:
        return {"status": "error", "error": True,
                "message": f"no account for {host} — call register_account first"}
    if cred.get("verified") is False:
        return {"status": "waiting", "error": True,
                "message": f"the account for {host} is waiting for email verification; "
                           "call register_account again after the verification event"}
    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    result = run(browser_service.open_and_login(
        portal_url, cred["email"], cred["password"],
        session_key=_session_key(tool_context), application_id=app_id))
    if result.get("status") != "success":
        return result
    _register_fill(result, app_id, tool_context)
    inspect = run(browser_service.inspect(result["page"]))
    if inspect.get("status") == "success":
        _remember_questions(app_id, inspect["fields"])
        _remember_signature(app_id, inspect["signature"])
        tool_context.state[ss.K_PORTAL_SIGNATURE] = inspect["signature"]
        return {"status": "success", "title": result["title"],
                "field_count": len(inspect["fields"]), "signature": inspect["signature"]}
    return {"status": "success", "title": result["title"], "field_count": 0}


def open_portal(application_url: str, tool_context: ToolContext) -> dict:
    """Open the application portal and log in (guard G3: refuses before APPROVED).

    Args:
        application_url: The portal's application URL from the opportunity record.

    Returns:
        dict with status, page title, and field count. Credentials are fetched
        by name from Secret Manager at execution time and never enter state,
        prompts, or logs.
    """
    if tool_context.state.get(ss.K_CURRENT_STEP) not in (
            ss.ApplicationStep.APPROVED, ss.ApplicationStep.FORM_FILLING,
            ss.ApplicationStep.AWAITING_SUBMIT_APPROVAL):
        return {"status": "error", "error": True,
                "message": "Gate G3: portal access only after the application is APPROVED."}

    from services import browser_service

    credentials = _creds()
    if credentials is None:
        return {"status": "error", "error": True,
                "message": "Portal credentials are unavailable in Secret Manager."}
    username, password = credentials
    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    result = run(browser_service.open_and_login(
        application_url, username, password,
        session_key=_session_key(tool_context), application_id=app_id))
    if result.get("status") != "success":
        return result
    _register_fill(result, app_id, tool_context)
    inspect = run(browser_service.inspect(result["page"]))
    if inspect.get("status") == "success":
        _remember_questions(app_id, inspect["fields"])
        _remember_signature(app_id, inspect["signature"])
        tool_context.state[ss.K_PORTAL_SIGNATURE] = inspect["signature"]
        return {"status": "success", "title": result["title"],
                "field_count": len(inspect["fields"]), "signature": inspect["signature"]}
    return {"status": "success", "title": result["title"], "field_count": 0,
            "note": "no form fields detected on landing page"}


def inspect_form(tool_context: ToolContext) -> dict:
    """List the visible form fields: name, label, type, required.

    Returns:
        dict with status and a field summary; the full field list is saved as
        an artifact, never dumped into the conversation.
    """
    from services import browser_service, storage

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    session = _portal_session(app_id)
    if not session:
        return {"status": "error", "error": True, "message": "no open portal — call open_portal first"}
    result = run(browser_service.inspect(session["page"]))
    if result.get("status") == "success":
        import json

        artifact = f"inspect_{app_id}.json"
        storage.save_text(artifact, json.dumps(result["fields"], indent=2))
        _remember_questions(app_id, result["fields"])
        _remember_signature(app_id, result["signature"])
        tool_context.state[ss.K_PORTAL_SIGNATURE] = result["signature"]
        return {"status": "success", "field_count": len(result["fields"]),
                "signature": result["signature"], "artifact": artifact,
                "summary": [f["name"] for f in result["fields"]][:10]}
    return result


def verify_page_state(expected_signature: str, tool_context: ToolContext) -> dict:
    """Hash the live form's field signature and compare with the inspected one.

    Args:
        expected_signature: The field-name hash captured at inspection time.

    Returns:
        dict with status, match (bool), and current_signature. A mismatch means
        the agent must stop — enforced in code by the fence callback (docs/09).
    """
    from services import browser_service

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    session = _portal_session(app_id)
    if not session:
        return {"status": "error", "error": True, "message": "no open portal"}
    current = run(browser_service.inspect(session["page"]))
    if current.get("status") != "success":
        return current
    return {"status": "success", "match": current["signature"] == expected_signature,
            "current_signature": current["signature"]}


def map_form_requirements(tool_context: ToolContext) -> dict:
    """Tier 1 vision reconnaissance (docs/09): navigate the form with the
    screenshot->Gemini->action loop and emit a form_map artifact (steps,
    fields, requirement text, required materials). Time-boxed at 90 seconds.

    Returns:
        dict with status, a <=400-char summary, and the form_map artifact name.
        Cached per portal_state_hash.
    """
    from services import recon_service, storage

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    session = _portal_session(app_id)
    if not session:
        return {"status": "error", "error": True, "message": "no open portal"}
    artifact = f"form_map_{app_id}.json"
    if session.get("signature") and storage.exists(artifact):
        return {"status": "success", "artifact": artifact, "cached": True,
                "summary": "recon map reused (page signature unchanged)"}
    return run(recon_service.run_recon(session["page"], "map every field and requirement of this application form", app_id))


def vision_step(goal: str, tool_context: ToolContext) -> dict:
    """One bounded vision action (recon/recovery only).

    Args:
        goal: What this step should achieve, e.g. "open the eligibility section".

    Returns:
        dict with status and the action taken. Allowlist is click/type/select/
        scroll/navigate_back — submission controls are excluded by construction.
    """
    from services import recon_service

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    session = _portal_session(app_id)
    if not session:
        return {"status": "error", "error": True, "message": "no open portal"}
    return run(recon_service.vision_step(session["page"], goal, [], app_id))


def get_approved_sections(tool_context: ToolContext) -> dict:
    """Read the APPROVED draft sections for the active application from the
    pipeline store — the ONLY legitimate source of fill values.

    Call this right before building the fill_fields mapping. Never fill from
    memory of the conversation: long sessions compact old turns away, and a
    paraphrased answer is an unapproved answer.

    Returns:
        dict with status, sections: [{section_key, content, word_count,
        notes}] (APPROVED only), and last_fill_mapping when a previous fill
        stored one (use it to re-fill after a browser restart).
    """
    from services import firestore

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    if not app_id:
        return {"status": "error", "error": True, "message": "no active application"}
    app_doc = run(firestore.get_application(app_id))
    if failed(app_doc):
        return app_doc
    if not app_doc:
        return {"status": "error", "error": True,
                "message": f"application {app_id} not found"}
    approved = [
        {"section_key": s.get("section_key", ""), "content": s.get("content", ""),
         "word_count": s.get("word_count", 0), "notes": s.get("notes", "")}
        for s in app_doc.get("draft_sections", [])
        if s.get("status") == ss.SectionStatus.APPROVED
    ]
    return {"status": "success", "application_id": app_id,
            "sections": approved, "approved_count": len(approved),
            "last_fill_mapping": app_doc.get("last_fill_mapping") or {},
            "form_questions": app_doc.get("form_questions") or []}


def fill_fields(mapping: dict, tool_context: ToolContext) -> dict:
    """Fill form fields from APPROVED sections (Tier 0 DOM; Tier 1/2 consult the
    current form_map for unmappable fields).

    Args:
        mapping: field name -> value from approved sections.

    Returns:
        dict with status, filled, total, needs_human (fields only the founder
        can complete). Writes the form_fill_report and runs the post-fill
        vision self-check.
    """
    from services import approval_service, browser_service, firestore, pipeline_service, storage

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    session = _portal_session(app_id)
    if not session:
        return {"status": "error", "error": True, "message": "no open portal — call open_portal first"}

    attachments: dict[str, str] = {}  # Day 7: map file fields to stored attachment artifacts
    run(browser_service.set_fill_phase(app_id, "filling"))
    result = run(browser_service.fill(session["page"], mapping, attachments))
    if result.get("status") != "success":
        return result

    shot_name = f"fillshot_{app_id}_after_fill.png"
    run(browser_service.screenshot(session["page"], storage.artifact_path(shot_name)))
    run(browser_service.update_fill_run(
        session.get("run_id", ""), "fill", f"filled {result['filled']}/{len(mapping)}",
        shot_name))
    # The two hashes the submit gate is bound to (docs/02, docs/22): the live
    # form's field signature and the intended mapping. Without a portal hash the
    # report cannot bind an approval, so re-read it from the page rather than
    # writing a report that can never be submitted.
    portal_state_hash = session.get("signature") or ""
    if not portal_state_hash:
        probe = run(browser_service.inspect(session["page"]))
        if not failed(probe) and probe.get("status") == "success":
            portal_state_hash = probe.get("signature") or ""
            if portal_state_hash:
                _remember_signature(app_id, portal_state_hash)
    fill_mapping_hash = approval_service.mapping_hash(mapping)
    report = {"filled": result["filled"], "total": len(mapping),
              "needs_human": result["needs_human"],
              "portal_state_hash": portal_state_hash or None,
              "mapping_hash": fill_mapping_hash,
              "screenshot_artifact": shot_name, "ran_at": ""}

    async def _persist():
        # last_fill_mapping: the recovery seed — after an instance restart the
        # open page is gone; open_portal + this mapping re-fills without
        # depending on (compactable) chat history.
        await firestore.update_application(
            app_id, form_fill_report=report, last_fill_mapping=mapping)
        await firestore.audit("agent:form_filler", "form_fill",
                              f"applications/{app_id}", "success",
                              f"filled {result['filled']}/{len(mapping)}")

    run(_persist())
    # Walk the legal chain (docs/03): APPROVED → FORM_FILLING →
    # AWAITING_SUBMIT_APPROVAL. The direct leap is refused by the table.
    # A corrective re-fill legitimately runs while the gate is ALREADY armed
    # (partial fills are first-class), so an already-AWAITING state is treated
    # as an idempotent success rather than attempted as an illegal same-state
    # transition — which would surface a misleading "state transition failed".
    # Failures on a real advance are still surfaced in the return.
    app_doc = run(firestore.get_application(app_id))
    current_state = app_doc.get("state") if app_doc else None
    if current_state == ss.ApplicationStep.APPROVED:
        run(pipeline_service.advance_application(
            app_id, ss.ApplicationStep.FORM_FILLING, actor="agent:form_filler"))
        current_state = ss.ApplicationStep.FORM_FILLING
    if current_state == ss.ApplicationStep.AWAITING_SUBMIT_APPROVAL:
        transition = {"status": "success", "already_armed": True}
    else:
        transition = run(pipeline_service.advance_application(
            app_id, ss.ApplicationStep.AWAITING_SUBMIT_APPROVAL, actor="agent:form_filler"))
    approval_requested = None
    subject = approval_service.submit_subject_hash(
        app_id, portal_state_hash, fill_mapping_hash)
    if transition.get("status") == "success":
        run(browser_service.set_fill_phase(app_id, "awaiting_approval"))
        tool_context.state[ss.K_CURRENT_STEP] = ss.ApplicationStep.AWAITING_SUBMIT_APPROVAL
        tool_context.state[ss.K_PENDING_SIGNALS] = ["founder_approval"]
        identity = _session_key(tool_context)
        if subject:
            # request_approval expires any PENDING/GRANTED approval bound to an
            # older subject first: a fill that changed the form or the answers
            # must never leave a grant behind that authorizes the old one.
            approval_requested = run(approval_service.request_approval(
                app_id, founder_id=identity["user_id"],
                session_id=identity["session_id"], subject_hash=subject))
    out = {**result, "total": len(mapping), "form_fill_report": report}
    if transition.get("status") != "success":
        out["warning"] = (f"fill succeeded but the state transition failed: "
                          f"{transition.get('message', 'unknown')}"[:200])
    elif not subject:
        # No portal signature → nothing to bind an approval to. Arming the gate
        # anyway would give the founder a button whose grant can never be
        # honoured; refuse to arm and say what to do instead.
        run(approval_service.invalidate_submit_approvals(
            app_id, reason="new fill produced no portal signature to bind"))
        out["warning"] = ("fill succeeded but the portal form could not be "
                          "hashed, so no submit approval was armed — call "
                          "inspect_form and fill_fields again")
    elif failed(approval_requested) or (
            approval_requested or {}).get("status") != "success":
        out["warning"] = ("fill succeeded but the submit-approval request "
                          f"failed: {(approval_requested or {}).get('message', 'unknown')}"[:200]
                          + " — call request_approval yourself before submitting")
    else:
        out["submit_approval_id"] = approval_requested.get("approval_id")
    return out


def capture_screenshot(label: str, tool_context: ToolContext) -> dict:
    """Save a screenshot artifact of the current page.

    Args:
        label: Short label for the artifact, e.g. "after_fill".

    Returns:
        dict with status and the artifact filename.
    """
    import time

    from services import browser_service, storage

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    session = _portal_session(app_id)
    if not session:
        return {"status": "error", "error": True, "message": "no open portal"}
    name = f"fillshot_{app_id}_{label}_{int(time.time())}.png"
    run(browser_service.screenshot(session["page"], storage.artifact_path(name)))
    run(browser_service.update_fill_run(
        session.get("run_id", ""), "screenshot", label, name))
    return {"status": "success", "artifact": name}


def _reopen_for_submit(app_id: str, tool_context: ToolContext) -> tuple[dict | None, dict | None]:
    """Recover the filled portal page after an instance restart.

    The founder already approved the filled form, but the live page lived only
    in the process-owned supervisor, which an instance restart / scale-to-zero
    clears. Rebuild it deterministically from durable truth: reopen the portal
    and re-fill the APPROVED `last_fill_mapping`. Returns (session, None) on
    success or (None, error_dict) — errors are data, never raised. If recovery
    is not possible the error is actionable rather than a bare "no open portal".
    """
    from services import approval_service, browser_service, firestore, portal_accounts

    app_doc = run(firestore.get_application(app_id))
    if failed(app_doc):
        return None, app_doc
    if not app_doc:
        return None, {"status": "error", "error": True,
                      "message": f"application {app_id} not found"}
    mapping = app_doc.get("last_fill_mapping") or {}
    opp = None
    opp_id = app_doc.get("opportunity_id", "")
    if opp_id:
        fetched = run(firestore.get_opportunity(opp_id))
        opp = fetched if not failed(fetched) else None
    application_url = ((app_doc.get("submission") or {}).get("portal_url")
                       or (opp or {}).get("application_url", ""))
    recovery_hint = {
        "status": "error", "error": True, "error_code": "portal_session_lost",
        "retriable": True,
        "message": ("The filled portal page was lost (the server restarted). "
                    "Re-open it and re-fill before submitting: call "
                    "get_approved_sections, then open_portal, then fill_fields "
                    "with the approved mapping, then submit_form again."),
    }
    if not mapping or not application_url:
        return None, recovery_hint

    # The approved subject, from durable truth. A report that carries neither
    # hash cannot prove what the founder saw, so recovery refuses rather than
    # reopening a portal under a grant nothing binds (fail closed).
    approved_report = app_doc.get("form_fill_report") or {}
    approved_portal_hash = approved_report.get("portal_state_hash") or ""
    approved_mapping_hash = approved_report.get("mapping_hash") or ""
    if not approved_portal_hash or not approved_mapping_hash:
        run(approval_service.invalidate_submit_approvals(
            app_id, reason="fill report carries no portal/mapping binding"))
        run(firestore.audit(
            "agent:form_filler", "submit_recover", f"applications/{app_id}",
            "refused", "fill report has no portal_state_hash/mapping_hash binding"))
        return None, {
            "status": "error", "error": True, "stale": True,
            "error_code": "approval_binding_missing",
            "message": ("The stored fill report does not record which form and "
                        "answers the founder approved, so this submission cannot "
                        "be bound to their approval. Call inspect_form and "
                        "fill_fields again to write a fresh report, then ask the "
                        "founder to approve it."),
        }
    # The re-fill must reapply exactly the approved answers. If the durable
    # mapping drifted from the approved report, refuse before typing anything
    # into the portal.
    current_mapping_hash = approval_service.mapping_hash(mapping)
    if current_mapping_hash != approved_mapping_hash:
        run(approval_service.invalidate_submit_approvals(
            app_id, reason="intended answers changed since the founder approved"))
        run(firestore.audit(
            "agent:form_filler", "submit_recover", f"applications/{app_id}",
            "refused", "intended fill mapping changed since the founder approved"))
        return None, {
            "status": "error", "error": True, "stale": True,
            "error_code": "mapping_changed",
            "message": ("The answers destined for this form changed since the "
                        "founder approved the fill, so the existing approval no "
                        "longer covers it. Call fill_fields again to re-arm a "
                        "fresh approval before submitting."),
        }

    host = urlparse(application_url).netloc
    cred = portal_accounts.get_credential(host)
    if cred and cred.get("email"):  # a registered-account portal (sign_in path)
        email, password = cred.get("email"), cred.get("password")
    else:  # the demo / mock portal path (open_portal creds)
        creds = _creds()
        if creds is None:
            return None, recovery_hint
        email, password = creds

    opened = run(browser_service.open_and_login(
        application_url, email, password,
        session_key=_session_key(tool_context), application_id=app_id))
    if opened.get("status") != "success":
        return None, opened
    _register_fill(opened, app_id, tool_context)
    session = _portal_session(app_id)
    if not session:  # errors are data: never dereference a missing session
        return None, recovery_hint
    inspect = run(browser_service.inspect(opened["page"]))
    if inspect.get("status") != "success":
        return None, (inspect if failed(inspect) else recovery_hint)
    # Bind the founder's existing grant to the form they actually approved.
    # The staleness fence is skipped on this path (no live session to compare),
    # so without this check a portal that changed between approval and restart
    # would be submitted under a grant issued for the previous form state. The
    # comparison is unconditional: a missing approved hash already refused above.
    if inspect["signature"] != approved_portal_hash:
        run(approval_service.invalidate_submit_approvals(
            app_id, reason="portal form changed since the founder approved"))
        run(firestore.audit(
            "agent:form_filler", "submit_recover", f"applications/{app_id}",
            "refused", "portal form changed since the founder approved this fill"))
        return None, {
            "status": "error", "error": True, "stale": True,
            "error_code": "portal_state_changed",
            "message": ("The portal form changed since the founder approved this "
                        "submission, so the existing approval no longer covers it. "
                        "Call inspect_form and fill_fields again to re-arm a fresh "
                        "approval before submitting."),
            "approved_signature": approved_portal_hash,
            "current_signature": inspect["signature"],
        }
    _remember_signature(app_id, inspect["signature"])
    tool_context.state[ss.K_PORTAL_SIGNATURE] = inspect["signature"]
    filled = run(browser_service.fill(session["page"], mapping, {}))
    if failed(filled) or filled.get("status") != "success":
        return None, (filled if failed(filled) else
                      {"status": "error", "error": True,
                       "message": "re-fill after portal recovery failed"})
    # Re-derive after the re-fill: the values that actually went into the page
    # are the ones the grant has to cover, so nothing that mutated `mapping`
    # mid-recovery can slip past the binding.
    if approval_service.mapping_hash(mapping) != approved_mapping_hash:
        run(approval_service.invalidate_submit_approvals(
            app_id, reason="intended answers changed during recovery re-fill"))
        run(firestore.audit(
            "agent:form_filler", "submit_recover", f"applications/{app_id}",
            "refused", "fill mapping changed during recovery re-fill"))
        return None, {
            "status": "error", "error": True, "stale": True,
            "error_code": "mapping_changed",
            "message": ("The answers written into the form during recovery no "
                        "longer match the ones the founder approved. Call "
                        "fill_fields again to re-arm a fresh approval."),
        }
    run(firestore.audit(
        "agent:form_filler", "submit_recover", f"applications/{app_id}", "success",
        f"reopened portal and re-filled {filled.get('filled', 0)}/{len(mapping)} "
        "approved fields after session loss"))
    return session, None


def _submit_binding(app_id: str, session: dict | None) -> tuple[str, dict | None]:
    """The subject hash the founder's approval must carry, or a refusal.

    Derived from `applications.form_fill_report` — the authority (docs/22) —
    and cross-checked against the live page's signature so a session whose form
    drifted cannot submit under the report's grant. Returns ("", error) rather
    than a permissive default: an unbindable submit is refused.
    """
    from services import approval_service, browser_service, firestore

    app_doc = run(firestore.get_application(app_id))
    if failed(app_doc):
        return "", app_doc
    report = (app_doc or {}).get("form_fill_report") or {}
    portal_state_hash = report.get("portal_state_hash") or ""
    fill_mapping_hash = report.get("mapping_hash") or ""
    if not portal_state_hash or not fill_mapping_hash:
        run(approval_service.invalidate_submit_approvals(
            app_id, reason="fill report carries no portal/mapping binding"))
        run(firestore.audit(
            "agent:form_filler", "submit", f"applications/{app_id}", "refused",
            "fill report has no portal_state_hash/mapping_hash binding"))
        return "", {
            "status": "error", "error": True,
            "error_code": "approval_binding_missing",
            "message": ("This application's fill report does not record which "
                        "form and answers the founder approved, so no approval "
                        "can cover this submission. Call inspect_form and "
                        "fill_fields again, then ask the founder to approve the "
                        "new fill."),
        }
    page = (session or {}).get("page")
    if page is None:
        return "", {
            "status": "error", "error": True,
            "error_code": "portal_state_unverifiable",
            "message": ("The live portal page is unavailable, so the approved "
                        "form state cannot be verified. Re-open and re-fill the "
                        "portal before submitting."),
        }
    # Never trust the signature cached when the form was opened or filled. A
    # portal can mutate labels, types, or required-state while approval is
    # pending. Re-read the DOM at the last reversible boundary, immediately
    # before resolving/claiming the founder's grant.
    inspected = run(browser_service.inspect(page))
    if failed(inspected) or inspected.get("status") != "success" \
            or not inspected.get("signature"):
        return "", {
            "status": "error", "error": True,
            "error_code": "portal_state_unverifiable",
            "message": ("The live portal form could not be verified immediately "
                        "before submission. Nothing was submitted; retry after "
                        "the form is stable."),
        }
    live = inspected["signature"]
    _remember_signature(app_id, live)
    if live != portal_state_hash:
        run(approval_service.invalidate_submit_approvals(
            app_id, reason="live portal form differs from the approved report"))
        run(firestore.audit(
            "agent:form_filler", "submit", f"applications/{app_id}", "refused",
            "live portal signature differs from the approved fill report"))
        return "", {
            "status": "error", "error": True, "stale": True,
            "error_code": "portal_state_changed",
            "message": ("The live portal form no longer matches the fill the "
                        "founder approved. Call inspect_form and fill_fields "
                        "again to re-arm a fresh approval before submitting."),
            "approved_signature": portal_state_hash,
            "current_signature": live,
        }
    return approval_service.submit_subject_hash(
        app_id, portal_state_hash, fill_mapping_hash), None


def submit_form(tool_context: ToolContext) -> dict:
    """Submit the filled form (guard G2).

    Deliberately takes NO token argument: a token argument would put the secret
    in the model's tool call. The approval is resolved server-side (docs/12) —
    the GRANTED, unexpired, unconsumed approval for the active application is
    looked up; if none exists this refuses and writes an audit row. Sends the
    derived Idempotency-Key header so retries can never double-submit.

    Returns:
        dict with status and the portal confirmation_id on success.
    """
    from services import approval_service, browser_service, firestore, pipeline_service

    state = tool_context.state
    app_id = state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    founder_id = state.get(ss.K_USER_PROFILE_ID, "founder")

    if state.get(ss.K_CURRENT_STEP) != ss.ApplicationStep.AWAITING_SUBMIT_APPROVAL:
        return {"status": "error", "error": True,
                "message": f"Gate G2: submit only from AWAITING_SUBMIT_APPROVAL "
                           f"(current: {state.get(ss.K_CURRENT_STEP)})"}

    key = pipeline_service.derive_submit_key(founder_id, app_id)
    prior = run(firestore.find_successful_action(key))
    if failed(prior):  # cannot verify idempotency → fail closed, do NOT claim
        return {"status": "error", "error": True,  # a prior submission exists
                "message": f"Could not check submission idempotency: "
                           f"{prior.get('message', '')}"[:300]}
    if prior:
        return {"status": "error", "error": True,
                "message": "Already submitted", "confirmation_id": prior.get("detail", "")}

    session = _portal_session(app_id)
    if not session:
        # The page lived only in memory and was lost to a restart — rebuild it
        # from the APPROVED mapping rather than dead-ending the granted submit.
        session, recovery_error = _reopen_for_submit(app_id, tool_context)
        if recovery_error:
            return recovery_error

    identity = _session_key(tool_context)
    subject, binding_error = _submit_binding(app_id, session)
    if binding_error:
        return binding_error
    approval = run(approval_service.resolve_for_submit(
        app_id, founder_id=identity["user_id"], session_id=identity["session_id"],
        expected_subject_hash=subject))
    if approval.get("status") != "success":
        return approval

    page_host = urlparse(session["page"].url).netloc
    mock_host = urlparse(os.environ.get("MOCK_PORTAL_URL", "")).netloc
    routing = None
    if page_host.startswith(("127.0.0.1", "localhost")) or (mock_host and page_host == mock_host):
        routing = {"session_id": identity["session_id"], "application_id": app_id,
                   "user_id": identity["user_id"]}
    run(browser_service.set_fill_phase(app_id, "submitting"))
    result = run(browser_service.submit(session["page"], key, routing=routing))
    if result.get("status") != "success":
        # Failed attempts are audited too — and the approval stays unconsumed
        # so the founder's grant survives a transient portal failure.
        run(firestore.audit("agent:form_filler", "submit", f"applications/{app_id}",
                            "error", str(result.get("message", ""))[:180],
                            idempotency_key=key))
        return result

    confirmation = result["confirmation_id"]

    async def _persist():
        # Consume the single-use approval only now — the side effect succeeded.
        await approval_service.consume(approval["approval_id"])
        await firestore.update_application(
            app_id, submission={"confirmation_id": confirmation, "portal_url": session["page"].url})
        await firestore.audit("agent:form_filler", "submit", f"applications/{app_id}",
                              "success", confirmation, idempotency_key=key)

    run(_persist())
    transition = run(pipeline_service.advance_application(
        app_id, ss.ApplicationStep.SUBMITTED, actor="agent:form_filler"))
    if transition.get("status") == "success":
        state[ss.K_CURRENT_STEP] = ss.ApplicationStep.SUBMITTED
        state[ss.K_PENDING_SIGNALS] = ["portal_confirmation"]
    if session.get("run_id"):
        run(browser_service.close_run(
            session["run_id"], "agent_close", "agent:form_filler"))
        state[ss.K_BROWSER_STATUS] = {
            "active": False, "kind": None, "run_id": None, "url": None,
            "goal": None, "last_action": None,
        }
    return {"status": "success", "confirmation_id": confirmation}

"""Browser tools (docs/05, 09). Errors as data: tools never raise to the model.

Guards (G2/G3) and the staleness fence live in code here and in the
before_tool_callback — never in prompts (docs/README principle 7).

Page sessions are held in a module registry keyed by application id (single
founder, one active fill at a time — v1).
"""

import logging
import os
from urllib.parse import urlparse

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import add_pending_signal, failed, run

_pages: dict[str, dict] = {}  # application_id -> {"context":..., "page":..., "signature":...}


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

    # A prior portal window may already be open for this application (a repeated
    # open_portal/sign_in, or a re-login after a staleness fence). Close it
    # before replacing the registry entry — otherwise each call leaves an
    # orphaned browser window open and they pile up ("browser keeps opening").
    # This is belt-and-suspenders with register_fill_run's durable supersede,
    # which can miss the live context if the session key drifted or the run was
    # already marked closed.
    prior = _pages.get(app_id)
    if (prior is not None and prior.get("context") is not None
            and prior.get("context") is not result.get("context")):
        run(prior["context"].close())  # run() swallows any close error to data

    registered = run(browser_service.register_fill_run(
        _session_key(tool_context), result["context"], result["page"],
        f"fill application {app_id or 'portal'}",
    ))
    _pages[app_id] = {
        "context": result["context"], "page": result["page"], "signature": None,
        "run_id": registered["run_id"],
    }
    tool_context.state[ss.K_BROWSER_STATUS] = run(
        browser_service.browser_status_projection(_session_key(tool_context)))
    return registered


def _mock_mailbox_url(portal_url: str, email: str) -> str:
    """Mock portal seam (docs/17): its /_mailbox endpoint stands in for the
    real inbox during offline registration tests."""
    host = urlparse(portal_url).netloc
    if host.startswith(("127.0.0.1", "localhost")):
        return f"http://{host}/_mailbox/{email}"
    return ""


def _creds() -> tuple[str, str]:
    """Portal creds: Secret Manager in prod (docs/12); env fallback for local dev."""
    try:
        import json

        from services import secrets

        raw = secrets.get(os.environ.get("PORTAL_SECRET_NAME", "mock-portal-creds"))
        data = json.loads(raw)
        return data["username"], data["password"]
    except Exception:
        return (os.environ.get("MOCK_PORTAL_USERNAME", "demo-founder"),
                os.environ.get("MOCK_PORTAL_PASSWORD", "demo-pass-2026"))


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
    from services import (alex_mailbox, approval_service, browser_service,
                          firestore, portal_accounts)

    host = urlparse(portal_url).netloc
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
            mail["link"], host))
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
    identity = _session_key(tool_context)
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
    result = run(browser_service.register(portal_url, email, password))
    if result.get("status") != "success":
        return result
    body = result.get("body", "")
    if "already exists" in body.lower():
        # Benign collision: the portal already has an account for this host. That
        # is the end state create_portal_account was after, so treat it as an
        # idempotent success rather than burning the founder's single-use
        # approval on a "failure". Close the context so the browser doesn't leak.
        run(result["context"].close())
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
            run(result["context"].close())
            if stored.get("status") != "success":
                return stored
            run(firestore.save_pending_portal_registration(
                host, identity["user_id"], identity["session_id"],
                portal_url, email))
            add_pending_signal(tool_context, "portal_verification")
            run(firestore.audit(
                "agent:form_filler", "register_account", target, "waiting",
                f"account created for {email}; waiting for verification event"))
            return {"status": "waiting", "verified": False,
                    "pending_signal": "portal_verification",
                    "message": "Account created; waiting for the verification email event."}
        if mail.get("link"):
            verified_result = run(browser_service.verify_registration_link(
                mail["link"], host))
            if verified_result.get("status") != "success":
                run(result["context"].close())
                return verified_result
        elif mail.get("code"):
            page = result["page"]
            # run() turns a Playwright failure into an error dict (never raises),
            # so an unchecked fill/click would silently store verified=True on a
            # code entry that never happened. Gate verified on both succeeding.
            filled = run(page.fill("input[name='code'], input[type='text']", mail["code"]))
            if failed(filled):
                run(result["context"].close())
                return filled
            clicked = run(page.click("button[type='submit']"))
            if failed(clicked):
                run(result["context"].close())
                return clicked
        verified = True
    stored = portal_accounts.store_credential(
        host, email, password, verified=verified, portal_url=portal_url,
        session_id=identity["session_id"])
    if stored.get("status") != "success":
        run(result["context"].close())
        return stored
    run(firestore.complete_portal_registration(host))
    run(result["context"].close())

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
    result = run(browser_service.open_and_login(portal_url, cred["email"], cred["password"]))
    if result.get("status") != "success":
        return result
    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    _register_fill(result, app_id, tool_context)
    inspect = run(browser_service.inspect(result["page"]))
    if inspect.get("status") == "success":
        _remember_questions(app_id, inspect["fields"])
        _pages[app_id]["signature"] = inspect["signature"]
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

    username, password = _creds()
    result = run(browser_service.open_and_login(application_url, username, password))
    if result.get("status") != "success":
        return result
    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    _register_fill(result, app_id, tool_context)
    inspect = run(browser_service.inspect(result["page"]))
    if inspect.get("status") == "success":
        _remember_questions(app_id, inspect["fields"])
        _pages[app_id]["signature"] = inspect["signature"]
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
    session = _pages.get(app_id)
    if not session:
        return {"status": "error", "error": True, "message": "no open portal — call open_portal first"}
    result = run(browser_service.inspect(session["page"]))
    if result.get("status") == "success":
        import json

        artifact = f"inspect_{app_id}.json"
        storage.save_text(artifact, json.dumps(result["fields"], indent=2))
        _remember_questions(app_id, result["fields"])
        session["signature"] = result["signature"]
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
    session = _pages.get(app_id)
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
    session = _pages.get(app_id)
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
    session = _pages.get(app_id)
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
    from services import browser_service, firestore, pipeline_service, storage

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    session = _pages.get(app_id)
    if not session:
        return {"status": "error", "error": True, "message": "no open portal — call open_portal first"}

    attachments: dict[str, str] = {}  # Day 7: map file fields to stored attachment artifacts
    result = run(browser_service.fill(session["page"], mapping, attachments))
    if result.get("status") != "success":
        return result

    shot_name = f"fillshot_{app_id}_after_fill.png"
    run(browser_service.screenshot(session["page"], storage.artifact_path(shot_name)))
    run(browser_service.update_fill_run(
        session.get("run_id", ""), "fill", f"filled {result['filled']}/{len(mapping)}",
        shot_name))
    report = {"filled": result["filled"], "total": len(mapping),
              "needs_human": result["needs_human"],
              "portal_state_hash": session.get("signature"),
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
    if transition.get("status") == "success":
        tool_context.state[ss.K_CURRENT_STEP] = ss.ApplicationStep.AWAITING_SUBMIT_APPROVAL
        tool_context.state[ss.K_PENDING_SIGNALS] = ["founder_approval"]
        from services import approval_service

        identity = _session_key(tool_context)
        approval_requested = run(approval_service.request_approval(
            app_id, founder_id=identity["user_id"], session_id=identity["session_id"]))
    out = {**result, "total": len(mapping), "form_fill_report": report}
    if transition.get("status") != "success":
        out["warning"] = (f"fill succeeded but the state transition failed: "
                          f"{transition.get('message', 'unknown')}"[:200])
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
    session = _pages.get(app_id)
    if not session:
        return {"status": "error", "error": True, "message": "no open portal"}
    name = f"fillshot_{app_id}_{label}_{int(time.time())}.png"
    run(browser_service.screenshot(session["page"], storage.artifact_path(name)))
    run(browser_service.update_fill_run(
        session.get("run_id", ""), "screenshot", label, name))
    return {"status": "success", "artifact": name}


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

    session = _pages.get(app_id)
    if not session:
        return {"status": "error", "error": True, "message": "no open portal"}

    identity = _session_key(tool_context)
    approval = run(approval_service.resolve_for_submit(
        app_id, founder_id=identity["user_id"], session_id=identity["session_id"]))
    if approval.get("status") != "success":
        return approval

    page_host = urlparse(session["page"].url).netloc
    mock_host = urlparse(os.environ.get("MOCK_PORTAL_URL", "")).netloc
    routing = None
    if page_host.startswith(("127.0.0.1", "localhost")) or (mock_host and page_host == mock_host):
        routing = {"session_id": identity["session_id"], "application_id": app_id,
                   "user_id": identity["user_id"]}
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

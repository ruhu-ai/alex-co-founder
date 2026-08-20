"""Browser tools (docs/05, 09). Errors as data: tools never raise to the model.

Guards (G2/G3) and the staleness fence live in code here and in the
before_tool_callback — never in prompts (docs/README principle 7).

Page sessions are held in a module registry keyed by application id (single
founder, one active fill at a time — v1).
"""

import os

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import run

_pages: dict[str, dict] = {}  # application_id -> {"context":..., "page":..., "signature":...}


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
    _pages[app_id] = {"context": result["context"], "page": result["page"], "signature": None}
    inspect = run(browser_service.inspect(result["page"]))
    if inspect.get("status") == "success":
        _pages[app_id]["signature"] = inspect["signature"]
        tool_context.state["temp:portal_signature"] = inspect["signature"]
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
        session["signature"] = result["signature"]
        tool_context.state["temp:portal_signature"] = result["signature"]
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
    from services import browser_service, firestore, pipeline_service

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    session = _pages.get(app_id)
    if not session:
        return {"status": "error", "error": True, "message": "no open portal — call open_portal first"}

    attachments: dict[str, str] = {}  # Day 7: map file fields to stored attachment artifacts
    result = run(browser_service.fill(session["page"], mapping, attachments))
    if result.get("status") != "success":
        return result

    report = {"filled": result["filled"], "total": len(mapping),
              "needs_human": result["needs_human"],
              "portal_state_hash": session.get("signature"), "ran_at": ""}

    async def _persist():
        await firestore.update_application(app_id, form_fill_report=report)
        await firestore.audit("agent:form_filler", "form_fill",
                              f"applications/{app_id}", "success",
                              f"filled {result['filled']}/{len(mapping)}")

    run(_persist())
    transition = run(pipeline_service.advance_application(
        app_id, ss.ApplicationStep.AWAITING_SUBMIT_APPROVAL, actor="agent:form_filler"))
    if transition.get("status") == "success":
        tool_context.state[ss.K_CURRENT_STEP] = ss.ApplicationStep.AWAITING_SUBMIT_APPROVAL
        tool_context.state[ss.K_PENDING_SIGNALS] = ["founder_approval"]
        from services import approval_service

        run(approval_service.request_approval(app_id))
    return {**result, "total": len(mapping), "form_fill_report": report}


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
    if prior:
        return {"status": "error", "error": True,
                "message": "Already submitted", "confirmation_id": prior.get("detail", "")}

    approval = run(approval_service.resolve_for_submit(app_id))
    if approval.get("status") != "success":
        return approval

    session = _pages.get(app_id)
    if not session:
        return {"status": "error", "error": True, "message": "no open portal"}

    result = run(browser_service.submit(session["page"], key))
    if result.get("status") != "success":
        return result

    confirmation = result["confirmation_id"]

    async def _persist():
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
    return {"status": "success", "confirmation_id": confirmation}

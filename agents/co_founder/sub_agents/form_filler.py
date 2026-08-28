"""Form-Filler agent — portal navigation + pre-fill (docs/04, 09).

The staleness fence is a before_tool_callback (guards in code, not prompts):
before fill_fields/submit_form it re-verifies the live page signature and
short-circuits the tool by returning a dict when the page changed.
"""

from google.adk.agents import Agent

from ..callbacks import (
    enforce_effect_claims,
    enforce_workflow_tool_contract,
    guard_specialist_entry,
    initialize_session_state,
    track_tool_outcome,
)
from ..config import MODEL
from ..instructions import FORM_FILLER_INSTRUCTION
from ..tools import browser, pipeline

_G3_STEPS = ("APPROVED", "FORM_FILLING", "AWAITING_SUBMIT_APPROVAL")

# Portal-touching tools gated by pipeline step (G3, docs/09). open_portal and
# submit_form also self-check; sign_in and fill_fields previously had NO step
# gate, so a live portal page could be opened and typed into during DRAFTING.
# register_account is deliberately NOT step-gated: account creation has its
# own founder approval (create_portal_account gate) and its email-verification
# resume can legitimately arrive at any pipeline step.
_G3_TOOLS = ("sign_in", "open_portal", "fill_fields", "submit_form")


async def verify_before_action(tool, args, tool_context):
    """Portal gate + staleness fence (docs/09 §safety). Runs before EVERY tool
    call on this agent; gates the portal-touching tools.

    Returning a dict short-circuits the tool — the model receives the dict as
    the tool result and the real fill/submit never executes. Returning None
    lets the tool run.
    """
    name = getattr(tool, "name", "")
    if name in _G3_TOOLS and tool_context.state.get(
            "current_step", "") not in _G3_STEPS:
        return {
            "status": "error",
            "error": True,
            "message": (f"Gate G3: {name} only after the application is "
                        f"APPROVED (current step: "
                        f"{tool_context.state.get('current_step', 'unset')})."),
        }
    if name not in ("fill_fields", "submit_form"):
        return None
    from services import browser_gateway as browser_service

    app_id = tool_context.state.get("active_application_id", "")
    session = browser_service.fill_session_for_application(app_id)
    if name == "submit_form" and not session:
        # After an instance restart / scale-to-zero the in-memory portal page is
        # gone. Don't dead-end the founder's already-granted submission at the
        # fence — let submit_form run: it self-recovers by reopening the portal
        # and re-filling the APPROVED mapping, then re-inspecting the live page.
        return None
    # The expected signature lives on a persisted key (a temp: key is dropped
    # cross-turn, which used to trip this fence on every fill→approve→submit).
    # Fall back to the in-process copy the browser tools keep in lock-step.
    expected = tool_context.state.get("portal_signature", "") or (
        session.get("signature", "") if session else "")
    if not session or not expected:
        return {
            "status": "error",
            "error": True,
            "stale": True,
            "message": "Staleness fence: call inspect_form to read the live form "
                       "before filling or submitting.",
        }
    try:
        current = await browser_service.inspect(session["page"])
    except Exception as exc:  # the callback is itself a model-facing guard
        return {"status": "error", "error": True,
                "message": f"Staleness fence could not inspect the live page: {exc}"[:300]}
    if current.get("status") != "success":
        return current
    if current.get("signature") != expected:
        return {
            "status": "error",
            "error": True,
            "stale": True,
            "message": "Staleness fence: the portal form changed; call inspect_form "
                       "to re-read it (that resets the signature) before continuing.",
            "expected_signature": expected,
            "current_signature": current.get("signature", ""),
        }
    return None


def build_agent(model=None) -> Agent:
    """Fresh instance per parent (voice + text surfaces each need their own tree)."""
    return Agent(
        name="form_filler_agent",
        model=model or MODEL,
        instruction=FORM_FILLER_INSTRUCTION,
        tools=[
            browser.register_account,
            browser.sign_in,
            browser.open_portal,
            browser.inspect_form,
            browser.verify_page_state,
            browser.map_form_requirements,
            browser.vision_step,
            browser.get_approved_sections,
            browser.fill_fields,
            browser.capture_screenshot,
            browser.submit_form,
            pipeline.request_approval,
        ],
        # Refresh {today}/{browser_status} on turns ADK routes straight here.
        before_agent_callback=[initialize_session_state, guard_specialist_entry],
        before_tool_callback=[enforce_workflow_tool_contract, verify_before_action],
        after_tool_callback=track_tool_outcome,
        after_model_callback=enforce_effect_claims,
    )

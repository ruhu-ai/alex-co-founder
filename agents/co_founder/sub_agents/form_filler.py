"""Form-Filler agent — portal navigation + pre-fill (docs/04, 09).

The staleness fence is a before_tool_callback (guards in code, not prompts):
before fill_fields/submit_form it re-verifies the live page signature and
short-circuits the tool by returning a dict when the page changed.
"""

from google.adk.agents import Agent

from ..config import MODEL
from ..instructions import FORM_FILLER_INSTRUCTION
from ..tools import browser, pipeline


async def verify_before_action(tool, args, tool_context):
    """Staleness fence (docs/09 §safety). Runs before EVERY tool call on this
    agent; only gates fill_fields and submit_form.

    Returning a dict short-circuits the tool — the model receives the dict as
    the tool result and the real fill/submit never executes. Returning None
    lets the tool run.
    """
    if getattr(tool, "name", "") not in ("fill_fields", "submit_form"):
        return None
    # Day 7: hash the live form's field signature and compare against the
    # signature captured by inspect_form (stored in state as
    # temp:portal_signature). Until the browser session exists, allow.
    if "temp:portal_signature" not in tool_context.state:
        return None
    return None  # Day 7: compare and short-circuit on mismatch


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
            browser.fill_fields,
            browser.capture_screenshot,
            browser.submit_form,
            pipeline.request_approval,
        ],
        before_tool_callback=verify_before_action,
    )


agent = build_agent()

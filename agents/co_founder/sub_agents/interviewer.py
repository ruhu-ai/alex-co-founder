"""Interviewer/Guide agent — clarifying questions + step-by-step guidance (docs/04)."""

from google.adk.agents import Agent

from ..callbacks import (
    enforce_effect_claims,
    enforce_workflow_tool_contract,
    guard_specialist_entry,
    initialize_session_state,
    track_tool_outcome,
)
from ..config import MODEL
from ..instructions import INTERVIEWER_INSTRUCTION
from ..tools import attachments, pipeline, profile


def build_agent(model=None) -> Agent:
    """Fresh instance per parent (voice + text surfaces each need their own tree)."""
    return Agent(
        name="interviewer_agent",
        model=model or MODEL,
        instruction=INTERVIEWER_INSTRUCTION,
        tools=[
            profile.get_profile,
            attachments.search_attachment,
            profile.record_answer,
            pipeline.complete_interview,
            pipeline.get_checklist,
            profile.ingest_document,
            profile.auto_apply_profile_updates,
            profile.propose_profile_updates,
            profile.confirm_profile_updates,
        ],
        # Refresh {today}/{browser_status} on turns ADK routes straight here.
        before_agent_callback=[initialize_session_state, guard_specialist_entry],
        before_tool_callback=enforce_workflow_tool_contract,
        after_tool_callback=track_tool_outcome,
        after_model_callback=enforce_effect_claims,
    )

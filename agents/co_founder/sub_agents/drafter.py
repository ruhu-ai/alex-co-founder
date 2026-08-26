"""Drafter agent — section drafting in the founder's voice (docs/04)."""

from google.adk.agents import Agent

from ..callbacks import (
    enforce_document_grounding,
    enforce_effect_claims,
    enforce_workflow_tool_contract,
    guard_specialist_entry,
    initialize_session_state,
    track_tool_outcome,
)
from ..config import MODEL
from ..instructions import DRAFTER_INSTRUCTION
from ..tools import attachments, documents, drafting, pipeline, profile


def build_agent(model=None) -> Agent:
    """Fresh instance per parent (voice + text surfaces each need their own tree)."""
    return Agent(
        name="drafter_agent",
        model=model or MODEL,
        instruction=DRAFTER_INSTRUCTION,
        tools=[
            drafting.save_draft_section,
            drafting.complete_drafting,
            drafting.get_section_feedback,
            drafting.get_form_questions,
            documents.produce_document,
            pipeline.get_opportunity,
            profile.get_relevant_answers,
            profile.get_voice_rules,
            attachments.search_attachment,
        ],
        # Refresh {today}/{browser_status} on turns ADK routes straight here.
        before_agent_callback=[initialize_session_state, guard_specialist_entry],
        # Grounding is enforced here, not asked for in a docstring (principle 7).
        before_tool_callback=[enforce_workflow_tool_contract, enforce_document_grounding],
        after_tool_callback=track_tool_outcome,
        after_model_callback=enforce_effect_claims,
    )

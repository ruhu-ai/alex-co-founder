"""Interviewer/Guide agent — clarifying questions + step-by-step guidance (docs/04)."""

from google.adk.agents import Agent

from ..callbacks import initialize_session_state
from ..config import MODEL
from ..instructions import INTERVIEWER_INSTRUCTION
from ..tools import pipeline, profile

def build_agent(model=None) -> Agent:
    """Fresh instance per parent (voice + text surfaces each need their own tree)."""
    return Agent(
        name="interviewer_agent",
        model=model or MODEL,
        instruction=INTERVIEWER_INSTRUCTION,
        tools=[
            profile.get_profile,
            profile.record_answer,
            pipeline.complete_interview,
            pipeline.get_checklist,
            profile.ingest_document,
            profile.auto_apply_profile_updates,
            profile.propose_profile_updates,
            profile.confirm_profile_updates,
        ],
        # Refresh {today}/{browser_status} on turns ADK routes straight here.
        before_agent_callback=initialize_session_state,
    )

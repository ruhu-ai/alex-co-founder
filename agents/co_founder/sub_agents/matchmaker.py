"""Matchmaker agent — fit scoring + deadline urgency (docs/04)."""

from google.adk.agents import Agent

from ..callbacks import initialize_session_state
from ..config import MODEL
from ..instructions import MATCHMAKER_INSTRUCTION
from ..tools import pipeline, profile


def build_agent(model=None) -> Agent:
    """Fresh instance per parent (voice + text surfaces each need their own tree)."""
    return Agent(
        name="matchmaker_agent",
        model=model or MODEL,
        instruction=MATCHMAKER_INSTRUCTION,
        tools=[
            pipeline.get_unscored_opportunities,
            pipeline.shortlist,
            pipeline.archive_with_reason,
            profile.get_profile,
        ],
        # Refresh {today}/{browser_status} on turns ADK routes straight here.
        before_agent_callback=initialize_session_state,
    )

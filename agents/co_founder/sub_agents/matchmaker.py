"""Matchmaker agent — fit scoring + deadline urgency (docs/04)."""

from google.adk.agents import Agent

from ..config import MODEL
from ..instructions import MATCHMAKER_INSTRUCTION
from ..tools import pipeline, profile

def build_agent() -> Agent:
    """Fresh instance per parent (voice + text surfaces each need their own tree)."""
    return Agent(
        name="matchmaker_agent",
        model=MODEL,
        instruction=MATCHMAKER_INSTRUCTION,
        tools=[
            pipeline.get_unscored_opportunities,
            pipeline.shortlist,
            pipeline.archive_with_reason,
            profile.get_profile,
        ],
    )


agent = build_agent()

"""Interviewer/Guide agent — clarifying questions + step-by-step guidance (docs/04)."""

from google.adk.agents import Agent

from ..config import MODEL
from ..instructions import INTERVIEWER_INSTRUCTION
from ..tools import pipeline, profile

def build_agent() -> Agent:
    """Fresh instance per parent (voice + text surfaces each need their own tree)."""
    return Agent(
        name="interviewer_agent",
        model=MODEL,
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
    )


agent = build_agent()

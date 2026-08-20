"""Drafter agent — section drafting in the founder's voice (docs/04)."""

from google.adk.agents import Agent
from ..config import MODEL
from ..instructions import DRAFTER_INSTRUCTION
from ..tools import documents, drafting, pipeline, profile


def build_agent(model=None) -> Agent:
    """Fresh instance per parent (voice + text surfaces each need their own tree)."""
    return Agent(
        name="drafter_agent",
        model=model or MODEL,
        instruction=DRAFTER_INSTRUCTION,
        tools=[
            drafting.save_draft_section,
            drafting.get_section_feedback,
            documents.produce_document,
            pipeline.get_opportunity,
            profile.get_relevant_answers,
            profile.get_voice_rules,
        ],
    )


agent = build_agent()

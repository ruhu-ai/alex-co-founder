"""Matchmaker agent — fit scoring + deadline urgency (docs/04)."""

from google.adk.agents import Agent

from ..callbacks import (
    enforce_effect_claims,
    enforce_workflow_tool_contract,
    guard_specialist_entry,
    initialize_session_state,
    track_tool_outcome,
)
from ..config import MODEL
from ..instructions import MATCHMAKER_INSTRUCTION
from ..tools import pipeline, profile


def build_agent(model=None, *, task_mode: bool = False) -> Agent:
    """Fresh instance per parent (voice + text surfaces each need their own tree)."""
    return Agent(
        name="matchmaker_agent",
        description=(
            "Score pending opportunities against durable Founder Profile facts "
            "and return grounded fit results to Alex."
        ),
        model=model or MODEL,
        mode="task" if task_mode else None,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        instruction=MATCHMAKER_INSTRUCTION,
        tools=[
            pipeline.get_unscored_opportunities,
            pipeline.shortlist,
            pipeline.archive_with_reason,
            profile.get_profile,
        ],
        # Refresh {today}/{browser_status} on turns ADK routes straight here.
        before_agent_callback=[initialize_session_state, guard_specialist_entry],
        before_tool_callback=enforce_workflow_tool_contract,
        after_tool_callback=track_tool_outcome,
        after_model_callback=enforce_effect_claims,
    )

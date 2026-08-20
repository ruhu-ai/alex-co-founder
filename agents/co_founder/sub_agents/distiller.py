"""Distiller agent — feedback -> structured profile mutations (docs/04, 06).

Standalone, task-run worker: NOT in the transfer graph. include_contents="none"
means it sees only the feedback JSON in its run message — a short prompt is
cheaper, faster, and cannot be derailed by the founder conversation.
"""

from google.adk.agents import Agent

from ..config import MODEL
from ..instructions import DISTILLER_INSTRUCTION
from ..tools import feedback, profile

agent = Agent(
    name="distiller_agent",
    model=MODEL,
    include_contents="none",
    output_key="distillation",
    instruction=DISTILLER_INSTRUCTION,
    tools=[
        feedback.get_feedback,
        feedback.mark_distilled,
        profile.apply_profile_update,
    ],
)

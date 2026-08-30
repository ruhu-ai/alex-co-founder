"""Scout agent — discovery + structured extraction (docs/04, 08).

Knows nothing about "grants": it extracts whatever entity schema the active
workflow defines, baked into its instruction at build time.
"""

import json

from google.adk.agents import Agent

from ..callbacks import (
    enforce_effect_claims,
    enforce_workflow_tool_contract,
    guard_specialist_entry,
    initialize_session_state,
    track_tool_outcome,
)
from ..config import MODEL
from ..instructions import SCOUT_INSTRUCTION
from ..tools import discovery
from ..workflow import get_workflow

_schema = get_workflow().entity_schema

def build_agent(model=None, *, task_mode: bool = False) -> Agent:
    """Fresh instance per parent (voice + text surfaces each need their own tree)."""
    return Agent(
        name="scout_agent",
        description=(
            "Research and extract bounded, source-grounded funding and program "
            "opportunities, then return the result to Alex."
        ),
        model=model or MODEL,
        mode="task" if task_mode else None,
        # A specialist is a task boundary, never the durable conversational
        # owner. Text re-enters Alex on the next turn; Live uses task mode and
        # returns the specialist result to Alex within the same call.
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        instruction=SCOUT_INSTRUCTION.replace(
            "__ENTITY_SCHEMA__", json.dumps(_schema, indent=2)
        ),
        tools=[
            discovery.search_programs,
            discovery.fetch_source,
            discovery.extract_records,
            discovery.dedupe_check,
            discovery.save_opportunity,
        ],
        # Refresh {today}/{browser_status} on turns ADK routes straight here.
        before_agent_callback=[initialize_session_state, guard_specialist_entry],
        before_tool_callback=enforce_workflow_tool_contract,
        after_tool_callback=track_tool_outcome,
        after_model_callback=enforce_effect_claims,
    )

"""Scout agent — discovery + structured extraction (docs/04, 08).

Knows nothing about "grants": it extracts whatever entity schema the active
workflow defines, baked into its instruction at build time.
"""

import json

from google.adk.agents import Agent

from ..callbacks import initialize_session_state
from ..config import MODEL
from ..instructions import SCOUT_INSTRUCTION
from ..tools import discovery
from ..workflow import get_workflow

_schema = get_workflow().entity_schema

def build_agent(model=None) -> Agent:
    """Fresh instance per parent (voice + text surfaces each need their own tree)."""
    return Agent(
        name="scout_agent",
        model=model or MODEL,
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
        before_agent_callback=initialize_session_state,
    )

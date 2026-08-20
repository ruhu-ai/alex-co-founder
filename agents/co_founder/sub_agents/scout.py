"""Scout agent — discovery + structured extraction (docs/04, 08).

Knows nothing about "grants": it extracts whatever entity schema the active
workflow defines, baked into its instruction at build time.
"""

import json

from google.adk.agents import Agent

from ..config import MODEL
from ..instructions import SCOUT_INSTRUCTION
from ..tools import discovery
from ..workflow import get_workflow

_schema = get_workflow().entity_schema

def build_agent() -> Agent:
    """Fresh instance per parent (voice + text surfaces each need their own tree)."""
    return Agent(
        name="scout_agent",
        model=MODEL,
        instruction=SCOUT_INSTRUCTION.replace(
            "__ENTITY_SCHEMA__", json.dumps(_schema, indent=2)
        ),
        tools=[
            discovery.search_programs,
            discovery.fetch_source,
            discovery.dedupe_check,
            discovery.save_opportunity,
        ],
    )


agent = build_agent()

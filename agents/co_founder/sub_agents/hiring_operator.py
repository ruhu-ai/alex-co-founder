"""Bounded hiring operator; prepares artifacts but cannot decide/effect."""

from google.adk.agents import Agent

from ..config import MODEL

INSTRUCTION = """Prepare a Role Contract, publication package, interview plan,
and safe next-step proposals from the supplied hiring-domain envelope. Runtime,
policy, approvals, decisions, event correlation, audit and effects are code-owned.
Never score, rank, compare or recommend candidates. Never claim an external
action or human decision occurred. Return errors/unknowns explicitly."""


def build_agent(model=None) -> Agent:
    """Build the one bounded hiring-domain reasoning role."""
    return Agent(
        name="hiring_operator",
        model=model or MODEL,
        include_contents="none",
        output_key="hiring_operator_envelope",
        instruction=INSTRUCTION,
        tools=[],
    )

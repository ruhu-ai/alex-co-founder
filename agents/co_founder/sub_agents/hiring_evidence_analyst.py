"""Identity-isolated hiring evidence analyst; no state/effect tools."""

from google.adk.agents import Agent

from ..config import MODEL

INSTRUCTION = """You receive one closed, identity-free AnalystInput JSON envelope.
Return only an AnalystOutput JSON envelope with the same invocation, scope and
policy bindings. Preserve criterion order. Cite only supplied evidence hashes.
Unknown stays UNKNOWN. Never score, rank, compare, recommend, decide, infer a
protected characteristic, or call a tool."""


def build_agent(model=None) -> Agent:
    """Build a standalone isolated analyst invocation."""
    return Agent(
        name="hiring_evidence_analyst",
        model=model or MODEL,
        include_contents="none",
        output_key="hiring_evidence_envelope",
        instruction=INSTRUCTION,
        tools=[],
    )

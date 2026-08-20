"""A2A negotiation tool (docs/19 §P1.5). Errors as data."""

from google.adk.tools import ToolContext

from ._common import run


def ask_portal_agent(portal_url: str, question: str, tool_context: ToolContext) -> dict:
    """Ask the program portal's own A2A agent a question — agent-to-agent
    negotiation over the A2A protocol.

    Args:
        portal_url: The portal's base URL (its A2A agent card is discovered
            at /.well-known/agent.json).
        question: What to ask — requirements, deadlines and extensions, or a
            submission status (confirmation id MP-XXXX).

    Returns:
        dict with status, agent (the portal agent's name), and answer. Every
        exchange is audited on both sides.
    """
    from services import a2a_talk, firestore

    result = run(a2a_talk.ask_agent(portal_url, question))
    run(firestore.audit(
        actor="agent:orchestrator", action="a2a_negotiate", target=portal_url,
        result="success" if result.get("status") == "success" else "error",
        detail=f"Q: {question[:120]} -> {result.get('answer', result.get('message', ''))[:200]}"))
    return result

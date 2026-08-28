"""Scoped attachment retrieval tools (docs/05, 06, 21)."""

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import run, workspace_id


def search_attachment(query: str, tool_context: ToolContext) -> dict:
    """Search the attachments explicitly registered in this session.

    Args:
        query: The fact, phrase, requirement, or topic to find in the attached documents.

    Returns:
        Bounded evidence excerpts with artifact and page/slide/paragraph citations.
        The tool never searches unrelated artifacts and never changes the Founder Profile.
    """
    from services import document_ingestion

    session = getattr(tool_context, "session", None)
    session_id = getattr(session, "id", "") or getattr(tool_context, "session_id", "")
    attachments = list(tool_context.state.get(ss.K_ACTIVE_ATTACHMENTS) or [])
    refs = [str(item.get("attachment_ref")) for item in attachments
            if isinstance(item, dict) and item.get("attachment_ref")]
    if not session_id:
        return {"status": "error", "error": True,
                "message": "attachment search requires an active session"}
    return run(document_ingestion.search_attachments(
        founder_id=workspace_id(tool_context),
        session_id=session_id,
        attachment_refs=refs,
        query=query,
    ))

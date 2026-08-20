"""Document production tool (docs/15). Errors as data.

Provenance is automatic: session_id and application_id come from the
invocation context and session state, never from the model.
"""

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import run


def produce_document(kind: str, title: str, spec: dict, tool_context: ToolContext) -> dict:
    """Produce a polished document (.docx / .xlsx / .pptx) from a JSON spec.

    Args:
        kind: "docx" (application packs, narratives), "xlsx" (budgets,
            projections), "pptx" (decks), or "pdf" (same spec as docx —
            converted headlessly when the program requires PDF).
        title: Document title, e.g. "Meridian Pre-Seed Grant — Application Pack".
        spec: JSON spec. docx/pdf: {"sections": [{"heading"?, "paragraphs"?,
            "bullets"?}]}; xlsx: {"sheets": [{"name", "columns", "rows",
            "formulas"?}]}; pptx: {"slides": [{"title", "bullets", "notes"?}]}.
            Ground every value in approved sections and profile facts — never
            invent numbers.

    Returns:
        dict with status, artifact_name, download_url, version. The file is
        validated before delivery; on failure an error dict and no artifact.
    """
    from services import document_service, firestore

    founder = tool_context.state.get(ss.K_USER_PROFILE_ID, "founder")
    session_id = getattr(getattr(tool_context, "session", None), "id", "") or ""
    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")

    async def _go():
        tslug = document_service.slug(title)
        doc_key = f"{app_id or 'general'}:{tslug}"
        version = await firestore.next_document_version(founder, doc_key)
        artifact_name = f"{kind}_{app_id or 'general'}_{tslug}_v{version}.{kind}"
        produced = document_service.produce(kind, title, spec, artifact_name)
        if produced["status"] != "success":
            return produced
        opp_name = ""
        if app_id:
            app = await firestore.get_application(app_id)
            if app:
                opp = await firestore.get_opportunity(app.get("opportunity_id", ""))
                opp_name = opp.get("name", "") if opp else ""
        await firestore.create_document_record(
            founder, artifact_name, kind, title, session_id, app_id, opp_name,
            document_service.spec_hash(spec), version, doc_key)
        await firestore.audit(
            actor="agent:drafter", action="produce_document",
            target=f"artifacts/{artifact_name}", result="success",
            detail=f"{kind} '{title}' v{version} session={session_id}")
        return {"status": "success", "artifact_name": artifact_name,
                "download_url": f"/api/artifacts/{artifact_name}/download",
                "version": version, "bytes": produced["bytes"]}

    return run(_go())

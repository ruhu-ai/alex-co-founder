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
            if app and app.get("opportunity_id"):
                opp = await firestore.get_opportunity(app["opportunity_id"])
                opp_name = opp.get("name", "") if opp else ""
        document_id = await firestore.create_document_record(
            founder, artifact_name, kind, title, session_id, app_id, opp_name,
            document_service.spec_hash(spec), version, doc_key)
        # Searchable provenance (docs/23 §6.1). The logical resource is the
        # document LINEAGE (founder, doc_key) — every version and its file is
        # a representation, so regenerating updates one resource instead of
        # minting a new search hit per version. Session comes from
        # ToolContext, never from model args.
        if session_id:
            from services import session_resources

            await session_resources.register_session_resource(
                founder_id=founder, session_id=session_id,
                resource_type=session_resources.ResourceType.DOCUMENT,
                canonical_id=doc_key,
                relationship=session_resources.Relationship.PRODUCED,
                occurrence_key=getattr(tool_context, "invocation_id", "")
                or f"document:{document_id}",
                producer_kind="tool", producer_id="produce_document",
                producer_output_key="document",
                title=title,
                summary=(f"{kind.upper()} · v{version}"
                         + (f" · {opp_name}" if opp_name else "")),
                status=f"v{version}",
                parent_resource_id=(
                    session_resources.resource_id_for(
                        founder, session_resources.ResourceType.APPLICATION,
                        "applications", app_id) if app_id else None),
                representation_refs=(
                    session_resources.RepresentationRef(
                        kind="document_record", ref=document_id),),
                session_verified=True)
        await firestore.audit(
            actor="agent:drafter", action="produce_document",
            target=f"artifacts/{artifact_name}", result="success",
            detail=f"{kind} '{title}' v{version} document={document_id}")
        return {"status": "success", "artifact_name": artifact_name,
                "download_url": f"/api/artifacts/{artifact_name}/download",
                "version": version, "bytes": produced["bytes"],
                "note": "Reply to the founder with ONLY: the title, version, "
                        "and download_url. Never paste the document's content "
                        "into chat — the file is the deliverable."}

    return run(_go())

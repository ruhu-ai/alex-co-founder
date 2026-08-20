"""Discovery tools (docs/05). Errors as data: tools never raise to the model.
Thin wrappers over services.discovery_service."""

import hashlib

from google.adk.tools import ToolContext

from ._common import run


def search_programs(query: str, tool_context: ToolContext) -> dict:
    """Search the web for funding/program opportunities matching a query.

    Args:
        query: Search query composed from the Founder Profile (sector, stage,
            geography, decision_patterns) plus freshness terms, e.g.
            "pre-seed fintech grant Africa 2026 application deadline".

    Returns:
        dict with status and results: [{title, url, snippet}] (max 10).
    """
    from services import discovery_service

    return run(discovery_service.search_programs(query))


def fetch_source(source_url: str, source_type: str, tool_context: ToolContext) -> dict:
    """Fetch a web page or PDF and return a summary plus crawlable links.

    Args:
        source_url: Absolute URL of the source to fetch.
        source_type: One of "web_page" or "pdf".

    Returns:
        dict with status, artifact name, <=300-char summary, char count,
        rendered flag (True if the Playwright JS-shell fallback fired), and
        links: [{url, anchor_text}] (top 15, for the crawl lane).
    """
    from services import discovery_service, pipeline_service

    artifact = f"source_{pipeline_service.dedup_hash(source_url, source_type)[:12]}.txt"
    return run(discovery_service.fetch_source(source_url, source_type, artifact))


def dedupe_check(name: str, application_url: str, tool_context: ToolContext) -> dict:
    """Check whether an opportunity already exists before saving it.

    Args:
        name: Program name as extracted from the source.
        application_url: Canonical application URL of the program.

    Returns:
        dict with status, dedup_hash, is_duplicate, and existing_id when a
        duplicate is found.
    """
    from services import firestore

    digest = hashlib.sha256(f"{name.strip().lower()}|{application_url.strip().lower()}".encode()).hexdigest()
    try:
        existing_id = run(firestore.find_opportunity_by_hash(digest))
        return {"status": "success", "dedup_hash": digest,
                "is_duplicate": existing_id is not None, "existing_id": existing_id}
    except Exception:
        return {"status": "success", "dedup_hash": digest, "is_duplicate": False,
                "note": "pipeline store unreachable; returned hash only"}


def save_opportunity(record: dict, tool_context: ToolContext) -> dict:
    """Validate and store one extracted opportunity record.

    Args:
        record: The extracted opportunity. Must match the active workflow's
            entity_schema exactly — missing fields become null, extra fields
            are rejected.

    Returns:
        dict with status and opportunity_id of the stored record.
    """
    from agents.co_founder.workflow import get_workflow
    from services import discovery_service

    return run(discovery_service.save_opportunity_record(record, get_workflow().entity_schema))

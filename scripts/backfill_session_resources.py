#!/usr/bin/env python3
"""Backfill the session-resource projection from exact legacy evidence.

Dry-run is the default. Pass ``--apply`` to write. The scan is paged and every
write uses deterministic live service seams, so reruns are idempotent and
tombstoned links are never resurrected. Ambiguous records are indexed without
a session link; timestamp proximity is never treated as provenance.

Examples:
    python scripts/backfill_session_resources.py
    python scripts/backfill_session_resources.py --apply --summary-file /tmp/backfill.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, AsyncIterator

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services import firestore  # noqa: E402
from services import session_resources as sr  # noqa: E402


async def _paged_rows(collection: str, owner_field: str, owner: str,
                      page_size: int) -> AsyncIterator[dict[str, Any]]:
    """Yield owner-scoped rows in bounded document-id pages."""
    query = (firestore.get_client().collection(collection)
             .where(owner_field, "==", owner)
             .order_by("__name__"))
    cursor = None
    while True:
        page = query.limit(page_size)
        if cursor is not None:
            page = page.start_after(cursor)
        snapshots = [snapshot async for snapshot in page.stream()]
        if not snapshots:
            return
        for snapshot in snapshots:
            yield (snapshot.to_dict() or {}) | {"id": snapshot.id}
        if len(snapshots) < page_size:
            return
        cursor = snapshots[-1]


def _linked(*, resource_type: str, canonical_id: str, session_id: str,
            relationship: str, occurrence_key: str, title: str,
            summary: str = "", status: str = "", visibility: str | None = None,
            producer_id: str = "legacy_backfill",
            representation_refs: tuple[sr.RepresentationRef, ...] = (),
            parent_resource_id: str | None = None) -> dict[str, Any]:
    return {
        "kind": "linked", "resource_type": resource_type,
        "canonical_id": canonical_id, "session_id": session_id,
        "relationship": relationship, "occurrence_key": occurrence_key,
        "title": title, "summary": summary, "status": status,
        "visibility": visibility, "producer_id": producer_id,
        "representation_refs": representation_refs,
        "parent_resource_id": parent_resource_id,
    }


def _unlinked(*, resource_type: str, canonical_id: str, title: str,
              summary: str = "", status: str = "",
              visibility: str | None = None,
              representation_refs: tuple[sr.RepresentationRef, ...] = (),
              reason: str = "missing_exact_session") -> dict[str, Any]:
    return {
        "kind": "unlinked", "resource_type": resource_type,
        "canonical_id": canonical_id, "title": title, "summary": summary,
        "status": status, "visibility": visibility,
        "representation_refs": representation_refs, "reason": reason,
    }


async def build_plan(founder_id: str, page_size: int) -> list[dict[str, Any]]:
    """Build an evidence-only plan without writing any projection."""
    plan: list[dict[str, Any]] = []
    application_sessions: dict[str, set[str]] = defaultdict(set)
    opportunity_occurrences: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

    async for row in _paged_rows("discovery_requests", "founder_id", founder_id,
                                 page_size):
        request_id = str(row.get("request_id") or row["id"])
        session_id = str(row.get("origin_session_id") or "")
        item = _linked(
            resource_type=sr.ResourceType.DISCOVERY_REQUEST,
            canonical_id=row["id"], session_id=session_id,
            relationship=sr.Relationship.CREATED,
            occurrence_key=request_id,
            title=str(row.get("display_query") or "Funding discovery"),
            summary="Legacy discovery request",
            status=str(row.get("status") or "UNKNOWN"),
            producer_id="discovery_request") if session_id else _unlinked(
                resource_type=sr.ResourceType.DISCOVERY_REQUEST,
                canonical_id=row["id"], title=str(
                    row.get("display_query") or "Funding discovery"),
                summary="Legacy discovery request",
                status=str(row.get("status") or "UNKNOWN"))
        plan.append(item)
        if session_id:
            for opportunity_id in (row.get("result_opportunity_ids") or [])[:100]:
                opportunity_occurrences[str(opportunity_id)].append(
                    (session_id, row["id"], sr.Relationship.DISCOVERED))

    async for row in _paged_rows("documents", "founder_id", founder_id, page_size):
        document_id = row["id"]
        doc_key = str(row.get("doc_key") or document_id)
        session_id = str(row.get("session_id") or "")
        application_id = str(row.get("application_id") or "")
        if session_id and application_id:
            application_sessions[application_id].add(session_id)
        common = {
            "resource_type": sr.ResourceType.DOCUMENT,
            "canonical_id": doc_key,
            "title": str(row.get("title") or "Produced document"),
            "summary": f"{str(row.get('kind') or 'document').upper()} · legacy version",
            "status": f"v{int(row.get('version') or 1)}",
            "representation_refs": (
                sr.RepresentationRef(kind="document_record", ref=document_id),),
        }
        plan.append(_linked(
            **common, session_id=session_id,
            relationship=sr.Relationship.PRODUCED,
            occurrence_key=f"legacy_document:{document_id}",
            producer_id="produce_document",
            parent_resource_id=(sr.resource_id_for(
                founder_id, sr.ResourceType.APPLICATION, "applications",
                application_id) if application_id else None)) if session_id
            else _unlinked(**common))

    async for row in _paged_rows("artifacts", "founder_id", founder_id, page_size):
        artifact_id = row["id"]
        session_id = str(row.get("session_id") or "")
        title = str(row.get("source_ref") or row.get("artifact") or "Attachment")
        common = {
            "resource_type": sr.ResourceType.ARTIFACT,
            "canonical_id": artifact_id, "title": title,
            "summary": str(row.get("scope") or row.get("source_type") or "Legacy attachment"),
            "status": str(row.get("status") or row.get("provenance_status") or "stored"),
        }
        plan.append(_linked(
            **common, session_id=session_id,
            relationship=sr.Relationship.CREATED,
            occurrence_key=f"legacy_artifact:{artifact_id}",
            producer_id="source_ingestion") if session_id
            else _unlinked(**common))

    async for row in _paged_rows("browser_runs", "user_id", founder_id, page_size):
        run_id = str(row.get("run_id") or row["id"])
        session_id = str(row.get("session_id") or "")
        common = {
            "resource_type": sr.ResourceType.BROWSER_REPORT,
            "canonical_id": run_id,
            "title": str(row.get("goal") or f"Browser {row.get('kind') or 'run'}"),
            "summary": f"Browser {row.get('kind') or 'run'}",
            "status": str(row.get("status") or "closed"),
            "visibility": sr.Visibility.SUPPORTING,
        }
        plan.append(_linked(
            **common, session_id=session_id,
            relationship=sr.Relationship.PRODUCED,
            occurrence_key=f"browser_run:{run_id}",
            producer_id=f"browser:{row.get('kind') or 'run'}") if session_id
            else _unlinked(**common))

    applications: dict[str, dict[str, Any]] = {}
    async for row in _paged_rows("applications", "founder_id", founder_id, page_size):
        applications[row["id"]] = row
        application_id = row["id"]
        sessions = sorted(application_sessions.get(application_id, set()))
        common = {
            "resource_type": sr.ResourceType.APPLICATION,
            "canonical_id": application_id,
            "title": str(row.get("opportunity_name") or f"Application {application_id[:8]}"),
            "summary": "Legacy application",
            "status": str(row.get("state") or "UNKNOWN"),
        }
        if sessions:
            for session_id in sessions:
                plan.append(_linked(
                    **common, session_id=session_id,
                    relationship=sr.Relationship.CONTINUED,
                    occurrence_key=f"legacy_application:{application_id}:{session_id}",
                    producer_id="choose_opportunity"))
                opportunity_id = str(row.get("opportunity_id") or "")
                if opportunity_id:
                    opportunity_occurrences[opportunity_id].append(
                        (session_id, application_id, sr.Relationship.SELECTED))
        else:
            plan.append(_unlinked(**common, reason="no_exact_application_session"))

    # Shared opportunity rows have no founder field. Read only the exact ids
    # proven by a founder-owned receipt/application relationship above.
    for opportunity_id, occurrences in sorted(opportunity_occurrences.items()):
        snapshot = await firestore.get_client().collection(
            "opportunities").document(opportunity_id).get()
        if not snapshot.exists:
            continue
        row = snapshot.to_dict() or {}
        for session_id, source_id, relationship in sorted(set(occurrences)):
            plan.append(_linked(
                resource_type=sr.ResourceType.OPPORTUNITY,
                canonical_id=opportunity_id, session_id=session_id,
                relationship=relationship,
                occurrence_key=f"legacy_opportunity:{source_id}:{opportunity_id}",
                title=str(row.get("name") or "Opportunity"),
                summary=str(row.get("fit_rationale") or row.get("description") or "")[:500],
                status=str(row.get("state") or "DISCOVERED"),
                producer_id="legacy_provenance"))

    async for row in _paged_rows("evidence_checks", "founder_id", founder_id,
                                 page_size):
        report_id = row["id"]
        application_id = str(row.get("application_id") or "")
        sessions = sorted(application_sessions.get(application_id, set()))
        common = {
            "resource_type": sr.ResourceType.EVIDENCE_REPORT,
            "canonical_id": report_id, "title": "Evidence check",
            "summary": f"{len(row.get('findings') or [])} findings",
            "status": str(row.get("status") or row.get("execution_status") or "UNKNOWN"),
            "visibility": sr.Visibility.SUPPORTING,
        }
        if len(sessions) == 1:
            plan.append(_linked(
                **common, session_id=sessions[0],
                relationship=sr.Relationship.PRODUCED,
                occurrence_key=f"evidence_check:{report_id}",
                producer_id="gemma_evidence",
                parent_resource_id=sr.resource_id_for(
                    founder_id, sr.ResourceType.APPLICATION, "applications",
                    application_id)))
        else:
            plan.append(_unlinked(
                **common, reason="ambiguous_evidence_session" if sessions
                else "no_exact_evidence_session"))

    return plan


async def apply_plan(founder_id: str, plan: list[dict[str, Any]],
                     apply: bool) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "mode": "apply" if apply else "dry_run", "planned": len(plan),
        "linked": 0, "legacy_unlinked": 0, "replayed": 0, "errors": 0,
        "by_type": {},
    }
    for item in plan:
        kind = item["kind"]
        summary[kind if kind == "linked" else "legacy_unlinked"] += 1
        resource_type = item["resource_type"]
        summary["by_type"].setdefault(resource_type, 0)
        summary["by_type"][resource_type] += 1
        if not apply:
            continue
        fields = {key: value for key, value in item.items()
                  if key not in {"kind", "reason"} and value is not None}
        if kind == "linked":
            result = await sr.register_session_resource(
                founder_id=founder_id, producer_kind="migration",
                producer_output_key="legacy_resource", session_verified=True,
                **fields)
        else:
            result = await sr.register_legacy_unlinked_resource(
                founder_id=founder_id, **fields)
        if result.get("error"):
            summary["errors"] += 1
        elif result.get("replayed"):
            summary["replayed"] += 1
    return summary


async def _main(args: argparse.Namespace) -> int:
    plan = await build_plan(args.founder_id, args.page_size)
    summary = await apply_plan(args.founder_id, plan, args.apply)
    encoded = json.dumps(summary, sort_keys=True, indent=2)
    if args.summary_file:
        Path(args.summary_file).write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 1 if summary["errors"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--founder-id", default="founder")
    parser.add_argument("--page-size", type=int, default=100, choices=range(10, 251),
                        metavar="10..250")
    parser.add_argument("--apply", action="store_true",
                        help="write deterministic projections (default: dry-run)")
    parser.add_argument("--summary-file",
                        help="optional counts-only JSON output path")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

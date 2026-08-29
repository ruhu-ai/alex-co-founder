"""Pipeline store accessors (docs/02).

The client is lazy: importing this module never requires credentials; the first
call does. Collections: opportunities, applications, profiles, feedback,
approvals, audit, ingestions, browser_runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_client = None

# ---------------------------------------------------------------------------
# Collection registry (docs/23 §9.7, docs/02).
#
# Every Firestore collection this application touches, by literal name. The
# coverage test (tests/unit/test_collection_registry.py) scans services/ and
# app/ for `.collection("…")` literals and fails when one is missing here, so
# a new collection cannot ship without registering — the future export/
# deletion implementation enumerates from this registry, never a hand list.
# ---------------------------------------------------------------------------

TOP_LEVEL_COLLECTIONS: frozenset[str] = frozenset({
    "opportunities",
    "applications",
    "profiles",
    "ingestions",
    "artifacts",
    "feedback",
    "approvals",
    "audit",
    "evidence_checks",
    "browser_runs",
    "discovery_requests",
    "oauth_states",
    "integrations",
    "gmail_state",
    "alex_mail_state",
    "portal_registrations",
    "source_state",
    "documents",
    "document_versions",
    "founder_state",       # proactive-delivery routing table (app/main.py)
    # docs/23 session-resource projections
    "resource_index",
    "session_resource_links",
    "session_catalog",
    # docs/24 data-source reliability safety records
    "data_connections",
    "source_grants",
    "external_events",
    "founder_inbox",
    "external_actions",
    "wake_deliveries",
    "conversation_deliveries",
    "background_pilot_capacity",
    "portal_event_receipts",
    "command_receipts",
    "command_outbox",
    # docs/35 content-free Live consent/share lifecycle records
    "media_consent_grants",
    "live_media_shares",
    "projection_streams",
    "projection_events",
    "tenancy_migration_receipts",
    "connector_credential_migration_receipts",
    "consequence_migration_receipts",
    "workflow_migration_receipts",
    "action_execution_outbox",
    # docs/25 H1-H3 durable hiring foundation
    "workspace_members",
    "workflow_runs",
    "workflow_plans",
    "workflow_steps",
    "step_attempts",
    "waits",
    "run_events",
    "investor_outreach",
    "investor_candidates",
    "outreach_drafts",
    "investor_replies",
    "meeting_briefs",
    "workspace_profiles",
    "actor_preference_profiles",
    "profile_fact_pointers",
    "profile_facts",
    "profile_fact_receipts",
    "memory_items",
    "memory_source_manifests",
    "memory_settings",
    "memory_control_receipts",
    "memory_deletion_tombstones",
    "memory_deletion_jobs",
    # The last two live in a separately configured Firestore database in M2;
    # they are still registered so collection coverage cannot miss them.
    "memory_deletion_ledger",
    "memory_deletion_ledger_heads",
    "memory_write_receipts",
    "memory_search_receipts",
    "deletion_jobs",
    "deletion_work_items",
    "deletion_receipts",
    "capability_states",
    "operational_snapshots",
    "slo_observations",
    "workspace_budgets",
    "budget_consumption_receipts",
    "change_rollouts",
    "chaos_drills",
    "migration_drills",
    "recovery_drills",
    "governance_reports",
    "connector_credential_grants",
    "hiring_roles",
    "hiring_policy_versions",
    "hiring_policy_impacts",
    "candidate_identities",
    "candidate_applications",
    "hiring_candidate_artifacts",
    "candidate_evidence",
    "candidate_assessments",
    "hiring_decisions",
    "hiring_candidate_requests",
    "candidate_data_rights_receipts",
    "hiring_mailbox_state",
    "hiring_mailbox_probe_receipts",
    "hiring_fixture_messages",
    "mailbox_fetch_batches",
    "mailbox_fetch_batch_entries",
    "hiring_cursor_receipts",
    # docs/30 H4S sandbox envelope. Effects remain separately gated.
    "hiring_sandbox_runs",
    "hiring_sandbox_destinations",
    "hiring_sandbox_connector_bindings",
    "hiring_conversation_turns",
    "hiring_process_retrospectives",
    "hiring_reply_correlations",
    # Internal controlled-demo records are durable workspace data too. They
    # must participate in export/deletion coverage even though they use the
    # generic DurableStore adapter rather than accessors in this module.
    "internal_demo_runs",
    "internal_demo_approvals",
    "internal_demo_actions",
    "internal_demo_applications",
})

SUBCOLLECTIONS: frozenset[str] = frozenset({
    "update_receipts",     # profiles/{id}/update_receipts
    "frames",              # browser_runs/{id}/frames
    "actions",             # browser_runs/{id}/actions
    "chunks",              # artifacts/{id}/chunks
    "image_observations",  # artifacts/{id}/image_observations (docs/35)
})

REGISTERED_COLLECTIONS: frozenset[str] = TOP_LEVEL_COLLECTIONS | SUBCOLLECTIONS


def get_client():
    """Lazy Firestore client (raises on first call if credentials are missing)."""
    global _client
    if _client is None:
        from google.cloud import firestore

        # The default database is addressed by passing database=None, NOT the
        # literal "(default)": some transports URL-encode the parens into the
        # request routing and Firestore rejects it ("Invalid database id
        # %28default%29"). Treat unset/empty/"(default)" all as the real default;
        # a real named database is passed through unchanged.
        _db = os.environ.get("FIRESTORE_DATABASE") or None
        if _db == "(default)":
            _db = None
        _client = firestore.AsyncClient(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            database=_db,
        )
    return _client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# User-facing Live vision metadata (docs/35). No media/content fields allowed.
# ---------------------------------------------------------------------------

async def create_media_consent_grant(row: dict[str, Any]) -> None:
    """Create one server-authored, source/session-bound consent receipt."""
    grant_id = str(row.get("grant_id") or "")
    if not re.fullmatch(r"[a-f0-9]{32}", grant_id):
        raise ValueError("invalid media consent grant id")
    await get_client().collection("media_consent_grants").document(grant_id).create(row)


async def get_media_consent_grant(grant_id: str) -> Optional[dict[str, Any]]:
    if not re.fullmatch(r"[a-f0-9]{32}", str(grant_id or "")):
        return None
    doc = await get_client().collection("media_consent_grants").document(grant_id).get()
    return doc.to_dict() | {"id": doc.id} if doc.exists else None


async def find_active_media_consent_grant(
        workspace_id: str, actor_id: str, session_id: str,
        source: str) -> Optional[dict[str, Any]]:
    """Find a current same-actor disclosure receipt without broadening scope."""
    query = (get_client().collection("media_consent_grants")
             .where("workspace_id", "==", workspace_id)
             .where("session_id", "==", session_id)
             .where("source", "==", source)
             .where("status", "==", "ACTIVE").limit(10))
    async for doc in query.stream():
        row = doc.to_dict() or {}
        if row.get("actor_id") == actor_id:
            return row | {"id": doc.id}
    return None


async def create_live_media_share(row: dict[str, Any]) -> None:
    """Create content-free operations metadata for one explicit share generation."""
    share_id = str(row.get("share_id") or "")
    if not share_id or len(share_id) > 128:
        raise ValueError("invalid live media share id")
    ref = get_client().collection("live_media_shares").document(
        hashlib.sha256(
            f"{row.get('workspace_id')}:{row.get('session_id')}:{share_id}".encode()
        ).hexdigest()[:32])
    existing = await ref.get()
    if existing.exists:
        current = existing.to_dict() or {}
        identity = ("workspace_id", "session_id", "share_id", "generation", "source")
        if any(current.get(key) != row.get(key) for key in identity):
            raise ValueError("live media share conflicts")
        return
    await ref.create(row)


def _live_media_share_ref(workspace_id: str, session_id: str, share_id: str):
    """Resolve a share only through its complete server-owned tenant key."""
    if not workspace_id or not session_id or not share_id:
        return None
    doc_id = hashlib.sha256(
        f"{workspace_id}:{session_id}:{share_id}".encode()).hexdigest()[:32]
    return get_client().collection("live_media_shares").document(doc_id)


async def update_live_media_share_counters(
        workspace_id: str, session_id: str, share_id: str,
        **counters: int) -> None:
    ref = _live_media_share_ref(workspace_id, session_id, share_id)
    if ref is None:
        return
    snapshot = await ref.get()
    if not snapshot.exists:
        return
    row = snapshot.to_dict() or {}
    if (row.get("workspace_id") != workspace_id
            or row.get("session_id") != session_id
            or row.get("share_id") != share_id):
        raise ValueError("live media share authority mismatch")
    allowed = {"frames_received", "frames_forwarded", "frames_dropped",
               "bytes_received", "throttle_count"}
    values = {key: max(0, int(value)) for key, value in counters.items()
              if key in allowed}
    if values:
        await ref.update({**values, "updated_at": _now()})


async def stop_live_media_share(
        workspace_id: str, session_id: str, share_id: str, *, status: str,
        end_reason: str, **counters: int) -> None:
    ref = _live_media_share_ref(workspace_id, session_id, share_id)
    if ref is None:
        return
    snapshot = await ref.get()
    if not snapshot.exists:
        return
    row = snapshot.to_dict() or {}
    if (row.get("workspace_id") != workspace_id
            or row.get("session_id") != session_id
            or row.get("share_id") != share_id):
        raise ValueError("live media share authority mismatch")
    allowed = {"frames_received", "frames_forwarded", "frames_dropped",
               "bytes_received", "throttle_count"}
    values = {key: max(0, int(value)) for key, value in counters.items()
              if key in allowed}
    await ref.update({**values, "status": status, "end_reason": end_reason,
                      "ended_at": _now(), "updated_at": _now()})


async def delete_session_live_media_metadata(
        workspace_id: str, session_id: str) -> int:
    """Delete content-free consent/share rows when their session is deleted."""
    if not workspace_id or not session_id:
        return 0
    deleted = 0
    for collection_name in ("media_consent_grants", "live_media_shares"):
        query = (get_client().collection(collection_name)
                 .where("workspace_id", "==", workspace_id)
                 .where("session_id", "==", session_id))
        async for doc in query.stream():
            await doc.reference.delete()
            deleted += 1
    return deleted


# ---------------------------------------------------------------------------
# opportunities
# ---------------------------------------------------------------------------

async def find_opportunity_by_hash(dedup_hash: str,
                                   founder_id: str = "") -> Optional[str]:
    """Return the existing opportunity id for a dedup hash, else None."""
    query = get_client().collection("opportunities")
    if founder_id:
        query = query.where("workspace_id", "==", founder_id)
    query = query.where("dedup_hash", "==", dedup_hash)
    async for doc in query.stream():
        return doc.id
    return None


async def create_opportunity(record: dict[str, Any]) -> str:
    """Create an opportunity in DISCOVERED, deduping on dedup_hash.

    The doc id is derived from dedup_hash and the create runs in a transaction,
    so two concurrent sweeps that discover the same program can never produce
    duplicate opportunities (the old query-then-write was racy)."""
    from google.cloud import firestore as gc_firestore

    workspace_id = str(record.get("workspace_id") or record.get("founder_id") or "")
    identity = f"{workspace_id}\x1e{record['dedup_hash']}"
    doc_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
    ref = get_client().collection("opportunities").document(doc_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _create(txn) -> str:
        snap = await ref.get(transaction=txn)
        if snap.exists:
            return doc_id  # already discovered — never a duplicate
        txn.set(ref, {**record, "workspace_id": workspace_id or None,
                      "founder_id": workspace_id or None,
                      "opportunity_domain": "OPPORTUNITY",
                      "tenancy_schema_version": 1,
                      "state": "DISCOVERED",
                      "created_at": _now(), "updated_at": _now()})
        return doc_id

    return await _create(transaction)


async def get_opportunity(opportunity_id: str,
                          founder_id: str = "") -> Optional[dict[str, Any]]:
    if not opportunity_id:  # empty id would build an invalid document path
        return None
    doc = await get_client().collection("opportunities").document(opportunity_id).get()
    if not doc.exists:
        return None
    row = doc.to_dict() | {"id": doc.id}
    return row if (not founder_id or row.get("workspace_id") == founder_id) else None


async def list_unscored_opportunities(limit: int = 10,
                                      founder_id: str = "") -> list[dict[str, Any]]:
    query = get_client().collection("opportunities")
    if founder_id:
        query = query.where("workspace_id", "==", founder_id)
    query = query.where("state", "==", "DISCOVERED")
    docs = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]
    docs.sort(key=lambda d: d.get("created_at", ""))
    return docs[:limit]


async def set_opportunity_state(opportunity_id: str, state: str,
                                founder_id: str = "", **fields: Any) -> None:
    ref = get_client().collection("opportunities").document(opportunity_id)
    if founder_id:
        snapshot = await ref.get()
        if not snapshot.exists or snapshot.to_dict().get("workspace_id") != founder_id:
            return
    await ref.update({"state": state, **fields, "updated_at": _now()})


# ---------------------------------------------------------------------------
# applications
# ---------------------------------------------------------------------------

async def create_application(founder_id: str, opportunity_id: str, checklist: list[dict]) -> str:
    """Backward-compatible wrapper around the transactional create contract."""
    result = await get_or_create_application(founder_id, opportunity_id, checklist)
    return result["application_id"]


async def find_application_by_founder_opportunity(
        founder_id: str, opportunity_id: str) -> Optional[dict[str, Any]]:
    """Find a legacy/random-id application for this founder and opportunity.

    New application IDs are deterministic, but records created before that
    invariant shipped used UUIDs. This compatibility lookup prevents selecting
    one of those opportunities from creating a second application.
    """
    query = (get_client().collection("applications")
             .where("workspace_id", "==", founder_id)
             .where("founder_id", "==", founder_id))
    matches = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()
               if doc.to_dict().get("opportunity_id") == opportunity_id]
    if not matches:
        return None
    matches.sort(
        key=lambda row: (row.get("state") == "CLOSED", row.get("created_at", "")))
    return matches[0]


async def get_or_create_application(
        founder_id: str, opportunity_id: str, checklist: list[dict]) -> dict[str, Any]:
    """Transactionally create at most one application per founder/opportunity.

    A deterministic document id is the durable uniqueness constraint. A
    caller-side lookup alone is racy: two sessions can both observe absence and
    create UUID-backed records. The transaction returns whether it performed
    the write so duplicate selection can rehydrate the existing application.
    """
    from google.cloud import firestore as gc_firestore

    digest = hashlib.sha256(f"{founder_id}:{opportunity_id}".encode()).hexdigest()
    doc_id = f"app_{digest[:28]}"
    ref = get_client().collection("applications").document(doc_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _create(txn):
        snap = await ref.get(transaction=txn)
        if snap.exists:
            return {"application_id": doc_id, "created": False,
                    "application": snap.to_dict() | {"id": doc_id}}
        now = _now()
        record = {
            "opportunity_id": opportunity_id,
            "workspace_id": founder_id,
            "founder_id": founder_id,
            "application_domain": "GRANT_APPLICATION",
            "tenancy_schema_version": 1,
            "state": "INTERVIEWING",
            "checklist": checklist,
            "interview_qa": [],
            "draft_sections": [],
            "form_fill_report": None,
            "submission": None,
            "followups": [],
            "created_at": now,
            "updated_at": now,
        }
        txn.set(ref, record)
        return {"application_id": doc_id, "created": True,
                "application": record | {"id": doc_id}}

    return await _create(transaction)


async def get_application(application_id: str,
                          founder_id: str) -> Optional[dict[str, Any]]:
    """Return an application only through its workspace ownership boundary."""
    doc = await get_client().collection("applications").document(application_id).get()
    if not doc.exists:
        return None
    row = doc.to_dict() | {"id": doc.id}
    return row if (row.get("workspace_id") == founder_id
                   and row.get("founder_id") == founder_id) else None


async def get_application_for_provider_event(
        application_id: str) -> Optional[dict[str, Any]]:
    """Provider-correlation lookup; callers must verify transport first.

    The returned workspace is routing authority for a globally unique opaque
    application ID. It is never exposed as a founder-facing point read.
    """
    if not application_id:
        return None
    doc = await get_client().collection("applications").document(
        application_id).get()
    return doc.to_dict() | {"id": doc.id} if doc.exists else None


async def append_application_followup(
    application_id: str, entry: dict[str, Any],
    dedupe_key: str = "") -> dict[str, Any]:
    """Transactionally append one follow-up, refusing a duplicate.

    Every writer previously did a read-modify-write of the whole `followups`
    array through the blind `update_application`, so two concurrent writers
    (an inbound-mail scan and an agent tool, or two mail scans) each read N
    items and each wrote N+1 — silently destroying one follow-up. A redelivered
    provider message also appended a second copy.

    `dedupe_key` is matched against `external_event_id`, making redelivery an
    idempotent success rather than a duplicate effect.
    """
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("applications").document(application_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _append(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists:
            return {"status": "error", "error": True,
                    "message": f"application {application_id} not found"}
        current = snap.to_dict()
        followups = list(current.get("followups") or [])
        if dedupe_key and any(
                str(item.get("external_event_id") or "") == dedupe_key
                for item in followups if isinstance(item, dict)):
            return {"status": "success", "duplicate": True,
                    "followups": len(followups)}
        followups.append(entry)
        txn.update(ref, {"followups": followups, "updated_at": _now()})
        return {"status": "success", "duplicate": False,
                "followups": len(followups)}

    return await _append(transaction)


async def update_application(application_id: str, **fields: Any) -> None:
    await get_client().collection("applications").document(application_id).update(
        {**fields, "updated_at": _now()}
    )


# ---------------------------------------------------------------------------
# evidence_checks (20) — one immutable report per deterministic input hash
# ---------------------------------------------------------------------------

async def get_evidence_check(report_id: str, founder_id: str,
                             application_id: str) -> Optional[dict[str, Any]]:
    """Fetch a report, enforcing ownership.

    Ownership is a parameter rather than a caller-side check: a corrupted or
    guessed pointer must not be able to surface another founder's report.
    """
    snap = await get_client().collection("evidence_checks").document(report_id).get()
    if not snap.exists:
        return None
    row = snap.to_dict()
    if (row.get("workspace_id") != founder_id
            or row.get("founder_id") != founder_id
            or row.get("application_id") != application_id):
        return None
    return {**row, "report_id": report_id}


async def claim_evidence_check(report_id: str, row: dict[str, Any],
                               lease_seconds: int) -> dict[str, Any]:
    """Take the execution lease transactionally.

    Read-then-set is not enough: two concurrent completions for the same hash
    both saw "no row" and both called the provider. The transaction makes the
    claim conditional on the row being absent, COMPLETE, or lease-expired.
    Returns {claimed: bool, existing: report|None}.
    """
    import time as _time

    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("evidence_checks").document(report_id)
    transaction = get_client().transaction()
    owner = uuid.uuid4().hex

    @gc_firestore.async_transactional
    async def _claim(txn):
        snap = await ref.get(transaction=txn)
        if snap.exists:
            current = snap.to_dict()
            if current.get("execution_status") == "COMPLETE":
                return {"claimed": False, "existing": {**current, "report_id": report_id}}
            started = float(current.get("lease_started_epoch") or 0)
            if (_time.time() - started) <= float(current.get("lease_seconds") or lease_seconds):
                return {"claimed": False, "existing": None}   # someone else holds it
        workspace_id = str(row.get("workspace_id") or row.get("founder_id") or "")
        txn.set(ref, {**row, "workspace_id": workspace_id,
                      "founder_id": workspace_id,
                      "evidence_domain": "APPLICATION_EVIDENCE",
                      "tenancy_schema_version": 1,
                      "execution_status": "PREPARED", "lease_owner": owner,
                      "lease_started_epoch": _time.time(), "lease_seconds": lease_seconds,
                      "created_at": _now()})
        return {"claimed": True, "existing": None, "lease_owner": owner}

    return await _claim(transaction)


async def complete_evidence_check(report_id: str, report: dict[str, Any],
                                  lease_owner: str, application_id: str) -> bool:
    """PREPARED -> COMPLETE and move the application's pointer, in ONE transaction.

    Conditional on still holding the lease, so a slow loser cannot overwrite a
    terminal report or replace a newer pointer with its own. Terminal contents
    are immutable once COMPLETE.
    """
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("evidence_checks").document(report_id)
    app_ref = get_client().collection("applications").document(application_id)
    transaction = get_client().transaction()
    payload = {k: v for k, v in report.items() if k != "report_id"}

    @gc_firestore.async_transactional
    async def _complete(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists:
            return False
        current = snap.to_dict()
        if current.get("execution_status") == "COMPLETE":
            return False                       # immutable
        if current.get("lease_owner") != lease_owner:
            return False                       # lease lost to a reclaimer
        txn.update(ref, {**payload, "execution_status": "COMPLETE", "completed_at": _now()})
        txn.update(app_ref, {"latest_evidence_check_id": report_id, "updated_at": _now()})
        return True

    return await _complete(transaction)


async def guarded_application_transition(application_id: str, expected_state: str,
                                         to_state: str, **fields: Any) -> dict[str, Any]:
    """Move application state only if the transaction still sees expected_state."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("applications").document(application_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _transition(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists:
            return {"status": "error", "error": True,
                    "message": f"application {application_id} not found"}
        record = snap.to_dict()
        current = record.get("state")
        if current != expected_state:
            return {"status": "error", "error": True,
                    "message": f"application changed concurrently ({expected_state} → {current})",
                    "current_step": current}
        txn.update(ref, {"state": to_state, **fields, "updated_at": _now()})
        return {"status": "success", "from_step": current, "current_step": to_state}

    return await _transition(transaction)


async def update_draft_section(application_id: str, founder_id: str,
                               section_id: str, status: str,
                               edited_text: str = "") -> dict[str, Any]:
    """Atomically validate ownership and update one draft section.

    Returning the committed section array lets the caller decide whether every
    section is approved without using a stale pre-transaction snapshot.
    """
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("applications").document(application_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _update(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists:
            return {"status": "error", "error": True,
                    "message": f"application {application_id} not found"}
        record = snap.to_dict()
        if record.get("founder_id") != founder_id:
            return {"status": "error", "error": True,
                    "message": "application does not belong to this founder"}
        if record.get("state") != "AWAITING_REVIEW":
            return {
                "status": "error", "error": True,
                "message": ("section feedback is accepted only in AWAITING_REVIEW "
                            f"(current: {record.get('state')})"),
            }
        sections = [dict(section) for section in record.get("draft_sections", [])]
        section = next((item for item in sections
                        if item.get("section_id") == section_id), None)
        if section is None:
            return {"status": "error", "error": True,
                    "message": f"section {section_id} not found in application"}
        original = section.get("content", "")
        section["status"] = status
        if edited_text:
            section["content"] = edited_text
        txn.update(ref, {"draft_sections": sections, "updated_at": _now()})
        return {"status": "success", "state": record.get("state"),
                "section": section, "original": original, "sections": sections}

    return await _update(transaction)


async def create_oauth_state(state: str, verifier: str, scopes: list[str] | None,
                             account: str, connector: str = "",
                             redirect_uri: str = "",
                             workspace_id: str = "",
                             actor_id: str = "",
                             ttl_minutes: int = 10) -> None:
    """Persist one PKCE consent transaction across restarts and instances.

    ``redirect_uri`` is stored because Google requires the token exchange to
    present the byte-identical URI the authorization request used. The callback
    cannot re-derive it: it may run on a different instance, and the loopback
    origin can differ from AGENT_BASE_URL.
    """
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    await get_client().collection("oauth_states").document(state).set({
        "verifier": verifier,
        "scopes": scopes,
        "account": account,
        "connector": connector,
        "redirect_uri": redirect_uri,
        "workspace_id": workspace_id,
        "actor_id": actor_id,
        "expires_at": expires.isoformat(),
        "created_at": _now(),
    })


async def consume_oauth_state(state: str) -> Optional[dict[str, Any]]:
    """Atomically consume a valid OAuth state; unknown/replayed states fail."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("oauth_states").document(state)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _consume(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists:
            return None
        record = snap.to_dict()
        txn.delete(ref)
        if record.get("expires_at", "") <= _now():
            return None
        return record

    return await _consume(transaction)


# ---------------------------------------------------------------------------
# profiles (Founder Profile — long-term memory, docs/06)
# ---------------------------------------------------------------------------

async def get_profile(founder_id: str) -> dict[str, Any]:
    doc = await get_client().collection("profiles").document(founder_id).get()
    if doc.exists:
        return doc.to_dict()
    return {
        "version": 0,
        "facts": {},
        "fact_provenance": {},
        "fact_history": [],
        "voice_rules": [],
        "canonical_answers": [],
        "rejection_history": [],
        "decision_patterns": [],
    }


def _default_profile() -> dict[str, Any]:
    return {"version": 0, "facts": {}, "fact_provenance": {},
            "fact_history": [],
            "voice_rules": [], "canonical_answers": [],
            "rejection_history": [], "decision_patterns": []}


async def apply_profile_update(founder_id: str, kind: str, payload: dict, evidence: str,
                               idempotency_key: str | None = None, *,
                               verification_level: str = "FOUNDER_CONFIRMED",
                               provenance: dict[str, Any] | None = None) -> int:
    """Append a profile mutation and bump version, transactionally.

    Read-modify-write inside a Firestore transaction so concurrent distiller /
    ingestion writers cannot clobber each other's rules/answers or duplicate a
    version number — the version is assigned inside the transaction."""
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    level = dsc.require_closed(verification_level, dsc.VerificationLevel)
    collection = {
        "voice_rule": "voice_rules",
        "canonical_answer_update": "canonical_answers",
        "fact_update": "facts",
        "decision_pattern": "decision_patterns",
    }[kind]
    safe_provenance = {
        str(key)[:64]: value for key, value in (provenance or {}).items()
        if key in {"source", "source_id", "source_grant_id", "source_version",
                   "citation", "source_available"}
    }
    entry = {**payload, "evidence": evidence, "created_at": _now(),
             "verification_level": level.value, **safe_provenance}
    if collection != "facts":
        entry["id"] = _new_id()[:12]

    ref = get_client().collection("profiles").document(founder_id)
    receipt_ref = None
    receipt_audit_ref = None
    if idempotency_key:
        receipt_id = hashlib.sha256(
            f"{founder_id}:{idempotency_key}".encode()).hexdigest()
        receipt_ref = ref.collection("update_receipts").document(receipt_id)
        receipt_audit_ref = get_client().collection("audit").document(
            f"profile_update_{receipt_id[:32]}")
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _apply(txn) -> tuple[int, bool]:
        snap = await ref.get(transaction=txn)
        receipt = await receipt_ref.get(transaction=txn) if receipt_ref else None
        if receipt is not None and receipt.exists:
            return int(receipt.to_dict().get("profile_version") or 0), False
        profile = snap.to_dict() if snap.exists else _default_profile()
        if collection == "facts":
            facts = dict(profile.get("facts", {}))
            history = list(profile.get("fact_history") or [])
            previous_provenance = dict(profile.get("fact_provenance") or {})
            for key, value in payload.items():
                if key in facts and facts[key] != value:
                    history.append({
                        "key": key, "value": facts[key],
                        **previous_provenance.get(key, {}),
                        "verification_level": "SUPERSEDED",
                        "superseded_at": entry["created_at"],
                    })
            facts.update(payload)
            profile["facts"] = facts
            profile["fact_history"] = history[-200:]
            provenance = dict(profile.get("fact_provenance") or {})
            for key in payload:
                provenance[key] = {
                    "source": kind,
                    "evidence": evidence[:2_000],
                    "recorded_at": entry["created_at"],
                    "verification_level": level.value,
                    **safe_provenance,
                }
            profile["fact_provenance"] = provenance
        else:
            items = list(profile.get(collection, []))
            items.append(entry)
            profile[collection] = items
        profile["version"] = int(profile.get("version", 0)) + 1
        txn.set(ref, profile)
        if receipt_ref:
            txn.set(receipt_ref, {
                "idempotency_key": idempotency_key,
                "profile_version": profile["version"], "created_at": _now(),
            })
            txn.set(receipt_audit_ref, {
                "actor": "agent:distiller", "action": "profile_update",
                "target": f"profiles/{founder_id}", "result": "success",
                "detail": f"{kind}: {evidence[:200]}",
                "idempotency_key": idempotency_key, "created_at": _now(),
            })
        return profile["version"], True

    version, applied = await _apply(transaction)
    if applied and not idempotency_key:
        await audit(
            actor="agent:distiller",
            action="profile_update",
            target=f"profiles/{founder_id}",
            result="success",
            detail=f"{kind}: {evidence[:200]}",
            idempotency_key=idempotency_key,
        )
    if applied:
        # Phase 4A dual-write: compatibility document remains readable during
        # migration, while immutable per-key facts become the target authority.
        from services.durable_store import production_store
        from services.profile_fact_service import ProfileFactService

        fact_service = ProfileFactService(production_store())
        source_id = str((provenance or {}).get("source_id") or "")
        source_ref = (f"source:{source_id}" if source_id else
                      f"profile-update:{hashlib.sha256(evidence.encode()).hexdigest()[:24]}")
        if kind == "fact_update":
            items = [(str(key), value, "WORKSPACE_BUSINESS")
                     for key, value in payload.items()]
        elif kind == "canonical_answer_update":
            items = [(f"canonical_answer:{payload.get('question_key', 'unknown')}",
                      payload, "WORKSPACE_BUSINESS")]
        else:
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]
            items = [(f"{kind}:{digest}", payload, "ACTOR_PREFERENCE")]
        for key, value, scope in items:
            mirrored = await fact_service.put(
                workspace_id=founder_id, actor_id=founder_id, scope=scope,
                key=key, value=value, verification_status=level.value,
                source_refs=[source_ref],
                idempotency_key=(idempotency_key or
                                 f"compat:{version}:{kind}:{key}"))
            if mirrored.get("error"):
                await audit(
                    actor="system:profile_migration", action="profile_fact_mirror",
                    target=f"profiles/{founder_id}", result="degraded",
                    detail=f"error_code={mirrored.get('error_code', 'unknown')}")
    return version


async def record_interview_answer(founder_id: str, application_id: str,
                                  question_key: str, question: str,
                                  answer: str) -> dict[str, Any]:
    """Atomically persist one verbatim answer to its application and profile.

    The former two-step path wrote global memory first and only *then* tried to
    append to an optional application. A missing application therefore polluted
    every later workflow. This transaction admits writes only for an owned
    INTERVIEWING application and commits both projections together.
    """
    from google.cloud import firestore as gc_firestore

    app_ref = get_client().collection("applications").document(application_id)
    profile_ref = get_client().collection("profiles").document(founder_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _record(txn):
        app_snap = await app_ref.get(transaction=txn)
        if not app_snap.exists:
            return {"status": "error", "error": True,
                    "message": "no active application"}
        app = app_snap.to_dict()
        if app.get("founder_id") != founder_id:
            return {"status": "error", "error": True,
                    "message": "application does not belong to this founder"}
        if app.get("state") != "INTERVIEWING":
            return {
                "status": "error", "error": True,
                "message": ("interview answers can only be recorded in "
                            f"INTERVIEWING (current: {app.get('state')})"),
            }

        qa = list(app.get("interview_qa") or [])
        duplicate = next((row for row in qa
                          if row.get("question_key") == question_key
                          and row.get("question") == question
                          and row.get("answer") == answer), None)
        if duplicate:
            return {"status": "success", "application_id": application_id,
                    "question_key": question_key, "already_recorded": True}

        now = _now()
        qa.append({
            "question_key": question_key,
            "question": question,
            "answer": answer,
            "source": "founder_turn",
            "recorded_at": now,
        })
        profile_snap = await profile_ref.get(transaction=txn)
        profile = profile_snap.to_dict() if profile_snap.exists else _default_profile()
        facts = dict(profile.get("facts") or {})
        history = list(profile.get("fact_history") or [])
        previous_provenance = dict(profile.get("fact_provenance") or {})
        if question_key in facts and facts[question_key] != answer:
            history.append({
                "key": question_key, "value": facts[question_key],
                **previous_provenance.get(question_key, {}),
                "verification_level": "SUPERSEDED",
                "superseded_at": now,
            })
        facts[question_key] = answer
        provenance = dict(profile.get("fact_provenance") or {})
        provenance[question_key] = {
            "source": "interview_answer",
            "application_id": application_id,
            "question": question[:500],
            "recorded_at": now,
            "verification_level": "FOUNDER_CONFIRMED",
            "source_available": True,
        }
        version = int(profile.get("version", 0)) + 1
        txn.update(app_ref, {"interview_qa": qa, "updated_at": now})
        txn.set(profile_ref, {
            **profile,
            "facts": facts,
            "fact_provenance": provenance,
            "fact_history": history[-200:],
            "version": version,
        })
        return {
            "status": "success",
            "application_id": application_id,
            "question_key": question_key,
            "profile_version": version,
            "already_recorded": False,
        }

    result = await _record(transaction)
    if result.get("status") == "success" and not result.get("already_recorded"):
        await audit(
            actor="agent:interviewer",
            action="record_answer",
            target=f"applications/{application_id}",
            result="success",
            detail=f"question_key={question_key[:100]}",
        )
        from services.durable_store import production_store
        from services.profile_fact_service import ProfileFactService

        await ProfileFactService(production_store()).put(
            workspace_id=founder_id, actor_id=founder_id,
            scope="WORKSPACE_BUSINESS", key=question_key, value=answer,
            verification_status="FOUNDER_CONFIRMED",
            source_refs=[f"application:{application_id}"],
            idempotency_key=(
                f"interview:{application_id}:{question_key}:"
                f"{hashlib.sha256(answer.encode()).hexdigest()[:24]}"))
    return result


# ---------------------------------------------------------------------------
# board / pipeline views
# ---------------------------------------------------------------------------

async def list_opportunities(limit: int = 40,
                             start_after: str | None = None,
                             founder_id: str = "") -> list[dict[str, Any]]:
    """Newest-first opportunities. `start_after` (a created_at cursor) pages
    through the whole collection so a full scan is not capped at one page —
    deadline_scan pages until the collection is exhausted."""
    query = get_client().collection("opportunities")
    if founder_id:
        query = query.where("workspace_id", "==", founder_id)
    rows = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]
    rows.sort(key=lambda row: row.get("created_at", ""), reverse=True)
    if start_after is not None:
        rows = [row for row in rows if row.get("created_at", "") < start_after]
    return rows[:limit]


async def list_inflight_applications(founder_id: str) -> list[dict[str, Any]]:
    # Workspace scope is in the datastore query; CLOSED remains a local
    # projection filter so no second composite index is required.
    query = get_client().collection("applications").where(
        "workspace_id", "==", founder_id)
    docs = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]
    docs = [d for d in docs if d.get("state") != "CLOSED"]
    docs.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return docs


# ---------------------------------------------------------------------------
# conversational discovery receipts (pre-Phase-0 adapter)
# ---------------------------------------------------------------------------

async def claim_discovery_request(request_id: str, founder_id: str,
                                  context_hash: str,
                                  lease_seconds: int = 900,
                                  max_attempts: int = 3) -> dict[str, Any]:
    """Claim one opaque founder submission for bounded discovery execution.

    Cloud Tasks task-name dedupe is only a dispatch optimization. This durable
    receipt is the worker-side idempotency boundary and permits a failed or
    lease-expired attempt to be retried without treating message text as an ID.
    """
    import time as _time

    from google.cloud import firestore as gc_firestore

    doc_id = hashlib.sha256(f"{founder_id}:{request_id}".encode()).hexdigest()[:32]
    ref = get_client().collection("discovery_requests").document(doc_id)
    transaction = get_client().transaction()
    owner = uuid.uuid4().hex

    @gc_firestore.async_transactional
    async def _claim(txn):
        snap = await ref.get(transaction=txn)
        current: dict[str, Any] = {}
        if snap.exists:
            current = snap.to_dict()
            if current.get("context_hash") != context_hash:
                return {"claimed": False, "conflict": True,
                        "status": current.get("status", "UNKNOWN")}
            if current.get("status") in {"COMPLETE", "FAILED"}:
                return {"claimed": False, "duplicate": True,
                        "status": current.get("status")}
            started = float(current.get("lease_started_epoch") or 0)
            lease = float(current.get("lease_seconds") or lease_seconds)
            if current.get("status") == "RUNNING" and _time.time() - started <= lease:
                return {"claimed": False, "in_progress": True, "status": "RUNNING"}
            if int(current.get("attempt") or 0) >= max_attempts:
                txn.update(ref, {
                    "status": "FAILED", "lease_owner": "",
                    "lease_started_epoch": None,
                    "last_error_code": "retry_budget_exhausted",
                    "updated_at": _now(),
                })
                return {"claimed": False, "duplicate": True,
                        "status": "FAILED", "exhausted": True}
        now = _now()
        # Merge-safe claim (docs/23 §5.5): the public boundary writes origin/
        # display/resource fields onto the ACCEPTED receipt before dispatch —
        # a full-document replace here would erase them.
        preserved = {k: current[k] for k in (
            "origin_session_id", "origin_message_id", "display_query",
            "context", "executed_queries", "result_opportunity_ids",
            "resource_id", "dispatch_status", "dispatch_error") if k in current}
        txn.set(ref, {
            **preserved,
            "request_id": request_id,
            "workspace_id": founder_id,
            "founder_id": founder_id,
            "request_domain": "OPPORTUNITY_DISCOVERY",
            "tenancy_schema_version": 1,
            "context_hash": context_hash,
            "status": "RUNNING",
            "lease_owner": owner,
            "lease_started_epoch": _time.time(),
            "lease_seconds": lease_seconds,
            "attempt": int(current.get("attempt") or 0) + 1,
            "max_attempts": max_attempts,
            "updated_at": now,
            "created_at": current.get("created_at", now) if snap.exists else now,
        })
        return {"claimed": True, "lease_owner": owner, "status": "RUNNING",
                "receipt": {**preserved, "request_id": request_id}}

    return await _claim(transaction)


def discovery_receipt_id(founder_id: str, request_id: str) -> str:
    """The receipt document id IS the discovery_request_id (docs/23 §5.5)."""
    return hashlib.sha256(f"{founder_id}:{request_id}".encode()).hexdigest()[:32]


def discovery_receipt_record(
        request_id: str, founder_id: str, context_hash: str, *,
        origin_session_id: str, display_query: str, context: str = "",
        origin_message_id: str | None = None,
        workflow_run_id: str = "", workflow_plan_hash: str = "",
        workflow_plan_version: str = "") -> dict[str, Any]:
    """Build the canonical create-only domain root for command transaction T8."""
    now = _now()
    return {
        "request_id": request_id,
        "workspace_id": founder_id,
        "founder_id": founder_id,
        "request_domain": "OPPORTUNITY_DISCOVERY",
        "tenancy_schema_version": 1,
        "context_hash": context_hash,
        "status": "ACCEPTED",
        "origin_session_id": origin_session_id,
        "origin_message_id": origin_message_id,
        "display_query": display_query,
        "context": context,
        "executed_queries": [],
        "result_opportunity_ids": [],
        "resource_id": "",
        "dispatch_status": "pending",
        "dispatch_error": "",
        "lease_owner": "",
        "attempt": 0,
        "max_attempts": 3,
        "workflow_run_id": workflow_run_id,
        "workflow_plan_hash": workflow_plan_hash or None,
        "workflow_plan_version": workflow_plan_version or None,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }


async def create_discovery_receipt(request_id: str, founder_id: str,
                                   context_hash: str, *,
                                   origin_session_id: str,
                                   display_query: str,
                                   context: str = "",
                                   origin_message_id: str | None = None
                                   ) -> dict[str, Any]:
    """Durably accept one founder discovery submission at the public boundary
    (docs/23 §6.2). State machine: ACCEPTED → RUNNING → COMPLETE | FAILED.

    Duplicate delivery of the same request returns the existing receipt;
    reusing the request id with different normalized context is a conflict.
    """
    from google.cloud import firestore as gc_firestore

    doc_id = discovery_receipt_id(founder_id, request_id)
    ref = get_client().collection("discovery_requests").document(doc_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _create(txn):
        snap = await ref.get(transaction=txn)
        if snap.exists:
            current = snap.to_dict()
            if current.get("context_hash") != context_hash:
                return {"accepted": False, "conflict": True,
                        "discovery_request_id": doc_id,
                        "status": current.get("status", "UNKNOWN")}
            return {"accepted": True, "duplicate": True,
                    "discovery_request_id": doc_id,
                    "status": current.get("status", "ACCEPTED"),
                    "resource_id": current.get("resource_id", ""),
                    "workflow_run_id": current.get("workflow_run_id", ""),
                    "workflow_plan_hash": current.get("workflow_plan_hash"),
                    "workflow_plan_version": current.get("workflow_plan_version")}
        txn.set(ref, discovery_receipt_record(
            request_id, founder_id, context_hash,
            origin_session_id=origin_session_id,
            display_query=display_query, context=context,
            origin_message_id=origin_message_id))
        return {"accepted": True, "duplicate": False,
                "discovery_request_id": doc_id, "status": "ACCEPTED",
                "resource_id": ""}

    return await _create(transaction)


async def get_discovery_request(request_id: str, founder_id: str
                                ) -> Optional[dict[str, Any]]:
    doc_id = discovery_receipt_id(founder_id, request_id)
    snap = await get_client().collection("discovery_requests").document(
        doc_id).get()
    return (snap.to_dict() | {"id": doc_id}) if snap.exists else None


async def get_discovery_request_by_id(discovery_request_id: str
                                      ) -> Optional[dict[str, Any]]:
    """Load a receipt by its document id — the worker path (docs/23 §6.2)."""
    if not discovery_request_id:
        return None
    snap = await get_client().collection("discovery_requests").document(
        discovery_request_id).get()
    return (snap.to_dict() | {"id": discovery_request_id}) if snap.exists else None


async def update_discovery_receipt(request_id: str, founder_id: str,
                                   fields: dict[str, Any]) -> bool:
    """Bounded metadata update on the receipt (dispatch outcome, resource id,
    executed-query projection). Never changes status/lease — those move only
    through claim/finish."""
    allowed = {k: v for k, v in fields.items()
               if k in ("dispatch_status", "dispatch_error", "resource_id",
                        "executed_queries", "result_opportunity_ids")}
    if not allowed:
        return False
    doc_id = discovery_receipt_id(founder_id, request_id)
    ref = get_client().collection("discovery_requests").document(doc_id)
    snap = await ref.get()
    if not snap.exists:
        return False
    await ref.update({**allowed, "updated_at": _now()})
    return True


async def finish_discovery_request(request_id: str, founder_id: str,
                                   lease_owner: str, status: str,
                                   summary: dict[str, Any] | None = None) -> bool:
    """Finish a discovery receipt only when this attempt still owns its lease."""
    from google.cloud import firestore as gc_firestore

    doc_id = hashlib.sha256(f"{founder_id}:{request_id}".encode()).hexdigest()[:32]
    ref = get_client().collection("discovery_requests").document(doc_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _finish(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists or snap.to_dict().get("lease_owner") != lease_owner:
            return False
        txn.update(ref, {"status": status, "summary": summary or {},
                         "updated_at": _now(), "finished_at": _now()})
        return True

    return await _finish(transaction)


# ---------------------------------------------------------------------------
# feedback
# ---------------------------------------------------------------------------

async def create_feedback(founder_id: str, application_id: str, section_id: str,
                          feedback_type: str, original: str, reason: str,
                          edited_text: str, section_key: str = "") -> str:
    doc_id = _new_id()
    await get_client().collection("feedback").document(doc_id).set(
        {
            "workspace_id": founder_id,
            "founder_id": founder_id,
            "feedback_domain": "FOUNDER_DRAFT",
            "tenancy_schema_version": 1,
            "application_id": application_id,
            "section_id": section_id,
            # The stable, cross-application key ("describe_traction"). section_id
            # is a per-application UUID, so feedback history keyed on it alone
            # was always empty across applications (get_section_feedback).
            "section_key": section_key,
            "type": feedback_type,
            "original": original,
            "edited_text": edited_text,
            "reason": reason,
            "distilled": False,
            "distilled_rule_ids": [],
            "created_at": _now(),
        }
    )
    return doc_id


async def get_feedback(feedback_id: str,
                       founder_id: str = "") -> Optional[dict[str, Any]]:
    doc = await get_client().collection("feedback").document(feedback_id).get()
    if not doc.exists:
        return None
    row = doc.to_dict() | {"id": doc.id}
    if founder_id and (row.get("workspace_id") != founder_id
                       or row.get("founder_id") != founder_id):
        return None
    return row


async def mark_distilled(feedback_id: str, rule_ids: list[str],
                         founder_id: str = "") -> bool:
    ref = get_client().collection("feedback").document(feedback_id)
    if founder_id:
        snapshot = await ref.get()
        if (not snapshot.exists
                or snapshot.to_dict().get("workspace_id") != founder_id
                or snapshot.to_dict().get("founder_id") != founder_id):
            return False
    await ref.update({"distilled": True, "distilled_rule_ids": rule_ids})
    return True


# ---------------------------------------------------------------------------
# approvals (docs/12: tokens exist only here — never in model context)
# ---------------------------------------------------------------------------

async def create_approval(application_id: str, gate: str, ttl_minutes: int,
                          details: Optional[dict[str, Any]] = None,
                          founder_id: str = "", session_id: str = "",
                          subject_hash: str = "",
                          bindings: Optional[dict[str, Any]] = None) -> str:
    from datetime import timedelta

    doc_id = _new_id()
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    await get_client().collection("approvals").document(doc_id).set(
        {
            "application_id": application_id,
            # Only the platform approval service mints v2 records.  Keeping a
            # v1 marker when the internal low-level helper is called without
            # bindings makes the migration window explicit; the Phase 3
            # backfill can identify those rows rather than treating missing
            # fields as a valid v2 approval.
            "schema_version": 2 if bindings else 1,
            "approval_domain": "GRANT_APPLICATION",
            "workspace_id": founder_id,
            "gate": gate,
            "founder_id": founder_id,
            "requested_by_actor_id": founder_id,
            "approving_actor_requirement": "INTERACTIVE_MEMBER",
            "session_id": session_id,
            "details": details or {},  # what the founder is approving (e.g. email to/subject/body)
            # Immutable identity of the subject (docs/02): for
            # submit_application, application id + fill-report portal/mapping
            # hashes. Submit must match it after any reopen.
            "subject_hash": subject_hash or None,
            **dict(bindings or {}),
            "claim_id": None, "claimed_action_id": None,
            "claimed_at": None, "voided_at": None, "void_reason": None,
            "token": None,
            "status": "PENDING",
            "expires_at": expires.isoformat(),
            "granted_by": None,
            "consumed_at": None,
            "created_at": _now(),
        }
    )
    return doc_id


async def list_pending_approvals(founder_id: str = "", session_id: str = "",
                                 *, now: datetime | str | None = None) -> list[dict[str, Any]]:
    """Pending approvals scoped to one founder session, newest first.

    ``now`` is injectable because waiting views must be a pure function of the
    caller's checkpoint time.  Production callers omit it and use UTC wall
    time; deterministic replay and tests pass the workflow checkpoint.
    """
    if isinstance(now, datetime):
        now_stamp = now.astimezone(timezone.utc).isoformat()
    else:
        now_stamp = str(now or _now())
    query = get_client().collection("approvals").where("status", "==", "PENDING")
    if founder_id:
        query = query.where("workspace_id", "==", founder_id)
    out = []
    async for doc in query.stream():
        record = doc.to_dict()
        if record.get("expires_at", "") <= now_stamp:
            continue
        if founder_id and (record.get("workspace_id") != founder_id
                           or record.get("founder_id") != founder_id):
            continue
        if session_id and record.get("session_id") != session_id:
            continue
        out.append(record | {"id": doc.id})
    return sorted(out, key=lambda a: a.get("created_at", ""), reverse=True)


async def grant_approval(approval_id: str, founder_id: str) -> None:
    await get_client().collection("approvals").document(approval_id).update(
        {"token": _new_id(), "status": "GRANTED", "granted_by": founder_id}
    )


async def deny_approval(approval_id: str) -> None:
    await get_client().collection("approvals").document(approval_id).update({"status": "DENIED"})


async def resolve_approval_decision(approval_id: str, decision: str,
                                    founder_id: str,
                                    session_id: str, *,
                                    deciding_actor_id: str = "") -> dict[str, Any]:
    """Linearize one founder decision and its audit record.

    Reading a PENDING row and updating it in separate calls lets two browser
    requests both report success. Firestore retries this transaction when the
    approval changes, so exactly one decision can win and the losing caller
    receives a stable conflict instead of a false confirmation.
    """
    from google.cloud import firestore as gc_firestore

    if decision not in {"grant", "deny"}:
        return _contract_error("decision must be grant|deny")
    if not founder_id or not session_id:
        return _contract_error("approval requires a founder-bound session",
                               "owner_mismatch")

    client = get_client()
    ref = client.collection("approvals").document(approval_id)
    audit_ref = client.collection("audit").document(_new_id())
    wake_id = "wake_" + hashlib.sha256(
        f"approval\x1f{approval_id}\x1f{session_id}".encode()).hexdigest()[:32]
    wake_ref = client.collection("wake_deliveries").document(wake_id)
    transaction = client.transaction()

    @gc_firestore.async_transactional
    async def _resolve(txn):
        snapshot = await ref.get(transaction=txn)
        if not snapshot.exists:
            return _contract_error("approval not found", "not_found")
        approval = snapshot.to_dict()
        if ((approval.get("workspace_id") or approval.get("founder_id")) != founder_id
                or approval.get("founder_id") != founder_id
                or approval.get("session_id") != session_id):
            return _contract_error(
                "approval does not belong to this founder session",
                "owner_mismatch")
        if approval.get("status") != "PENDING":
            return _contract_error(
                f"approval already {approval.get('status', 'resolved')}",
                "approval_terminal")

        now = _now()
        if approval.get("expires_at", "") <= now:
            txn.update(ref, {"status": "EXPIRED", "token": None,
                             "updated_at": now})
            return _contract_error(
                "approval request expired; request a new one",
                "approval_expired")

        fields: dict[str, Any] = {
            "status": "GRANTED" if decision == "grant" else "DENIED",
            "decided_by_actor_id": deciding_actor_id or founder_id,
            "decided_at": now,
            "updated_at": now,
        }
        if decision == "grant":
            fields.update(token=_new_id(), granted_by=founder_id)
        else:
            fields.update(token=None, denied_by=founder_id)
        txn.update(ref, fields)
        txn.create(audit_ref, {
            "workspace_id": founder_id,
            "actor": f"founder:{founder_id}",
            "actor_id": deciding_actor_id or founder_id,
            "action": f"approval_{decision}",
            "idempotency_key": None,
            "target": f"approvals/{approval_id}",
            "result": "success",
            "detail": "",
            "created_at": now,
        })
        if decision == "grant":
            txn.create(wake_ref, {
                "schema_version": 1, "delivery_id": wake_id,
                "workspace_id": founder_id, "delivery_domain": "FOUNDER_WAKE",
                "founder_id": founder_id, "session_id": session_id,
                "source_kind": "approval", "source_id": approval_id,
                "notice": ("Resume: founder approved "
                           f"{approval.get('gate', 'action')} at the approval gate."),
                "state_delta": {"pending_signals": []},
                "status": "PENDING", "attempt": 0,
                "lease_owner": None, "lease_started_at": None,
                "lease_seconds": 120, "last_error_code": None,
                "created_at": now, "updated_at": now,
                "delivered_at": None,
            })
        return {
            "status": "success", "approval_id": approval_id,
            "decision": decision, "gate": approval.get("gate", ""),
            "application_id": approval.get("application_id", ""),
            "session_id": approval.get("session_id", ""),
            "decided_by_actor_id": deciding_actor_id or founder_id,
            "wake_delivery_id": wake_id if decision == "grant" else None,
        }

    return await _resolve(transaction)


async def get_approval(approval_id: str) -> Optional[dict[str, Any]]:
    doc = await get_client().collection("approvals").document(approval_id).get()
    return doc.to_dict() | {"id": doc.id} if doc.exists else None


async def get_approval_for_workspace(
        founder_id: str, approval_id: str) -> Optional[dict[str, Any]]:
    """Workspace-scoped point read with collapsed cross-tenant absence."""
    row = await get_approval(approval_id)
    if not row:
        return None
    owner = row.get("workspace_id") or row.get("founder_id")
    return row if owner == founder_id else None


async def find_valid_approval(application_id: str, gate: str = "",
                              founder_id: str = "", session_id: str = "",
                              subject_hash: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Server-side lookup for submit: GRANTED, unexpired, unconsumed.

    `subject_hash` (when not None) additionally binds the lookup to exactly
    what the founder approved (docs/02 `approvals.subject_hash`). A row with no
    stored `subject_hash` never satisfies a bound lookup — legacy rows written
    before the binding existed fail closed and need a fresh approval.
    """
    now = _now()
    query = (
        get_client()
        .collection("approvals")
        .where("application_id", "==", application_id)
        .where("status", "==", "GRANTED")
    )
    if founder_id:
        query = query.where("workspace_id", "==", founder_id)
    async for doc in query.stream():
        record = doc.to_dict()
        if (record.get("expires_at", "") > now
                and (not gate or record.get("gate") == gate)
                and (not founder_id or record.get("founder_id") == founder_id)
                and (not session_id or record.get("session_id") == session_id)
                and (subject_hash is None
                     or (record.get("subject_hash") or "") == subject_hash)):
            return record | {"id": doc.id}
    return None


async def expire_stale_approvals(application_id: str, gate: str,
                                 subject_hash: str = "") -> list[str]:
    """Expire open (PENDING/GRANTED) approvals whose subject no longer matches.

    Called when a new fill materially changes the portal form or the intended
    mapping: the founder approved a different thing, so neither an open request
    nor an existing grant may survive it (docs/12 §Approval tokens, docs/22
    §Fill Stop and restart recovery). Passing an empty `subject_hash` expires
    every open approval for the gate. The token is cleared with the status so an
    expired row carries nothing that could later be replayed.
    """
    expired: list[str] = []
    collection = get_client().collection("approvals")
    query = collection.where("application_id", "==", application_id)
    async for doc in query.stream():
        record = doc.to_dict()
        if record.get("gate") != gate:
            continue
        if record.get("status") not in ("PENDING", "GRANTED"):
            continue
        if subject_hash and (record.get("subject_hash") or "") == subject_hash:
            continue
        await collection.document(doc.id).update(
            {"status": "EXPIRED", "token": None})
        expired.append(doc.id)
    return expired


async def find_pending_approval(application_id: str, gate: str = "",
                                session_id: str = "",
                                founder_id: str = "") -> Optional[dict[str, Any]]:
    """The open PENDING approval for an application (UI approval gate)."""
    now = _now()
    query = (
        get_client()
        .collection("approvals")
        .where("application_id", "==", application_id)
        .where("status", "==", "PENDING")
    )
    if founder_id:
        query = query.where("workspace_id", "==", founder_id)
    async for doc in query.stream():
        record = doc.to_dict()
        if record.get("expires_at", "") <= now:
            continue
        if gate and record.get("gate") != gate:
            continue
        if session_id and record.get("session_id") != session_id:
            continue
        if founder_id and record.get("founder_id") != founder_id:
            continue
        return record | {"id": doc.id}
    return None


async def consume_approval(approval_id: str) -> None:
    await get_client().collection("approvals").document(approval_id).update(
        {"status": "CONSUMED", "consumed_at": _now()}
    )


async def claim_approval(approval_id: str) -> bool:
    """Atomically consume a still-GRANTED approval before an external action."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("approvals").document(approval_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _claim(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists:
            return False
        record = snap.to_dict()
        if record.get("status") != "GRANTED" or record.get("expires_at", "") <= _now():
            return False
        txn.update(ref, {"status": "CONSUMED", "consumed_at": _now()})
        return True

    return await _claim(transaction)


async def find_successful_action(idempotency_key: str) -> Optional[dict[str, Any]]:
    """Second idempotency layer: prior successful action with this key?"""
    query = (
        get_client()
        .collection("audit")
        .where("idempotency_key", "==", idempotency_key)
        .where("result", "==", "success")
        .limit(1)
    )
    async for doc in query.stream():
        return doc.to_dict() | {"id": doc.id}
    return None


async def list_audit(limit: int = 30) -> list[dict[str, Any]]:
    """Recent audit rows, newest first (UI activity feed)."""
    query = get_client().collection("audit").order_by("created_at", direction="DESCENDING").limit(limit)
    return [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]


# ---------------------------------------------------------------------------
# browser run registry + crash-safe action ledger (docs/02, 18)
# ---------------------------------------------------------------------------

async def create_browser_run(record: dict[str, Any]) -> str:
    """Persist a fully formed BrowserRun using its server-minted run id."""
    run_id = record["run_id"]
    now = _now()
    await get_client().collection("browser_runs").document(run_id).set(
        {**record, "created_at": record.get("created_at", now), "updated_at": now}
    )
    return run_id


async def get_browser_run(run_id: str) -> Optional[dict[str, Any]]:
    if not run_id:
        return None
    doc = await get_client().collection("browser_runs").document(run_id).get()
    return doc.to_dict() | {"run_id": doc.id} if doc.exists else None


async def update_browser_run(run_id: str, **fields: Any) -> None:
    """Update non-lifecycle fields; status changes require the graph transaction."""
    from google.cloud import firestore as gc_firestore

    fields.pop("goal", None)
    fields.pop("run_id", None)
    fields.pop("status", None)
    ref = get_client().collection("browser_runs").document(run_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _update(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists or snap.to_dict().get("status") == "closed":
            return
        txn.update(ref, {**fields, "updated_at": _now()})

    await _update(transaction)


_BROWSER_EDGES = {
    "opening": {"active", "stopping"},
    "active": {"blocked", "stopping"},
    "blocked": {"stopping"},
    "stopping": {"closed"},
    "closed": set(),
}


async def transition_browser_run(
    run_id: str,
    status: str,
    *,
    owner_loss: bool = False,
    **fields: Any,
) -> dict[str, Any]:
    """Transactionally enforce docs/22's browser lifecycle and version bump."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("browser_runs").document(run_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _transition(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists:
            return {"ok": False, "missing": True}
        current = snap.to_dict()
        old = current.get("status")
        if old == status:
            return {"ok": True, "idempotent": True, **current}
        allowed = status in _BROWSER_EDGES.get(old, set())
        if owner_loss and old in {"opening", "active", "blocked", "stopping"}:
            allowed = status == "closed"
        if not allowed:
            return {"ok": False, "from": old, "to": status}
        update = {
            **fields,
            "status": status,
            "version": int(current.get("version", 0)) + 1,
            "updated_at": _now(),
        }
        txn.update(ref, update)
        return {"ok": True, **current, **update, "run_id": run_id}

    return await _transition(transaction)


async def mutate_browser_run_view(run_id: str, **fields: Any) -> dict[str, Any]:
    """Atomically mutate UI-visible non-status fields and bump version."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("browser_runs").document(run_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _mutate(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists:
            return {"ok": False, "missing": True}
        current = snap.to_dict()
        if current.get("status") == "closed":
            return {"ok": False, "closed": True, **current}
        update = {
            **fields,
            "version": int(current.get("version", 0)) + 1,
            "updated_at": _now(),
        }
        txn.update(ref, update)
        return {"ok": True, **current, **update, "run_id": run_id}

    return await _mutate(transaction)


async def renew_browser_lease(
    run_id: str,
    expires_at: str,
    *,
    expected_generation: int | None = None,
    lease_generation: int | None = None,
) -> dict[str, Any]:
    """Transactionally mint the next lease generation for a live run.

    Read-modify-write outside a transaction let two concurrent renewals mint
    the SAME generation (so a stale expiry task still matched), and could renew
    a run that reached `stopping` in between.
    """
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("browser_runs").document(run_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _renew(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists:
            return {"ok": False, "missing": True}
        current = snap.to_dict()
        if current.get("status") not in {"opening", "active", "blocked"}:
            return {"ok": False, "status": current.get("status")}
        current_generation = int(current.get("lease_generation", 0))
        if (expected_generation is not None
                and current_generation != int(expected_generation)):
            return {"ok": False, "superseded": True,
                    "lease_generation": current_generation}
        generation = (int(lease_generation) if lease_generation is not None
                      else current_generation + 1)
        if generation <= current_generation:
            return {"ok": False, "invalid_generation": True,
                    "lease_generation": current_generation}
        txn.update(ref, {"lease_generation": generation,
                         "expires_at": expires_at, "updated_at": _now()})
        return {"ok": True, "lease_generation": generation, "expires_at": expires_at}

    return await _renew(transaction)


async def commit_browser_frame(
    run_id: str,
    candidate_seq: int,
    frame: dict[str, Any],
    artifact: str | None,
    *,
    closing: bool = False,
) -> dict[str, Any]:
    """Commit immutable frame metadata and latest RunView atomically.

    Artifact bytes are uploaded before this call. A counter precondition makes
    a conflicting upload an undiscoverable lifecycle-cleaned orphan.

    `closing=True` marks the terminal closed-frame written by the stop path.
    Ordinary frames are refused once the run reaches `stopping`: a surviving
    action task could otherwise land an after-frame *behind* the closed frame
    (the close path commits the closed frame while still `stopping`), leaving
    a stray screenshot as the run's terminal evidence.
    """
    from google.cloud import firestore as gc_firestore

    run_ref = get_client().collection("browser_runs").document(run_id)
    frame_ref = run_ref.collection("frames").document(str(candidate_seq))
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _commit(txn):
        snap = await run_ref.get(transaction=txn)
        if not snap.exists:
            return {"committed": False, "missing": True}
        run = snap.to_dict()
        if run.get("status") == "closed":
            return {"committed": False, "closed": True}
        if run.get("status") == "stopping" and not closing:
            return {"committed": False, "stopping": True}
        if int(run.get("frame_seq", 0)) + 1 != candidate_seq:
            return {
                "committed": False,
                "conflict": True,
                "current_seq": int(run.get("frame_seq", 0)),
            }
        version = int(run.get("version", 0)) + 1
        value = {
            **frame,
            "seq": candidate_seq,
            "run_id": run_id,
            "run_version": version,
            "artifact": artifact,
            "created_at": _now(),
        }
        # create(), not set(): frames are immutable, so a counter regression
        # (e.g. a bad backfill) must fail loudly rather than silently
        # overwrite committed evidence.
        txn.create(frame_ref, value)
        update: dict[str, Any] = {
            "frame_seq": candidate_seq,
            "version": version,
            "updated_at": _now(),
        }
        if artifact:
            update["screenshot_artifact"] = artifact
        txn.update(run_ref, update)
        return {"committed": True, **value}

    return await _commit(transaction)


async def list_browser_runs(app_name: str, user_id: str, session_id: str,
                            kind: str | None = None) -> list[dict[str, Any]]:
    """List one session's runs newest-first without a composite-index dependency."""
    query = get_client().collection("browser_runs").where("user_id", "==", user_id)
    rows = [doc.to_dict() | {"run_id": doc.id} async for doc in query.stream()]
    rows = [row for row in rows
            if row.get("app_name") == app_name and row.get("session_id") == session_id]
    if kind:
        rows = [row for row in rows if row.get("kind") == kind]
    return sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)


async def find_active_browser_run(app_name: str, user_id: str, session_id: str,
                                  kind: str = "browse") -> Optional[dict[str, Any]]:
    rows = await list_browser_runs(app_name, user_id, session_id, kind)
    return next((row for row in rows if row.get("status") == "active"), None)


async def list_active_browser_runs() -> list[dict[str, Any]]:
    query = get_client().collection("browser_runs").where("status", "==", "active")
    return [doc.to_dict() | {"run_id": doc.id} async for doc in query.stream()]


_NONTERMINAL_BROWSER_STATES = ("opening", "active", "blocked", "stopping")


async def list_nonterminal_browser_runs(
    owner_instance: str | None = None,
) -> list[dict[str, Any]]:
    """Return durable runs that still claim browser ownership.

    The status filter runs server-side (one small `in` query per call instead
    of streaming the whole collection on every startup/snapshot).

    `owner_instance` restricts the result to runs stamped by that process. A
    process may only reconcile runs it can prove nobody else owns: during a
    rolling deploy two revisions overlap, and an unfenced sweep would close the
    *other* live instance's runs while their contexts keep executing.
    """
    query = get_client().collection("browser_runs").where(
        "status", "in", list(_NONTERMINAL_BROWSER_STATES))
    rows = [doc.to_dict() | {"run_id": doc.id} async for doc in query.stream()]
    if owner_instance is not None:
        rows = [row for row in rows if row.get("owner_instance") == owner_instance]
    return rows


async def reserve_browser_action(run_id: str, now_iso: str, max_actions: int) -> dict[str, Any]:
    """Atomically reserve one action against the durable count/deadline budget."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("browser_runs").document(run_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _reserve(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists:
            return {"exceeded": True, "reason": "closed", "count": 0}
        run = snap.to_dict()
        count = int(run.get("action_count", 0))
        if run.get("status") != "active":
            return {"exceeded": True, "reason": "closed", "count": count}
        if run.get("deadline_at", "") <= now_iso:
            return {"exceeded": True, "reason": "time", "count": count}
        if count >= max_actions:
            return {"exceeded": True, "reason": "count", "count": count}
        count += 1
        txn.update(ref, {"action_count": count, "updated_at": now_iso})
        return {"exceeded": False, "reason": None, "count": count}

    return await _reserve(transaction)


async def get_browser_action(run_id: str, action_id: str) -> Optional[dict[str, Any]]:
    ref = (get_client().collection("browser_runs").document(run_id)
           .collection("actions").document(action_id))
    doc = await ref.get()
    return doc.to_dict() | {"action_id": doc.id} if doc.exists else None


async def prepare_browser_action(run_id: str, action_id: str, record: dict[str, Any]) -> dict:
    """Create PREPARED once; return the existing row on an invocation retry."""
    from google.cloud import firestore as gc_firestore

    ref = (get_client().collection("browser_runs").document(run_id)
           .collection("actions").document(action_id))
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _prepare(txn):
        snap = await ref.get(transaction=txn)
        if snap.exists:
            return {"created": False, **snap.to_dict(), "action_id": snap.id}
        value = {**record, "status": "PREPARED", "created_at": _now()}
        txn.set(ref, value)
        return {"created": True, **value, "action_id": action_id}

    return await _prepare(transaction)


_TERMINAL_ACTION_STATES = {"SUCCEEDED", "FAILED", "UNCERTAIN"}


async def update_browser_action(
    run_id: str, action_id: str, **fields: Any
) -> dict[str, Any]:
    """Update one ledger row, refusing to rewrite a terminal outcome.

    A stop/crash terminalizes in-flight rows as UNCERTAIN. A late action task
    that survived the cancel bound would otherwise overwrite that row with
    SUCCEEDED/FAILED, erasing the very ambiguity the ledger exists to record.
    """
    from google.cloud import firestore as gc_firestore

    ref = (get_client().collection("browser_runs").document(run_id)
           .collection("actions").document(action_id))
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _update(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists:
            return {"updated": False, "missing": True}
        current = snap.to_dict()
        if current.get("status") in _TERMINAL_ACTION_STATES:
            return {"updated": False, **current, "action_id": action_id}
        update = {**fields, "updated_at": _now()}
        txn.update(ref, update)
        return {"updated": True, **current, **update, "action_id": action_id}

    return await _update(transaction)


async def mark_prepared_browser_actions_uncertain(
    run_id: str, reason: str
) -> int:
    """Terminalize in-flight ledger rows before a context is closed."""
    from google.cloud import firestore as gc_firestore

    actions = (
        get_client().collection("browser_runs").document(run_id).collection("actions")
    )
    rows = [doc async for doc in actions.stream()]
    changed = 0
    for doc in rows:
        transaction = get_client().transaction()

        @gc_firestore.async_transactional
        async def _mark(txn, ref=doc.reference):
            latest = await ref.get(transaction=txn)
            if not latest.exists or latest.to_dict().get("status") != "PREPARED":
                return False
            txn.update(
                ref,
                {
                    "status": "UNCERTAIN",
                    "uncertainty_reason": reason,
                    "updated_at": _now(),
                },
            )
            return True

        changed += int(await _mark(transaction))
    return changed


# ---------------------------------------------------------------------------
# artifacts + ingestions (docs/02, 06 §bootstrap)
# ---------------------------------------------------------------------------

async def register_artifact_ingestion(
    *, founder_id: str, session_id: str, scope: str, source_type: str,
    source_ref: str, storage_name: str, declared_content_type: str,
    detected_content_type: str, detected_extension: str, size_bytes: int,
    sha256: str, connection_id: str | None = None,
    source_grant_id: str | None = None,
    provider_source_id: str | None = None,
    provider_version: str | None = None,
    provider_modified_at: str | None = None,
    provider_content_type: str | None = None,
    authority: str = "reference_only",
    kind: str = "document",
    image_metadata: dict[str, Any] | None = None,
    document_id: str | None = None,
    occurrence_key: str | None = None,
) -> str:
    """Atomically register source metadata and a QUEUED ingestion.

    v1 deliberately shares the opaque id between records so existing
    ``attachment_refs`` remain compatible while authorization/provenance and
    execution state stay separate concepts.
    """
    from google.cloud import firestore as gc_firestore

    if scope not in {"profile", "reference_only"}:
        raise ValueError("invalid ingestion scope")
    if kind not in {"document", "image"}:
        raise ValueError("invalid artifact kind")
    if kind == "image" and scope != "reference_only":
        raise ValueError("image artifacts are reference_only")
    authority = "profile_candidate" if scope == "profile" else "reference_only"
    doc_id = document_id or _new_id()
    if not re.fullmatch(r"[a-f0-9]{32}", doc_id):
        raise ValueError("invalid ingestion id")
    now = _now()
    retention_expires_at = (
        (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        if scope == "reference_only" else None)
    artifact_ref = get_client().collection("artifacts").document(doc_id)
    ingestion_ref = get_client().collection("ingestions").document(doc_id)
    audit_id = hashlib.sha256(f"ingestion-audit:{doc_id}".encode()).hexdigest()[:32]
    audit_ref = get_client().collection("audit").document(audit_id)
    artifact_row = {
        "workspace_id": founder_id,
        "founder_id": founder_id,
        "artifact_domain": "FOUNDER_EVIDENCE",
        "tenancy_schema_version": 1,
        "session_id": session_id,
        "kind": kind,
        "scope": scope,
        "source_type": source_type,
        "source_ref": source_ref,
        "storage_name": storage_name,
        # Compatibility with callers that still display ``artifact``.
        "artifact": storage_name,
        "declared_content_type": declared_content_type,
        "detected_content_type": detected_content_type,
        "detected_extension": detected_extension,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "connection_id": connection_id,
        "source_grant_id": source_grant_id,
        "provider_source_id": provider_source_id,
        "provider_version": provider_version,
        "provider_modified_at": provider_modified_at,
        "provider_content_type": provider_content_type,
        "authority": authority,
        "occurrence_key": occurrence_key,
        "provenance_status": "PENDING",
        "status": "QUEUED",
        "index_generation": "",
        "extractor_version": ("alex-image-observation:v1" if kind == "image"
                              else "alex-document-extractor:v1"),
        "model_version": os.environ.get("ADK_MODEL", "gemini-3.6-flash"),
        "retention_policy": "profile" if scope == "profile" else "session",
        # Unsaved/session-only imports are memory-ineligible and live for at
        # most 24 hours. The scheduled lifecycle worker deletes bytes first;
        # this timestamp is durable evidence, not a Firestore-only deletion.
        "retention_expires_at": retention_expires_at,
        "created_at": now,
        "updated_at": now,
        **(image_metadata or {}),
    }
    ingestion_row = {
        "artifact_id": doc_id,
        "workspace_id": founder_id,
        "founder_id": founder_id,
        "ingestion_domain": "FOUNDER_SOURCE",
        "tenancy_schema_version": 1,
        "session_id": session_id,
        "kind": kind,
        "scope": scope,
        "source_type": source_type,
        "source_ref": source_ref,
        "artifact": storage_name,
        "content_type": detected_content_type,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "connection_id": connection_id,
        "source_grant_id": source_grant_id,
        "provider_source_id": provider_source_id,
        "provider_version": provider_version,
        "provider_modified_at": provider_modified_at,
        "authority": authority,
        "occurrence_key": occurrence_key,
        "provenance_status": "PENDING",
        "status": "QUEUED",
        "proposed_updates": [],
        "auto_applied": 0,
        "needs_founder_count": 0,
        "chunk_count": 0,
        "confirmed_at": None,
        "created_at": now,
        "updated_at": now,
        "retention_expires_at": retention_expires_at,
        **({key: value for key, value in (image_metadata or {}).items()
            if key in {"width", "height", "pixel_count"}}),
    }
    audit_row = {
        "actor": f"founder:{founder_id}", "action": "register_attachment",
        "idempotency_key": doc_id, "target": f"artifacts/{doc_id}",
        "result": "success", "detail": f"scope={scope}; source_type={source_type}",
        "created_at": now,
    }
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _register(txn):
        existing = await ingestion_ref.get(transaction=txn)
        if existing.exists:
            row = existing.to_dict()
            identity = ("founder_id", "session_id", "scope", "source_type", "source_ref",
                        "source_grant_id", "sha256", "occurrence_key")
            if any(row.get(key) != ingestion_row.get(key) for key in identity):
                raise ValueError("ingestion occurrence conflicts with existing receipt")
            return doc_id
        txn.set(artifact_ref, artifact_row)
        txn.set(ingestion_ref, ingestion_row)
        txn.set(audit_ref, audit_row)
        return doc_id

    return await _register(transaction)


async def get_artifact(artifact_id: str) -> Optional[dict[str, Any]]:
    doc = await get_client().collection("artifacts").document(artifact_id).get()
    return doc.to_dict() | {"id": doc.id} if doc.exists else None


async def get_artifact_by_storage_name(
        founder_id: str, storage_name: str) -> Optional[dict[str, Any]]:
    """Authorize a blob through its workspace-scoped artifact metadata."""
    query = (get_client().collection("artifacts")
             .where("founder_id", "==", founder_id)
             .where("storage_name", "==", storage_name).limit(1))
    async for doc in query.stream():
        return doc.to_dict() | {"id": doc.id}
    return None


async def delete_session_artifact_records(founder_id: str, session_id: str,
                                          artifact_id: str) -> bool:
    """Delete artifact metadata/chunks and its paired ingestion if owned.

    Blob deletion is deliberately handled by ``session_deletion`` before this
    metadata boundary so a missing blob cannot be hidden behind a deleted row.
    """
    client = get_client()
    artifact_ref = client.collection("artifacts").document(artifact_id)
    snap = await artifact_ref.get()
    if not snap.exists:
        return False
    row = snap.to_dict() or {}
    if (
        row.get("founder_id") != founder_id
        or row.get("session_id") != session_id
        or row.get("retention_policy") != "session"
    ):
        return False
    await client.recursive_delete(artifact_ref)
    ingestion_ref = client.collection("ingestions").document(artifact_id)
    ingestion = await ingestion_ref.get()
    if ingestion.exists:
        ingestion_row = ingestion.to_dict() or {}
        if (
            ingestion_row.get("founder_id") == founder_id
            and ingestion_row.get("session_id") == session_id
        ):
            await client.recursive_delete(ingestion_ref)
    return True


async def update_artifact(artifact_id: str, **fields: Any) -> None:
    await get_client().collection("artifacts").document(artifact_id).update(
        {**fields, "updated_at": _now()})


async def replace_artifact_chunks(artifact_id: str, chunks: list[dict]) -> None:
    """Replace one derived index using an atomic generation pointer.

    Chunk writes can span Firestore's batch limit. Readers continue using the
    previous complete generation until every new chunk exists; only then does
    one artifact update publish the new generation. Stale chunks are cleanup,
    never part of the correctness boundary.
    """
    collection = (get_client().collection("artifacts").document(artifact_id)
                  .collection("chunks"))
    existing = [doc async for doc in collection.stream()]
    generation_material = "|".join(
        f"{int(chunk.get('ordinal', 0))}:{chunk.get('content_sha256', '')}"
        for chunk in chunks)
    generation = hashlib.sha256(generation_material.encode()).hexdigest()[:20]
    writes: list[tuple[Any, dict]] = []
    new_ids: set[str] = set()
    for chunk in chunks:
        digest = hashlib.sha256(
            f"{chunk.get('ordinal', 0)}:{chunk.get('content_sha256', '')}".encode()
        ).hexdigest()[:16]
        chunk_id = f"chunk_{int(chunk.get('ordinal', 0)):04d}_{digest}"
        chunk["id"] = chunk_id
        new_ids.add(chunk_id)
        writes.append((collection.document(chunk_id), {
            **chunk, "artifact_id": artifact_id, "generation": generation,
            "created_at": _now(),
        }))
    for start in range(0, len(writes), 450):
        batch = get_client().batch()
        for ref, payload in writes[start:start + 450]:
            batch.set(ref, payload)
        await batch.commit()
    await update_artifact(artifact_id, index_generation=generation)
    stale = [doc.reference for doc in existing if doc.id not in new_ids]
    for start in range(0, len(stale), 450):
        batch = get_client().batch()
        for ref in stale[start:start + 450]:
            batch.delete(ref)
        await batch.commit()


async def list_artifact_chunks(artifact_id: str, limit: int = 400) -> list[dict[str, Any]]:
    artifact = await get_artifact(artifact_id)
    generation = (artifact or {}).get("index_generation", "")
    collection = (get_client().collection("artifacts").document(artifact_id)
                  .collection("chunks"))
    rows = [doc.to_dict() | {"id": doc.id} async for doc in collection.stream()]
    if generation:
        rows = [row for row in rows if row.get("generation") == generation]
    rows.sort(key=lambda row: int(row.get("ordinal") or 0))
    return rows[:max(1, min(limit, 400))]


async def replace_image_observations(
        artifact_id: str, observations: list[dict[str, Any]]) -> None:
    """Replace one bounded image-observation generation before publishing it."""
    if len(observations) > 64:
        raise ValueError("too many image observations")
    collection = (get_client().collection("artifacts").document(artifact_id)
                  .collection("image_observations"))
    existing = [doc async for doc in collection.stream()]
    generation_material = "|".join(
        f"{row.get('id')}:{row.get('evidence_sha256')}" for row in observations)
    generation = hashlib.sha256(generation_material.encode()).hexdigest()[:20]
    batch = get_client().batch()
    new_ids: set[str] = set()
    for row in observations:
        observation_id = str(row.get("id") or "")
        if not re.fullmatch(r"[a-f0-9]{32}", observation_id):
            raise ValueError("invalid image observation id")
        new_ids.add(observation_id)
        batch.set(collection.document(observation_id), {
            **row, "artifact_id": artifact_id, "generation": generation,
            "status": "READY", "created_at": _now(),
        })
    await batch.commit()
    await update_artifact(artifact_id, observation_generation=generation)
    stale = [doc.reference for doc in existing if doc.id not in new_ids]
    if stale:
        cleanup = get_client().batch()
        for ref in stale:
            cleanup.delete(ref)
        await cleanup.commit()


async def list_image_observations(
        artifact_id: str, limit: int = 64) -> list[dict[str, Any]]:
    """Read only the published bounded observation generation."""
    artifact = await get_artifact(artifact_id)
    generation = str((artifact or {}).get("observation_generation") or "")
    collection = (get_client().collection("artifacts").document(artifact_id)
                  .collection("image_observations"))
    rows = [doc.to_dict() | {"id": doc.id} async for doc in collection.stream()]
    if generation:
        rows = [row for row in rows if row.get("generation") == generation]
    rows = [row for row in rows if row.get("status") == "READY"]
    rows.sort(key=lambda row: int(row.get("ordinal") or 0))
    return rows[:max(1, min(limit, 64))]


async def claim_ingestion(ingestion_id: str, *, lease_owner: str = "",
                          lease_seconds: int = 900) -> dict[str, Any]:
    """Lease one ingestion attempt; terminal work is an idempotent duplicate."""
    import time as _time

    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("ingestions").document(ingestion_id)
    artifact_ref = get_client().collection("artifacts").document(ingestion_id)
    transaction = get_client().transaction()
    owner = lease_owner or uuid.uuid4().hex
    terminal = {"READY", "NEEDS_FOUNDER", "CONFIRMED", "NO_TEXT", "UNSUPPORTED", "FAILED"}

    @gc_firestore.async_transactional
    async def _claim(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists:
            return {"status": "error", "error": True, "message": "ingestion not found"}
        current = snap.to_dict()
        if current.get("status") in terminal:
            return {"status": "success", "duplicate": True,
                    "ingestion_status": current.get("status")}
        started = float(current.get("lease_started_epoch") or 0)
        lease = float(current.get("lease_seconds") or lease_seconds)
        if current.get("lease_owner") and _time.time() - started <= lease:
            return {"status": "success", "in_progress": True,
                    "ingestion_status": current.get("status")}
        fields = {
            "status": "VALIDATING", "lease_owner": owner,
            "lease_started_epoch": _time.time(), "lease_seconds": lease_seconds,
            "attempt": int(current.get("attempt") or 0) + 1,
            "updated_at": _now(), "error_code": None, "message": None,
        }
        txn.update(ref, fields)
        txn.update(artifact_ref, {"status": "VALIDATING", "updated_at": _now()})
        return {"status": "success", "claimed": True, "lease_owner": owner}

    return await _claim(transaction)


async def set_ingestion_stage(ingestion_id: str, lease_owner: str, status: str) -> bool:
    """Advance a processing stage only while this worker owns the lease."""
    import time as _time

    from google.cloud import firestore as gc_firestore
    ref = get_client().collection("ingestions").document(ingestion_id)
    artifact_ref = get_client().collection("artifacts").document(ingestion_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _set(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists or snap.to_dict().get("lease_owner") != lease_owner:
            return False
        txn.update(ref, {"status": status, "lease_started_epoch": _time.time(),
                         "updated_at": _now()})
        txn.update(artifact_ref, {"status": status, "updated_at": _now()})
        return True
    return await _set(transaction)


async def update_ingestion_leased(ingestion_id: str, lease_owner: str,
                                  **fields: Any) -> bool:
    """Update worker-owned ingestion data without accepting a stale writer."""
    from google.cloud import firestore as gc_firestore
    ref = get_client().collection("ingestions").document(ingestion_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _update(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists or snap.to_dict().get("lease_owner") != lease_owner:
            return False
        txn.update(ref, {**fields, "updated_at": _now()})
        return True

    return await _update(transaction)


async def finish_ingestion(ingestion_id: str, lease_owner: str, status: str,
                           **fields: Any) -> bool:
    """Commit a terminal ingestion result only for the current lease owner."""
    from google.cloud import firestore as gc_firestore
    ref = get_client().collection("ingestions").document(ingestion_id)
    artifact_ref = get_client().collection("artifacts").document(ingestion_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _finish(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists or snap.to_dict().get("lease_owner") != lease_owner:
            return False
        now = _now()
        audit_ref = get_client().collection("audit").document(_new_id())
        clean = {key: value for key, value in fields.items() if value is not None}
        txn.update(ref, {
            **clean, "status": status, "lease_owner": None,
            "finished_at": now, "updated_at": now,
            **({"confirmed_at": now} if status == "CONFIRMED" else {}),
        })
        txn.update(artifact_ref, {
            "status": status, "updated_at": now,
            **{key: value for key, value in clean.items()
               if key in {"chunk_count", "error_code", "message"}},
        })
        txn.set(audit_ref, {
            "actor": "agent:document_ingestion", "action": "ingest_document",
            "idempotency_key": ingestion_id,
            "target": f"artifacts/{ingestion_id}",
            "result": ("success" if status in {"READY", "NEEDS_FOUNDER", "CONFIRMED"}
                       else "error"),
            "detail": f"terminal_status={status}", "created_at": now,
        })
        return True
    return await _finish(transaction)


async def retry_ingestion(ingestion_id: str, lease_owner: str, *,
                          error_code: str, message: str,
                          max_attempts: int = 3) -> dict[str, Any]:
    """Release a transient failure for redelivery, or terminalize at the cap."""
    from google.cloud import firestore as gc_firestore
    ref = get_client().collection("ingestions").document(ingestion_id)
    artifact_ref = get_client().collection("artifacts").document(ingestion_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _retry(txn):
        snap = await ref.get(transaction=txn)
        if not snap.exists or snap.to_dict().get("lease_owner") != lease_owner:
            return {"status": "error", "error": True,
                    "message": "ingestion lease changed"}
        attempt = int(snap.to_dict().get("attempt") or 1)
        terminal = attempt >= max_attempts
        status = "FAILED" if terminal else "QUEUED"
        now = _now()
        audit_ref = get_client().collection("audit").document(_new_id())
        fields = {
            "status": status, "lease_owner": None, "lease_started_epoch": None,
            "error_code": error_code, "message": message, "updated_at": now,
            **({"finished_at": now} if terminal else {}),
        }
        txn.update(ref, fields)
        txn.update(artifact_ref, {
            "status": status, "error_code": error_code,
            "message": message, "updated_at": now,
        })
        txn.set(audit_ref, {
            "actor": "agent:document_ingestion", "action": "ingest_document_attempt",
            "idempotency_key": f"{ingestion_id}:{attempt}",
            "target": f"artifacts/{ingestion_id}",
            "result": "error" if terminal else "retrying",
            "detail": f"attempt={attempt}; error_code={error_code}"[:500],
            "created_at": now,
        })
        return {"status": "success", "retryable": not terminal,
                "ingestion_status": status, "attempt": attempt}

    return await _retry(transaction)

async def create_ingestion(founder_id: str, source_type: str, source_ref: str,
                           artifact: str, proposed_updates: list[dict]) -> str:
    doc_id = _new_id()
    await get_client().collection("ingestions").document(doc_id).set(
        {
            "workspace_id": founder_id,
            "founder_id": founder_id,
            "ingestion_domain": "FOUNDER_SOURCE",
            "tenancy_schema_version": 1,
            "source_type": source_type,
            "source_ref": source_ref,
            "artifact": artifact,
            "status": "EXTRACTED",
            "proposed_updates": proposed_updates,
            "confirmed_at": None,
            "created_at": _now(),
        }
    )
    return doc_id


async def get_ingestion(ingestion_id: str) -> Optional[dict[str, Any]]:
    doc = await get_client().collection("ingestions").document(ingestion_id).get()
    return doc.to_dict() | {"id": doc.id} if doc.exists else None


async def update_ingestion(ingestion_id: str, **fields: Any) -> None:
    await get_client().collection("ingestions").document(ingestion_id).update(fields)


# ---------------------------------------------------------------------------
# audit (append-only, docs/02 + 12)
# ---------------------------------------------------------------------------

async def audit(
    actor: str,
    action: str,
    target: str,
    result: str,
    detail: str = "",
    idempotency_key: Optional[str] = None,
) -> str:
    """Append an audit row. Never updates; no PII beyond refs; no secrets."""
    doc_id = _new_id()
    await get_client().collection("audit").document(doc_id).set(
        {
            "actor": actor,
            "action": action,
            "idempotency_key": idempotency_key,
            "target": target,
            "result": result,
            "detail": detail[:500],
            "created_at": _now(),
        }
    )
    return doc_id


# ---------------------------------------------------------------------------
# integrations state (Drive file selection, Gmail label + scan idempotency)
# ---------------------------------------------------------------------------

async def get_integrations(founder_id: str) -> dict[str, Any]:
    doc = await get_client().collection("integrations").document(founder_id).get()
    if doc.exists:
        return doc.to_dict()
    return {"drive_files": [], "gmail_label": "grants"}


async def update_integrations(founder_id: str, **fields: Any) -> None:
    await get_client().collection("integrations").document(founder_id).set(fields, merge=True)


def _connector_state_doc(kind: str, founder_id: str = "") -> str:
    if not founder_id:
        return kind
    digest = hashlib.sha256(founder_id.encode()).hexdigest()[:40]
    return f"{kind}_{digest}"


async def get_processed_gmail_ids(founder_id: str = "") -> list[str]:
    doc = await get_client().collection("gmail_state").document(
        _connector_state_doc("processed", founder_id)).get()
    return doc.to_dict().get("ids", []) if doc.exists else []


async def _append_processed_ids(
        collection: str, ids: list[str], founder_id: str = "") -> None:
    """Atomic, bounded append. A transaction (not a bare read-modify-write) so
    concurrent scans cannot drop each other's ids and re-emit duplicate
    follow-ups; the last-2000 bound keeps rescans idempotent yet finite."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection(collection).document(
        _connector_state_doc("processed", founder_id))
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _append(txn) -> None:
        snap = await ref.get(transaction=txn)
        existing = (snap.to_dict() or {}).get("ids", []) if snap.exists else []
        seen = set(existing)
        merged = existing + [i for i in ids if i not in seen]
        txn.set(ref, {"ids": merged[-2000:]})

    await _append(transaction)


async def add_processed_gmail_ids(ids: list[str], founder_id: str = "") -> None:
    await _append_processed_ids("gmail_state", ids, founder_id)


async def set_last_gmail_scan(
        summary: dict[str, Any], founder_id: str = "") -> None:
    await get_client().collection("gmail_state").document(
        _connector_state_doc("last_scan", founder_id)).set(
        {**summary, "workspace_id": founder_id or None, "at": _now()})


async def get_last_gmail_scan(founder_id: str = "") -> Optional[dict[str, Any]]:
    doc = await get_client().collection("gmail_state").document(
        _connector_state_doc("last_scan", founder_id)).get()
    return doc.to_dict() if doc.exists else None


# alex_mail_state (adr/001 v2): same pattern as gmail_state, separate collection
# — Alex's mailbox is a different account with its own idempotency store.

async def get_processed_alex_ids(founder_id: str = "") -> list[str]:
    doc = await get_client().collection("alex_mail_state").document(
        _connector_state_doc("processed", founder_id)).get()
    return doc.to_dict().get("ids", []) if doc.exists else []


async def add_processed_alex_ids(ids: list[str], founder_id: str = "") -> None:
    await _append_processed_ids("alex_mail_state", ids, founder_id)


async def get_alex_history_id(founder_id: str = "") -> Optional[str]:
    doc = await get_client().collection("alex_mail_state").document(
        _connector_state_doc("watch", founder_id)).get()
    return doc.to_dict().get("history_id") if doc.exists else None


async def set_alex_history_id(history_id: str, founder_id: str = "") -> None:
    await get_client().collection("alex_mail_state").document(
        _connector_state_doc("watch", founder_id)).set(
        {"history_id": history_id, "workspace_id": founder_id or None,
         "at": _now()})


async def set_last_alex_scan(
        summary: dict[str, Any], founder_id: str = "") -> None:
    await get_client().collection("alex_mail_state").document(
        _connector_state_doc("last_scan", founder_id)).set(
        {**summary, "workspace_id": founder_id or None, "at": _now()})


async def get_last_alex_scan(founder_id: str = "") -> Optional[dict[str, Any]]:
    doc = await get_client().collection("alex_mail_state").document(
        _connector_state_doc("last_scan", founder_id)).get()
    return doc.to_dict() if doc.exists else None


async def get_legacy_data_source_snapshot(founder_id: str) -> dict[str, Any]:
    """Bounded M1 migration input; values are counts/ids, never credentials."""
    integrations_doc = await get_client().collection("integrations").document(
        founder_id).get()
    integrations = integrations_doc.to_dict() if integrations_doc.exists else {}
    gmail_ids = await get_processed_gmail_ids()
    alex_ids = await get_processed_alex_ids()
    missing_owner = missing_session = 0
    for collection_name in ("ingestions", "applications", "portal_registrations"):
        async for doc in get_client().collection(collection_name).stream():
            row = doc.to_dict() or {}
            if not row.get("founder_id"):
                missing_owner += 1
            if collection_name != "applications" and not row.get("session_id"):
                missing_session += 1
    return {
        "integrations_exists": integrations_doc.exists,
        "drive_files": list(integrations.get("drive_files") or [])[:500],
        "gmail_label_configured": bool(integrations.get("gmail_label")),
        "processed_gmail_count": len(gmail_ids),
        "processed_alex_count": len(alex_ids),
        "missing_owner_count": missing_owner,
        "missing_session_count": missing_session,
    }


# Pending portal verification routing is non-secret and must survive Cloud Run
# scale-to-zero. Passwords remain exclusively in Secret Manager.
async def save_pending_portal_registration(host: str, founder_id: str,
                                           session_id: str, portal_url: str,
                                           email: str,
                                           application_id: str = "") -> None:
    doc_id = hashlib.sha256(host.lower().encode()).hexdigest()[:24]
    await get_client().collection("portal_registrations").document(doc_id).set({
        "host": host, "founder_id": founder_id, "session_id": session_id,
        "application_id": application_id,
        "portal_url": portal_url, "email": email, "status": "PENDING",
        "updated_at": _now(),
    })


async def list_pending_portal_registrations(founder_id: str) -> list[dict[str, Any]]:
    query = (get_client().collection("portal_registrations")
             .where("founder_id", "==", founder_id)
             .where("status", "==", "PENDING"))
    return [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]


async def complete_portal_registration(host: str) -> None:
    doc_id = hashlib.sha256(host.lower().encode()).hexdigest()[:24]
    await get_client().collection("portal_registrations").document(doc_id).set(
        {"status": "COMPLETED", "updated_at": _now()}, merge=True)


async def get_source_hash(url: str) -> Optional[str]:
    doc = await get_client().collection("source_state").document(
        hashlib.sha256(url.encode()).hexdigest()[:20]).get()
    return doc.to_dict().get("content_hash") if doc.exists else None


async def set_source_hash(url: str, content_hash: str) -> None:
    await get_client().collection("source_state").document(
        hashlib.sha256(url.encode()).hexdigest()[:20]).set(
        {"url": url, "content_hash": content_hash, "checked_at": _now()})


# ---------------------------------------------------------------------------
# documents registry (docs/15 §provenance) — lineage for produced files
# ---------------------------------------------------------------------------

async def create_document_record(founder_id: str, artifact_name: str, kind: str,
                                 title: str, session_id: str, application_id: str,
                                 opportunity_name: str, spec_hash: str,
                                 version: int, doc_key: str) -> str:
    doc_id = _new_id()
    await get_client().collection("documents").document(doc_id).set({
        "id": doc_id, "founder_id": founder_id, "artifact_name": artifact_name,
        "kind": kind, "title": title, "session_id": session_id,
        "application_id": application_id, "opportunity_name": opportunity_name,
        "spec_hash": spec_hash, "version": version, "doc_key": doc_key,
        "created_at": _now(),
    })
    return doc_id


async def list_documents(founder_id: str, session_id: str | None = None,
                         application_id: str | None = None) -> list[dict[str, Any]]:
    query = get_client().collection("documents").where("founder_id", "==", founder_id)
    if session_id:
        query = query.where("session_id", "==", session_id)
    if application_id:
        query = query.where("application_id", "==", application_id)
    docs = [doc.to_dict() async for doc in query.stream()]
    return sorted(docs, key=lambda d: d.get("created_at", ""), reverse=True)


async def get_document_by_artifact(founder_id: str,
                                   artifact_name: str) -> Optional[dict[str, Any]]:
    """Return the produced-document registry row authorizing one export."""
    query = (get_client().collection("documents")
             .where("founder_id", "==", founder_id)
             .where("artifact_name", "==", artifact_name)
             .limit(1))
    async for doc in query.stream():
        return doc.to_dict() | {"id": doc.id}
    return None


async def get_document(document_id: str) -> Optional[dict[str, Any]]:
    """Return one produced-document row by its opaque canonical id."""
    doc = await get_client().collection("documents").document(document_id).get()
    return doc.to_dict() | {"id": doc.id} if doc.exists else None


async def delete_session_document_record(founder_id: str, session_id: str,
                                         document_id: str) -> bool:
    """Delete an exact document row only when the named session owns it."""
    ref = get_client().collection("documents").document(document_id)
    snap = await ref.get()
    if not snap.exists:
        return False
    row = snap.to_dict() or {}
    if row.get("founder_id") != founder_id or row.get("session_id") != session_id:
        return False
    await ref.delete()
    return True


async def next_document_version(founder_id: str, doc_key: str) -> int:
    """Monotonic per-(founder, doc_key) version from a transactional counter.

    The old client-side count over the whole documents collection duplicated
    versions under concurrency and read O(all documents); a counter doc bumped
    inside a transaction is race-free and O(1)."""
    from google.cloud import firestore as gc_firestore

    counter_id = hashlib.sha256(f"{founder_id}:{doc_key}".encode()).hexdigest()[:24]
    ref = get_client().collection("document_versions").document(counter_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _next(txn) -> int:
        snap = await ref.get(transaction=txn)
        current = int((snap.to_dict() or {}).get("version", 0)) if snap.exists else 0
        nxt = current + 1
        txn.set(ref, {"founder_id": founder_id, "doc_key": doc_key,
                      "version": nxt, "updated_at": _now()})
        return nxt

    return await _next(transaction)


# ---------------------------------------------------------------------------
# session-resource projections (docs/23): resource_index,
# session_resource_links, session_catalog
# ---------------------------------------------------------------------------

async def upsert_resource_and_link(resource: dict[str, Any],
                                   link: dict[str, Any]) -> dict[str, Any]:
    """Transactionally create/update one resource_index row and create its
    immutable session link if absent (docs/23 §6 step 5).

    Replay semantics: an existing link — tombstoned or live — is final. The
    call reports a replay and writes nothing, so retries and repair reruns
    can never duplicate or resurrect an occurrence. Catalog counters are
    deliberately NOT part of this transaction (hot-document contention).
    """
    from google.cloud import firestore as gc_firestore

    resource_ref = get_client().collection("resource_index").document(
        resource["resource_id"])
    link_ref = get_client().collection("session_resource_links").document(
        link["link_id"])
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _commit(txn):
        link_snap = await link_ref.get(transaction=txn)
        if link_snap.exists:
            existing = link_snap.to_dict() or {}
            return {"resource_created": False, "link_created": False,
                    "replayed": True,
                    "tombstoned": bool(existing.get("deleted_at"))}
        resource_snap = await resource_ref.get(transaction=txn)
        now = _now()
        if resource_snap.exists:
            projection = {
                k: resource[k]
                for k in ("title", "summary", "status", "visibility",
                          "search_terms", "search_prefixes",
                          "representation_refs", "content_hash")
                if k in resource
            }
            projection["updated_at"] = now
            txn.update(resource_ref, projection)
            created = False
        else:
            txn.set(resource_ref, {**resource,
                                   "created_at": now, "updated_at": now})
            created = True
        txn.set(link_ref, {**link, "occurred_at": link.get("occurred_at") or now,
                           "updated_at": now, "deleted_at": None})
        return {"resource_created": created, "link_created": True,
                "replayed": False, "tombstoned": False}

    return await _commit(transaction)


async def upsert_unlinked_resource(resource: dict[str, Any]) -> dict[str, Any]:
    """Create an evidence-supported legacy resource without inventing origin.

    Migration may know that a canonical founder-owned work product exists but
    have no exact session evidence.  Such a resource remains globally
    findable with an unavailable-origin tombstone; timestamp proximity is
    never used to fabricate a ``session_resource_links`` row (docs/23 WI-5).
    Existing projections are left semantically intact so a backfill rerun
    cannot downgrade a resource that a live producer has already linked.
    """
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("resource_index").document(
        resource["resource_id"])
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _commit(txn):
        snapshot = await ref.get(transaction=txn)
        if snapshot.exists:
            return {"resource_created": False, "replayed": True}
        now = _now()
        txn.set(ref, {**resource, "created_at": now, "updated_at": now})
        return {"resource_created": True, "replayed": False}

    return await _commit(transaction)


async def update_resource_projection(resource_id: str,
                                     fields: dict[str, Any]) -> bool:
    """Update the CURRENT projection of an existing resource (title/status/
    search data) without inventing a new occurrence — the matchmaker path."""
    ref = get_client().collection("resource_index").document(resource_id)
    snap = await ref.get()
    if not snap.exists:
        return False
    allowed = {k: v for k, v in fields.items()
               if k in ("title", "summary", "status", "visibility",
                        "search_terms", "search_prefixes",
                        "representation_refs", "content_hash")}
    allowed["updated_at"] = _now()
    await ref.update(allowed)
    return True


async def get_resource(resource_id: str) -> Optional[dict[str, Any]]:
    if not resource_id:
        return None
    snap = await get_client().collection("resource_index").document(
        resource_id).get()
    return (snap.to_dict() | {"id": snap.id}) if snap.exists else None


async def resource_has_other_session_link(founder_id: str, resource_id: str,
                                          *, excluding_session_id: str) -> bool:
    """Return true when a live occurrence belongs to another session."""
    query = (get_client().collection("session_resource_links")
             .where("founder_id", "==", founder_id)
             .where("resource_id", "==", resource_id))
    async for doc in query.stream():
        row = doc.to_dict() or {}
        if not row.get("deleted_at") and row.get("session_id") != excluding_session_id:
            return True
    return False


async def delete_resource_projection(founder_id: str, resource_id: str) -> bool:
    """Delete one founder-owned index projection after its canonical file."""
    ref = get_client().collection("resource_index").document(resource_id)
    snap = await ref.get()
    if not snap.exists or (snap.to_dict() or {}).get("founder_id") != founder_id:
        return False
    await ref.delete()
    return True


async def tombstone_session_links(founder_id: str, session_id: str) -> int:
    """Tombstone-in-place every link of one session (docs/23 §5.2). Returns
    the number newly tombstoned. Rows are never physically deleted here."""
    query = (get_client().collection("session_resource_links")
             .where("founder_id", "==", founder_id)
             .where("session_id", "==", session_id))
    count = 0
    async for doc in query.stream():
        row = doc.to_dict() or {}
        if not row.get("deleted_at"):
            await doc.reference.update({"deleted_at": _now(),
                                        "updated_at": _now()})
            count += 1
    return count


async def tombstone_session_resource_links(
        founder_id: str, session_id: str, resource_id: str) -> int:
    """Tombstone this session's occurrences of one explicitly deleted resource."""
    if not founder_id or not session_id or not resource_id:
        return 0
    query = (get_client().collection("session_resource_links")
             .where("founder_id", "==", founder_id)
             .where("session_id", "==", session_id)
             .where("resource_id", "==", resource_id))
    count = 0
    async for doc in query.stream():
        row = doc.to_dict() or {}
        if not row.get("deleted_at"):
            await doc.reference.update({"deleted_at": _now(),
                                        "updated_at": _now()})
            count += 1
    return count


def now_iso() -> str:
    """Public UTC timestamp helper for cross-store lifecycle services."""
    return _now()


async def upsert_session_catalog(session_id: str,
                                 fields: dict[str, Any]) -> None:
    """Create/update one catalog row. created_at is immutable; merge keeps
    fields the caller did not supply."""
    ref = get_client().collection("session_catalog").document(session_id)
    snap = await ref.get()
    now = _now()
    if snap.exists:
        await ref.update({**fields, "updated_at": now})
    else:
        await ref.set({"schema_version": 1, "session_id": session_id,
                       "status": "active", "message_count": 0,
                       "resource_count": 0, "resource_types": [],
                       "search_terms": [], "search_prefixes": [],
                       **fields, "created_at": now, "updated_at": now})


async def get_session_catalog(session_id: str) -> Optional[dict[str, Any]]:
    snap = await get_client().collection("session_catalog").document(
        session_id).get()
    return snap.to_dict() if snap.exists else None


async def bump_session_catalog_resources(session_id: str, resource_type: str,
                                         count_delta: int = 1) -> None:
    """Best-effort advisory counter update AFTER a link commit — never part
    of the link transaction (docs/23 §6 step 5); repaired from links."""
    ref = get_client().collection("session_catalog").document(session_id)
    snap = await ref.get()
    if not snap.exists:
        return
    row = snap.to_dict() or {}
    types = list(row.get("resource_types") or [])
    if resource_type not in types and len(types) < 16:
        types.append(resource_type)
    await ref.update({
        "resource_count": int(row.get("resource_count") or 0) + count_delta,
        "resource_types": types, "updated_at": _now()})


def _keyset_after(rows: list[dict[str, Any]], order_field: str, id_field: str,
                  start_after: Optional[tuple[str, str]]) -> list[dict[str, Any]]:
    """Python-side keyset continuation for DESC (order_field, id) scans."""
    if not start_after:
        return rows
    after_key = (start_after[0], start_after[1])
    return [r for r in rows
            if (r.get(order_field, ""), r.get(id_field, "")) < after_key]


async def search_session_links(founder_id: str, prefix: str, *,
                               resource_type: Optional[str] = None,
                               session_id: Optional[str] = None,
                               limit: int = 50,
                               start_after: Optional[tuple[str, str]] = None
                               ) -> list[dict[str, Any]]:
    """Indexed candidate scan over link occurrences, newest first by the
    immutable (occurred_at, link_id) keyset. Excludes tombstones in code."""
    query = (get_client().collection("session_resource_links")
             .where("founder_id", "==", founder_id)
             .where("search_prefixes", "array_contains", prefix))
    if resource_type:
        query = query.where("resource_type", "==", resource_type)
    if session_id:
        query = query.where("session_id", "==", session_id)
    query = query.order_by("occurred_at", direction="DESCENDING")
    if start_after:
        query = query.start_after({"occurred_at": start_after[0]})
    rows = [doc.to_dict() | {"id": doc.id}
            async for doc in query.limit(max(1, limit * 2)).stream()]
    rows = [r for r in rows if not r.get("deleted_at")]
    rows.sort(key=lambda r: (r.get("occurred_at", ""), r.get("link_id", "")),
              reverse=True)
    return _keyset_after(rows, "occurred_at", "link_id", start_after)[:limit]


async def list_session_links(founder_id: str, session_id: str, *,
                             limit: int = 100,
                             start_after: Optional[tuple[str, str]] = None
                             ) -> list[dict[str, Any]]:
    query = (get_client().collection("session_resource_links")
             .where("founder_id", "==", founder_id)
             .where("session_id", "==", session_id)
             .order_by("occurred_at", direction="DESCENDING"))
    rows = [doc.to_dict() | {"id": doc.id}
            async for doc in query.limit(max(1, limit * 2)).stream()]
    rows = [r for r in rows if not r.get("deleted_at")]
    rows.sort(key=lambda r: (r.get("occurred_at", ""), r.get("link_id", "")),
              reverse=True)
    return _keyset_after(rows, "occurred_at", "link_id", start_after)[:limit]


async def list_resource_links(founder_id: str, resource_id: str, *,
                              limit: int = 50) -> list[dict[str, Any]]:
    query = (get_client().collection("session_resource_links")
             .where("founder_id", "==", founder_id)
             .where("resource_id", "==", resource_id)
             .order_by("occurred_at", direction="DESCENDING"))
    rows = [doc.to_dict() | {"id": doc.id}
            async for doc in query.limit(limit).stream()]
    return [r for r in rows if not r.get("deleted_at")]


async def search_resources(founder_id: str, prefix: str, *,
                           visibility: str = "primary",
                           resource_type: Optional[str] = None,
                           limit: int = 50,
                           start_after: Optional[tuple[str, str]] = None
                           ) -> list[dict[str, Any]]:
    """Indexed candidate scan over resource rows by the immutable
    (created_at, resource_id) keyset (docs/23 §5.6)."""
    query = (get_client().collection("resource_index")
             .where("founder_id", "==", founder_id)
             .where("visibility", "==", visibility)
             .where("search_prefixes", "array_contains", prefix))
    if resource_type:
        query = query.where("resource_type", "==", resource_type)
    query = query.order_by("created_at", direction="DESCENDING")
    if start_after:
        query = query.start_after({"created_at": start_after[0]})
    rows = [doc.to_dict() | {"id": doc.id}
            async for doc in query.limit(max(1, limit * 2)).stream()]
    rows.sort(key=lambda r: (r.get("created_at", ""), r.get("resource_id", "")),
              reverse=True)
    return _keyset_after(rows, "created_at", "resource_id", start_after)[:limit]


async def search_session_catalog(founder_id: str, prefix: str, *,
                                 limit: int = 50,
                                 start_after: Optional[tuple[str, str]] = None
                                 ) -> list[dict[str, Any]]:
    query = (get_client().collection("session_catalog")
             .where("founder_id", "==", founder_id)
             .where("search_prefixes", "array_contains", prefix)
             .order_by("created_at", direction="DESCENDING"))
    if start_after:
        query = query.start_after({"created_at": start_after[0]})
    rows = [doc.to_dict() | {"id": doc.id}
            async for doc in query.limit(max(1, limit * 2)).stream()]
    rows = [r for r in rows if r.get("status", "active") == "active"]
    rows.sort(key=lambda r: (r.get("created_at", ""), r.get("session_id", "")),
              reverse=True)
    return _keyset_after(rows, "created_at", "session_id", start_after)[:limit]


async def list_recent_session_catalog(founder_id: str, *,
                                      limit: int = 30) -> list[dict[str, Any]]:
    """Recency listing for the blank state; single page, updated_at DESC."""
    query = (get_client().collection("session_catalog")
             .where("founder_id", "==", founder_id)
             .where("status", "==", "active")
             .order_by("updated_at", direction="DESCENDING").limit(limit))
    return [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]


async def list_recent_resources(founder_id: str, *,
                                visibility: str = "primary",
                                limit: int = 30) -> list[dict[str, Any]]:
    query = (get_client().collection("resource_index")
             .where("founder_id", "==", founder_id)
             .where("visibility", "==", visibility)
             .order_by("updated_at", direction="DESCENDING").limit(limit))
    return [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]


async def create_voice_note_artifact(founder_id: str, session_id: str,
                                     storage_name: str, *,
                                     content_type: str = "audio/webm",
                                     size_bytes: int = 0,
                                     transcript_preview: str = "") -> str:
    """Register a founder voice note as a first-class artifact record.

    Voice notes previously wrote bytes with no durable metadata at all, so
    they could not be authorized, retained, or found from their conversation
    (docs/23 §6.1). They are terminal on arrival — there is no extraction
    pipeline — so no paired ingestion row is created.
    """
    doc_id = _new_id()
    now = _now()
    await get_client().collection("artifacts").document(doc_id).set({
        "founder_id": founder_id,
        "session_id": session_id,
        "scope": "reference_only",
        "source_type": "voice_note",
        "source_ref": "Voice note",
        "storage_name": storage_name,
        "artifact": storage_name,
        "declared_content_type": content_type,
        "detected_content_type": content_type,
        "detected_extension": ".webm",
        "size_bytes": int(size_bytes or 0),
        "sha256": "",
        "status": "READY",
        "transcript_preview": transcript_preview[:240],
        "retention_policy": "session",
        "created_at": now,
        "updated_at": now,
    })
    return doc_id


async def list_active_discovery_requests(founder_id: str, *,
                                         limit: int = 10) -> list[dict[str, Any]]:
    """Receipts still in flight for one founder (docs/24 §5.2).

    Single-field filter plus a client-side status cut, matching the existing
    no-composite-index policy for this collection.
    """
    query = (get_client().collection("discovery_requests")
             .where("founder_id", "==", founder_id))
    rows = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]
    rows = [r for r in rows
            if str(r.get("status") or "") in ("ACCEPTED", "RUNNING")]
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------------------
# Data-source reliability records (docs/24 §6)
# ---------------------------------------------------------------------------

def _contract_error(message: str, code: str = "invalid_contract") -> dict[str, Any]:
    return {"status": "error", "error": True,
            "error_code": code, "message": message}


def _workspace_owner_matches(row: dict[str, Any], workspace_id: str) -> bool:
    """Require both the migrated tenant boundary and compatibility owner."""
    return bool(workspace_id and row.get("workspace_id") == workspace_id
                and row.get("founder_id") == workspace_id)


def _lease_is_active(row: dict[str, Any]) -> bool:
    owner = str(row.get("lease_owner") or "")
    started = str(row.get("lease_started_at") or "")
    if not owner or not started:
        return False
    try:
        stamp = datetime.fromisoformat(started.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        return age <= max(1, int(row.get("lease_seconds") or 1))
    except (TypeError, ValueError):
        return False


async def upsert_data_connection(
        founder_id: str, connector_id: str, *, account_ref: str = "default",
        account_hint: str = "", roles: list[str] | None = None,
        auth_kind: str = "google_oauth", credential_ref: str | None = None,
        granted_scopes: list[str] | None = None,
        provider_account_hash: str = "", status: str = "CONNECTED",
        expected_version: int | None = None) -> dict[str, Any]:
    """Create/update one owner-scoped connection with optimistic versioning."""
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    try:
        connector = dsc.require_closed(connector_id, dsc.ConnectorId)
        auth = dsc.require_closed(auth_kind, dsc.ConnectionAuthKind)
        state = dsc.require_closed(status, dsc.ConnectionStatus)
        contract = dsc.CONNECTOR_REGISTRY[connector]
        enabled_roles = list(roles or (role.value for role in contract.roles))
        closed_roles = [dsc.require_closed(role, dsc.DataSourceRole)
                        for role in enabled_roles]
        if not set(closed_roles).issubset(contract.roles):
            return _contract_error("connector role is not enabled")
        connection_id = dsc.data_connection_id(
            founder_id, connector.value, account_ref)
    except ValueError:
        return _contract_error("unknown connection contract")

    ref = get_client().collection("data_connections").document(connection_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _write(txn):
        snapshot = await ref.get(transaction=txn)
        current = snapshot.to_dict() if snapshot.exists else None
        version = int((current or {}).get("version") or 0)
        if expected_version is not None and version != expected_version:
            return _contract_error("connection changed concurrently",
                                   "version_conflict")
        now = _now()
        row = {
            "schema_version": 1, "connection_id": connection_id,
            "workspace_id": founder_id, "founder_id": founder_id,
            "connector_id": connector.value,
            "account_ref": str(account_ref)[:256],
            "account_hint": str(account_hint)[:120],
            "roles": sorted(role.value for role in closed_roles),
            "auth_kind": auth.value,
            "credential_ref": str(credential_ref)[:256] if credential_ref else None,
            "provider_account_hash": (str(provider_account_hash)[:96]
                                      or (current or {}).get(
                                          "provider_account_hash")),
            "granted_scopes": sorted({str(scope)[:256]
                                      for scope in (granted_scopes or []) if scope}),
            "status": state.value,
            "last_verified_at": (current or {}).get("last_verified_at"),
            "last_success_at": (current or {}).get("last_success_at"),
            "last_error_code": (current or {}).get("last_error_code"),
            "last_error_at": (current or {}).get("last_error_at"),
            "disconnected_at": (now if state == dsc.ConnectionStatus.DISCONNECTED
                                else (current or {}).get("disconnected_at")),
            "version": version + 1,
            "created_at": (current or {}).get("created_at") or now,
            "updated_at": now,
        }
        txn.set(ref, row)
        return {"status": "success", "created": current is None, **row}

    return await _write(transaction)


async def get_data_connection(founder_id: str,
                              connection_id: str) -> Optional[dict[str, Any]]:
    snapshot = await get_client().collection("data_connections").document(
        connection_id).get()
    if not snapshot.exists:
        return None
    row = snapshot.to_dict() | {"id": snapshot.id}
    return row if (row.get("workspace_id") == founder_id
                   and row.get("founder_id") == founder_id) else None


async def list_data_connections(founder_id: str) -> list[dict[str, Any]]:
    query = get_client().collection("data_connections").where(
        "workspace_id", "==", founder_id)
    rows = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]
    return sorted(rows, key=lambda row: (row.get("connector_id", ""),
                                         row.get("account_ref", "")))


async def find_data_connections_by_provider(
        connector_id: str, provider_account_hash: str) -> list[dict[str, Any]]:
    """Resolve provider ingress to an exact active workspace connection.

    This is deliberately a provider-correlation query, not a user read. It
    returns only closed connector metadata and never scans user content.
    """
    if not connector_id or not provider_account_hash:
        return []
    query = get_client().collection("data_connections").where(
        "connector_id", "==", connector_id)
    rows = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]
    return [row for row in rows
            if row.get("provider_account_hash") == provider_account_hash
            and row.get("status") in {"CONNECTED", "DEGRADED"}]


async def transition_data_connection(
        founder_id: str, connection_id: str, *, status: str | None = None,
        expected_version: int | None = None, verified: bool = False,
        successful_operation: str | None = None,
        error_code: str | None = None,
        disconnect_outcome: str | None = None) -> dict[str, Any]:
    """Transactionally advance one connection's durable health projection.

    ``expected_version`` is required by user-triggered lifecycle changes and is
    also available to provider operations so a stale failure cannot overwrite a
    later reconnect.  Provider text is never persisted; ``error_code`` is a
    closed safe code.
    """
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    try:
        closed_status = (dsc.require_closed(status, dsc.ConnectionStatus)
                         if status else None)
        closed_error = (dsc.require_closed(error_code, dsc.SafeErrorCode)
                        if error_code else None)
    except ValueError:
        return _contract_error("invalid connection transition")
    ref = get_client().collection("data_connections").document(connection_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _write(txn):
        snapshot = await ref.get(transaction=txn)
        if not snapshot.exists:
            return _contract_error("connection not found", "owner_mismatch")
        row = snapshot.to_dict()
        if row.get("founder_id") != founder_id:
            return _contract_error("connection not found", "owner_mismatch")
        version = int(row.get("version") or 0)
        if expected_version is not None and version != expected_version:
            return _contract_error("connection changed concurrently",
                                   "version_conflict")
        if row.get("status") == dsc.ConnectionStatus.DISCONNECTED.value \
                and closed_status not in {dsc.ConnectionStatus.CONNECTED,
                                          dsc.ConnectionStatus.DISCONNECTING}:
            return _contract_error("connection is disconnected", "auth_required")
        now = _now()
        updates: dict[str, Any] = {
            "version": version + 1, "updated_at": now,
        }
        if closed_status:
            updates["status"] = closed_status.value
            if closed_status == dsc.ConnectionStatus.DISCONNECTED:
                updates["disconnected_at"] = now
            elif closed_status == dsc.ConnectionStatus.CONNECTED:
                updates["disconnected_at"] = None
        if verified:
            updates["last_verified_at"] = now
        if successful_operation:
            updates.update({
                "last_success_at": now,
                "last_successful_operation": str(successful_operation)[:80],
                "last_error_code": None,
                "last_error_at": None,
            })
        if closed_error:
            updates.update({"last_error_code": closed_error.value,
                            "last_error_at": now})
        if disconnect_outcome:
            updates["disconnect_outcome"] = str(disconnect_outcome)[:80]
        txn.update(ref, updates)
        return {"status": "success", **row, **updates,
                "connection_id": connection_id}

    return await _write(transaction)


async def revoke_connection_source_grants(founder_id: str,
                                           connection_id: str) -> dict[str, Any]:
    """Revoke every ACTIVE source grant for one owner connection."""
    from services import data_source_contracts as dsc

    connection = await get_data_connection(founder_id, connection_id)
    if not connection:
        return _contract_error("connection not found", "owner_mismatch")
    grants = await list_source_grants(founder_id, connection_id=connection_id)
    revoked = 0
    for grant in grants:
        if grant.get("status") != dsc.SourceGrantStatus.ACTIVE.value:
            continue
        result = await revoke_source_grant(
            founder_id, grant.get("source_grant_id") or grant.get("id"))
        if result.get("status") == "success":
            revoked += 1
    return {"status": "success", "revoked_count": revoked}


async def create_source_grant(
        founder_id: str, connection_id: str, provider_source_id: str, *,
        display_name: str, allowed_ingestion_scopes: list[str],
        selected_session_id: str | None = None,
        provider_content_type: str | None = None,
        provider_version: str | None = None,
        provider_modified_at: str | None = None) -> dict[str, Any]:
    """Create/reactivate one Drive source grant; provider ids are never inferred."""
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    connection = await get_data_connection(founder_id, connection_id)
    if (not connection
            or connection.get("connector_id") != dsc.ConnectorId.DRIVE.value
            or connection.get("status") not in {
                dsc.ConnectionStatus.CONNECTED.value,
                dsc.ConnectionStatus.DEGRADED.value}):
        return _contract_error("source connection not found", "source_not_selected")
    try:
        scopes = sorted({dsc.require_closed(scope, dsc.IngestionScope).value
                         for scope in allowed_ingestion_scopes})
        if not scopes:
            raise ValueError("empty scope")
        grant_id = dsc.source_grant_id(
            founder_id, connection_id, provider_source_id)
    except ValueError:
        return _contract_error("invalid source grant")
    ref = get_client().collection("source_grants").document(grant_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _write(txn):
        snapshot = await ref.get(transaction=txn)
        previous = snapshot.to_dict() if snapshot.exists else {}
        now = _now()
        row = {
            "schema_version": 1, "source_grant_id": grant_id,
            "workspace_id": founder_id, "founder_id": founder_id,
            "connection_id": connection_id,
            "connector_id": dsc.ConnectorId.DRIVE.value,
            "provider_source_id": str(provider_source_id)[:512],
            "display_name": str(display_name)[:240], "source_kind": "file",
            "allowed_ingestion_scopes": scopes,
            "selected_session_id": selected_session_id,
            "provider_content_type": (str(provider_content_type)[:256]
                                      if provider_content_type else None),
            "provider_version": (str(provider_version)[:256]
                                 if provider_version else None),
            "provider_modified_at": provider_modified_at,
            "status": dsc.SourceGrantStatus.ACTIVE.value,
            "selected_by": f"founder:{founder_id}",
            "selected_at": now, "revoked_at": None, "updated_at": now,
        }
        txn.set(ref, row)
        return {"status": "success", "created": not bool(previous), **row}

    return await _write(transaction)


async def get_source_grant(founder_id: str,
                           source_grant_id: str) -> Optional[dict[str, Any]]:
    snapshot = await get_client().collection("source_grants").document(
        source_grant_id).get()
    if not snapshot.exists:
        return None
    row = snapshot.to_dict() | {"id": snapshot.id}
    return row if _workspace_owner_matches(row, founder_id) else None


async def list_source_grants(founder_id: str, *,
                             connection_id: str | None = None) -> list[dict[str, Any]]:
    query = get_client().collection("source_grants").where(
        "workspace_id", "==", founder_id)
    rows = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]
    if connection_id:
        rows = [row for row in rows if row.get("connection_id") == connection_id]
    return sorted(rows, key=lambda row: row.get("selected_at", ""), reverse=True)


async def revoke_source_grant(founder_id: str,
                              source_grant_id: str) -> dict[str, Any]:
    from services import data_source_contracts as dsc

    row = await get_source_grant(founder_id, source_grant_id)
    if not row:
        return _contract_error("source grant not found", "source_not_selected")
    if row.get("status") == dsc.SourceGrantStatus.REVOKED.value:
        return {"status": "success", "duplicate": True,
                "source_grant_id": source_grant_id}
    now = _now()
    await get_client().collection("source_grants").document(source_grant_id).update({
        "status": dsc.SourceGrantStatus.REVOKED.value,
        "revoked_at": now, "updated_at": now,
    })
    return {"status": "success", "duplicate": False,
            "source_grant_id": source_grant_id}


async def mark_source_grant_missing(founder_id: str,
                                    source_grant_id: str) -> dict[str, Any]:
    """Project a provider-deleted/unreadable source without erasing history."""
    from services import data_source_contracts as dsc

    row = await get_source_grant(founder_id, source_grant_id)
    if not row:
        return _contract_error("source grant not found", "source_not_selected")
    now = _now()
    await get_client().collection("source_grants").document(source_grant_id).update({
        "status": dsc.SourceGrantStatus.SOURCE_MISSING.value,
        "updated_at": now,
    })
    return {"status": "success", "source_grant_id": source_grant_id,
            "grant_status": dsc.SourceGrantStatus.SOURCE_MISSING.value}


async def create_external_event(
        founder_id: str, connection_id: str, connector_id: str,
        provider_event_id: str, event_kind: str, *, payload_hash: str,
        provider_thread_id: str | None = None,
        source_ref: dict[str, Any] | None = None,
        safe_display: dict[str, Any] | None = None,
        content_risk: str = "CLEAR", occurred_at: str | None = None,
        delivery_status: str = "PENDING") -> dict[str, Any]:
    """Insert one immutable provider receipt; duplicates return the first row."""
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    connection = await get_data_connection(founder_id, connection_id)
    if not connection or connection.get("connector_id") != connector_id:
        return _contract_error("event connection not found", "owner_mismatch")
    if not dsc.validate_connector_role(connector_id, dsc.DataSourceRole.EVENT.value):
        return _contract_error("connector cannot emit events")
    try:
        kind = dsc.require_closed(event_kind, dsc.ExternalEventKind)
        risk = dsc.require_closed(content_risk, dsc.ContentRisk)
        delivery = dsc.require_closed(delivery_status, dsc.DeliveryStatus)
        event_id = dsc.external_event_id(
            founder_id, connection_id, provider_event_id)
        if len(payload_hash) != 64:
            raise ValueError("invalid hash")
    except ValueError:
        return _contract_error("invalid external event")
    safe = {str(key)[:64]: str(value)[:280]
            for key, value in (safe_display or {}).items()}
    refs = {str(key)[:64]: str(value)[:512]
            for key, value in (source_ref or {}).items()}
    ref = get_client().collection("external_events").document(event_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _insert(txn):
        snapshot = await ref.get(transaction=txn)
        if snapshot.exists:
            return {"status": "success", "duplicate": True,
                    **snapshot.to_dict()}
        now = _now()
        row = {
            "schema_version": 1, "event_id": event_id,
            "workspace_id": founder_id, "event_domain": "CONNECTOR_EVENT",
            "founder_id": founder_id, "connection_id": connection_id,
            "connector_id": connector_id,
            "provider_event_id": str(provider_event_id)[:512],
            "provider_thread_id": (str(provider_thread_id)[:512]
                                   if provider_thread_id else None),
            "event_kind": kind.value, "payload_hash": payload_hash,
            "source_ref": refs, "safe_display": safe,
            "content_risk": risk.value,
            "verification_status": "VERIFIED",
            "verified_at": now,
            "business_disposition": "RECEIVED",
            "processing_status": dsc.EventProcessingStatus.RECEIVED.value,
            "lease_owner": None, "lease_started_at": None, "lease_seconds": 0,
            "correlation_status": dsc.CorrelationStatus.PENDING.value,
            "application_id": None, "session_id": None, "resource_id": None,
            "correlation_basis": None, "delivery_status": delivery.value,
            "effect_ref": None, "attempt_count": 0,
            "received_at": now, "occurred_at": occurred_at or now,
            "updated_at": now,
        }
        txn.create(ref, row)
        return {"status": "success", "duplicate": False, **row}

    return await _insert(transaction)


async def get_external_event(founder_id: str,
                             event_id: str) -> Optional[dict[str, Any]]:
    snapshot = await get_client().collection("external_events").document(
        event_id).get()
    if not snapshot.exists:
        return None
    row = snapshot.to_dict() | {"id": snapshot.id}
    return row if _workspace_owner_matches(row, founder_id) else None


async def list_external_events_by_thread(
        founder_id: str, provider_thread_id: str, *,
        limit: int = 20) -> list[dict[str, Any]]:
    """Owner-scoped thread evidence; only previously exact rows may correlate."""
    query = (get_client().collection("external_events")
             .where("workspace_id", "==", founder_id)
             .where("provider_thread_id", "==", provider_thread_id))
    rows = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]
    rows.sort(key=lambda row: row.get("received_at", ""), reverse=True)
    return rows[:limit]


async def claim_external_event(founder_id: str, event_id: str, *,
                               lease_seconds: int = 120,
                               lease_owner: str = "") -> dict[str, Any]:
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    ref = get_client().collection("external_events").document(event_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _claim(txn):
        snapshot = await ref.get(transaction=txn)
        if not snapshot.exists or snapshot.to_dict().get("founder_id") != founder_id:
            return _contract_error("external event not found", "owner_mismatch")
        row = snapshot.to_dict()
        if row.get("processing_status") in {
                dsc.EventProcessingStatus.APPLIED.value,
                dsc.EventProcessingStatus.INBOXED.value}:
            return {"status": "success", "duplicate": True, **row}
        if _lease_is_active(row):
            return {"status": "success", "in_progress": True,
                    "event_id": event_id}
        owner = lease_owner or uuid.uuid4().hex
        fields = {
            "processing_status": dsc.EventProcessingStatus.APPLYING.value,
            "business_disposition": "APPLYING",
            "lease_owner": owner, "lease_started_at": _now(),
            "lease_seconds": max(1, min(int(lease_seconds), 900)),
            "attempt_count": min(int(row.get("attempt_count") or 0) + 1, 100),
            "updated_at": _now(),
        }
        txn.update(ref, fields)
        return {"status": "success", "claimed": True,
                "lease_owner": owner, "event_id": event_id}

    return await _claim(transaction)


async def create_founder_inbox_item(
        founder_id: str, event_id: str, item_kind: str, *, title: str,
        summary: str, candidate_refs: list[dict[str, str]] | None = None,
        lease_owner: str = "") -> dict[str, Any]:
    """Atomically inbox an ambiguous/unmatched event under its active lease."""
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    try:
        kind = dsc.require_closed(item_kind, dsc.FounderInboxKind)
        inbox_id = dsc.founder_inbox_id(founder_id, event_id, kind.value)
    except ValueError:
        return _contract_error("invalid inbox item")
    candidates = []
    for candidate in (candidate_refs or [])[:5]:
        candidates.append({
            "resource_id": str(candidate.get("resource_id") or "")[:128],
            "application_id": str(candidate.get("application_id") or "")[:128],
            "reason_code": str(candidate.get("reason_code") or "")[:64],
        })
    event_ref = get_client().collection("external_events").document(event_id)
    inbox_ref = get_client().collection("founder_inbox").document(inbox_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _write(txn):
        event_snapshot = await event_ref.get(transaction=txn)
        if (not event_snapshot.exists
                or event_snapshot.to_dict().get("founder_id") != founder_id):
            return _contract_error("external event not found", "owner_mismatch")
        event = event_snapshot.to_dict()
        if lease_owner and event.get("lease_owner") != lease_owner:
            return _contract_error("external event lease changed", "lease_conflict")
        inbox_snapshot = await inbox_ref.get(transaction=txn)
        now = _now()
        if inbox_snapshot.exists:
            row = inbox_snapshot.to_dict()
            duplicate = True
        else:
            row = {
                "schema_version": 1, "inbox_item_id": inbox_id,
                "workspace_id": founder_id, "inbox_domain": "EXTERNAL_EVENT",
                "founder_id": founder_id, "event_id": event_id,
                "item_kind": kind.value,
                "status": dsc.FounderInboxStatus.UNREAD.value,
                "title": str(title)[:160], "summary": str(summary)[:500],
                "candidate_refs": candidates,
                "resolved_resource_id": None, "resolved_session_id": None,
                "resolution": None, "created_at": now,
                "updated_at": now, "resolved_at": None,
            }
            txn.create(inbox_ref, row)
            duplicate = False
        txn.update(event_ref, {
            "processing_status": dsc.EventProcessingStatus.INBOXED.value,
            "business_disposition": "INBOXED",
            "correlation_status": (dsc.CorrelationStatus.AMBIGUOUS.value
                                   if kind == dsc.FounderInboxKind.AMBIGUOUS_EVENT
                                   else dsc.CorrelationStatus.UNMATCHED.value),
            "delivery_status": dsc.DeliveryStatus.NOT_REQUIRED.value,
            "lease_owner": None, "lease_started_at": None,
            "updated_at": now,
        })
        return {"status": "success", "duplicate": duplicate, **row}

    return await _write(transaction)


async def apply_external_event_to_application(
        founder_id: str, event_id: str, lease_owner: str,
        application_id: str, session_id: str, correlation_basis: str,
        followup: dict[str, Any]) -> dict[str, Any]:
    """Atomically commit exact correlation, follow-up, and terminal receipt."""
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    try:
        basis = dsc.require_closed(correlation_basis, dsc.CorrelationBasis)
    except ValueError:
        return _contract_error("invalid correlation basis")
    event_ref = get_client().collection("external_events").document(event_id)
    app_ref = get_client().collection("applications").document(application_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _apply(txn):
        event_snapshot = await event_ref.get(transaction=txn)
        app_snapshot = await app_ref.get(transaction=txn)
        if (not event_snapshot.exists or not app_snapshot.exists
                or event_snapshot.to_dict().get("founder_id") != founder_id
                or app_snapshot.to_dict().get("founder_id") != founder_id):
            return _contract_error("event or application not found", "owner_mismatch")
        event = event_snapshot.to_dict()
        if event.get("processing_status") == dsc.EventProcessingStatus.APPLIED.value:
            return {"status": "success", "duplicate": True,
                    "effect_ref": event.get("effect_ref")}
        if event.get("lease_owner") != lease_owner:
            return _contract_error("external event lease changed", "lease_conflict")
        application = app_snapshot.to_dict()
        followups = list(application.get("followups") or [])
        dedupe_key = event_id
        duplicate = any(
            str(item.get("external_event_id") or "") == dedupe_key
            for item in followups if isinstance(item, dict))
        if not duplicate:
            followups = [*followups[-199:], {**followup,
                                             "external_event_id": dedupe_key}]
            txn.update(app_ref, {"followups": followups, "updated_at": _now()})
        now = _now()
        effect_ref = f"applications/{application_id}/followups/{event_id}"
        txn.update(event_ref, {
            "processing_status": dsc.EventProcessingStatus.APPLIED.value,
            "business_disposition": "APPLIED",
            "correlation_status": dsc.CorrelationStatus.EXACT.value,
            "application_id": application_id, "session_id": session_id,
            "resource_id": application_id, "correlation_basis": basis.value,
            "effect_ref": effect_ref, "lease_owner": None,
            "lease_started_at": None, "updated_at": now,
        })
        return {"status": "success", "duplicate": duplicate,
                "effect_ref": effect_ref, "session_id": session_id,
                "application_id": application_id}

    return await _apply(transaction)


async def apply_external_event_signal(
        founder_id: str, event_id: str, lease_owner: str, *,
        session_id: str, correlation_basis: str,
        resource_id: str | None = None) -> dict[str, Any]:
    """Commit an exact causal signal that has no application follow-up."""
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    try:
        basis = dsc.require_closed(correlation_basis, dsc.CorrelationBasis)
    except ValueError:
        return _contract_error("invalid correlation basis")
    ref = get_client().collection("external_events").document(event_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _apply(txn):
        snapshot = await ref.get(transaction=txn)
        if not snapshot.exists or snapshot.to_dict().get("founder_id") != founder_id:
            return _contract_error("external event not found", "owner_mismatch")
        row = snapshot.to_dict()
        if row.get("processing_status") == dsc.EventProcessingStatus.APPLIED.value:
            return {"status": "success", "duplicate": True,
                    "effect_ref": row.get("effect_ref")}
        if row.get("lease_owner") != lease_owner:
            return _contract_error("external event lease changed", "lease_conflict")
        effect_ref = f"signals/{event_id}"
        txn.update(ref, {
            "processing_status": dsc.EventProcessingStatus.APPLIED.value,
            "business_disposition": "APPLIED",
            "correlation_status": dsc.CorrelationStatus.EXACT.value,
            "session_id": session_id, "resource_id": resource_id,
            "correlation_basis": basis.value, "effect_ref": effect_ref,
            "lease_owner": None, "lease_started_at": None,
            "updated_at": _now(),
        })
        return {"status": "success", "duplicate": False,
                "effect_ref": effect_ref, "session_id": session_id}

    return await _apply(transaction)


async def claim_external_event_delivery(founder_id: str,
                                        event_id: str,
                                        lease_seconds: int = 120) -> dict[str, Any]:
    """Lease one founder wake; an expired ENQUEUED delivery is reclaimable."""
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    ref = get_client().collection("external_events").document(event_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _claim(txn):
        snapshot = await ref.get(transaction=txn)
        if not snapshot.exists or snapshot.to_dict().get("founder_id") != founder_id:
            return _contract_error("external event not found", "owner_mismatch")
        row = snapshot.to_dict()
        if row.get("delivery_status") in {
                dsc.DeliveryStatus.DELIVERED.value,
                dsc.DeliveryStatus.NOT_REQUIRED.value}:
            return {"status": "success", "duplicate": True,
                    "delivery_status": row.get("delivery_status")}
        if (row.get("delivery_status") == dsc.DeliveryStatus.ENQUEUED.value
                and _lease_is_active(row)):
            return {"status": "success", "duplicate": True,
                    "delivery_status": row.get("delivery_status")}
        now = _now()
        owner = uuid.uuid4().hex
        txn.update(ref, {
            "delivery_status": dsc.DeliveryStatus.ENQUEUED.value,
            "lease_owner": owner, "lease_started_at": now,
            "lease_seconds": max(1, min(lease_seconds, 900)),
            "delivery_attempt": int(row.get("delivery_attempt") or 0) + 1,
            "updated_at": now,
        })
        return {"status": "success", "claimed": True,
                "lease_owner": owner}

    return await _claim(transaction)


async def finish_external_event_delivery(founder_id: str, event_id: str,
                                         lease_owner: str, *,
                                         delivered: bool) -> dict[str, Any]:
    """Fence a wake receipt to the worker that owns its current lease."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("external_events").document(event_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _finish(txn):
        snapshot = await ref.get(transaction=txn)
        if (not snapshot.exists
                or snapshot.to_dict().get("founder_id") != founder_id):
            return _contract_error("external event not found", "owner_mismatch")
        row = snapshot.to_dict()
        if row.get("delivery_status") == "DELIVERED":
            return {"status": "success", "duplicate": True,
                    "delivery_status": "DELIVERED"}
        if (not lease_owner or row.get("lease_owner") != lease_owner
                or row.get("delivery_status") != "ENQUEUED"):
            return _contract_error("wake delivery lease changed",
                                   "lease_conflict")
        value = "DELIVERED" if delivered else "FAILED"
        txn.update(ref, {
            "delivery_status": value, "lease_owner": None,
            "lease_started_at": None, "updated_at": _now(),
        })
        return {"status": "success", "delivery_status": value}

    return await _finish(transaction)


async def receive_portal_event(founder_id: str, application_id: str,
                               event_kind: str, confirmation_id: str,
                               session_id: str = "") -> dict[str, Any]:
    """Durably receive one verified portal event before changing its domain."""
    from google.cloud import firestore as gc_firestore

    if (event_kind not in {"submission_confirmed", "result_posted"}
            or not founder_id or not application_id or not confirmation_id
            or len(confirmation_id) > 256):
        return _contract_error("invalid portal event contract")
    payload_hash = hashlib.sha256(
        f"{event_kind}\x1f{application_id}\x1f{confirmation_id}\x1f{session_id}".encode()
    ).hexdigest()
    receipt_id = "portal_" + hashlib.sha256(
        f"{founder_id}\x1f{event_kind}\x1f{application_id}\x1f{confirmation_id}".encode()
    ).hexdigest()[:32]
    ref = get_client().collection("portal_event_receipts").document(receipt_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _receive(txn):
        snapshot = await ref.get(transaction=txn)
        if snapshot.exists:
            row = snapshot.to_dict()
            if (row.get("founder_id") != founder_id
                    or row.get("payload_hash") != payload_hash):
                return _contract_error("portal event identity changed",
                                       "version_conflict")
            return {"status": "success", "duplicate": True, **row}
        now = _now()
        row = {
            "schema_version": 1, "receipt_id": receipt_id,
            "workspace_id": founder_id, "event_domain": "PORTAL_EVENT",
            "founder_id": founder_id, "application_id": application_id,
            "session_id": session_id, "event_kind": event_kind,
            "confirmation_id": confirmation_id, "payload_hash": payload_hash,
            "status": "RECEIVED", "attempt": 0, "lease_owner": None,
            "lease_started_at": None, "lease_seconds": 30,
            "wake_delivery_id": None, "created_at": now, "updated_at": now,
        }
        txn.create(ref, row)
        return {"status": "success", "duplicate": False, **row}

    return await _receive(transaction)


async def claim_portal_event(founder_id: str, receipt_id: str,
                             lease_seconds: int = 30) -> dict[str, Any]:
    """Lease a non-terminal portal receipt so concurrent deliveries collapse."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("portal_event_receipts").document(receipt_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _claim(txn):
        snapshot = await ref.get(transaction=txn)
        if (not snapshot.exists
                or snapshot.to_dict().get("founder_id") != founder_id):
            return _contract_error("portal event not found", "owner_mismatch")
        row = snapshot.to_dict()
        if row.get("status") == "APPLIED":
            return {"status": "success", "duplicate": True, **row}
        if row.get("status") == "APPLYING" and _lease_is_active(row):
            return {"status": "success", "in_progress": True,
                    "receipt_id": receipt_id}
        owner = uuid.uuid4().hex
        now = _now()
        txn.update(ref, {
            "status": "APPLYING", "lease_owner": owner,
            "lease_started_at": now,
            "lease_seconds": max(1, min(lease_seconds, 300)),
            "attempt": int(row.get("attempt") or 0) + 1, "updated_at": now,
        })
        return {"status": "success", "claimed": True,
                "lease_owner": owner, "receipt_id": receipt_id}

    return await _claim(transaction)


async def finish_portal_event(founder_id: str, receipt_id: str,
                              lease_owner: str, *, notice: str = "",
                              state_delta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Atomically terminalize a portal receipt and create its optional wake."""
    from google.cloud import firestore as gc_firestore

    receipt_ref = get_client().collection(
        "portal_event_receipts").document(receipt_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _finish(txn):
        snapshot = await receipt_ref.get(transaction=txn)
        if (not snapshot.exists
                or snapshot.to_dict().get("founder_id") != founder_id):
            return _contract_error("portal event not found", "owner_mismatch")
        row = snapshot.to_dict()
        if row.get("status") == "APPLIED":
            return {"status": "success", "duplicate": True, **row}
        if row.get("status") != "APPLYING" or row.get("lease_owner") != lease_owner:
            return _contract_error("portal event lease changed", "lease_conflict")
        wake_id = None
        session_id = str(row.get("session_id") or "")
        if session_id:
            wake_id = "wake_" + hashlib.sha256(
                f"portal_event\x1f{receipt_id}\x1f{session_id}".encode()
            ).hexdigest()[:32]
            wake_ref = get_client().collection("wake_deliveries").document(wake_id)
            wake_snapshot = await wake_ref.get(transaction=txn)
            if not wake_snapshot.exists:
                now = _now()
                txn.create(wake_ref, {
                    "schema_version": 1, "delivery_id": wake_id,
                    "workspace_id": founder_id,
                    "delivery_domain": "FOUNDER_WAKE",
                    "founder_id": founder_id, "session_id": session_id,
                    "source_kind": "portal_event", "source_id": receipt_id,
                    "notice": notice[:300],
                    "state_delta": dict(state_delta or {}),
                    "status": "PENDING", "attempt": 0,
                    "lease_owner": None, "lease_started_at": None,
                    "lease_seconds": 120, "last_error_code": None,
                    "created_at": now, "updated_at": now,
                    "delivered_at": None,
                })
        now = _now()
        txn.update(receipt_ref, {
            "status": "APPLIED", "wake_delivery_id": wake_id,
            "lease_owner": None, "lease_started_at": None,
            "applied_at": now, "updated_at": now,
        })
        return {"status": "success", "receipt_status": "APPLIED",
                "receipt_id": receipt_id, "wake_delivery_id": wake_id}

    return await _finish(transaction)


async def create_wake_delivery(founder_id: str, session_id: str,
                               source_kind: str, source_id: str,
                               notice: str,
                               state_delta: dict[str, Any]) -> dict[str, Any]:
    """Create a content-bounded durable wake receipt for a committed event."""
    from google.cloud import firestore as gc_firestore

    if (source_kind not in {"approval", "feedback", "deadline",
                           "discovery", "portal_event", "external_event",
                           "system_notice"}
            or not founder_id
            or not session_id or not source_id or len(notice) > 300):
        return _contract_error("invalid wake delivery contract")
    delivery_id = "wake_" + hashlib.sha256(
        f"{source_kind}\x1f{source_id}\x1f{session_id}".encode()).hexdigest()[:32]
    ref = get_client().collection("wake_deliveries").document(delivery_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _create(txn):
        snapshot = await ref.get(transaction=txn)
        if snapshot.exists:
            row = snapshot.to_dict()
            if (row.get("founder_id") != founder_id
                    or row.get("session_id") != session_id
                    or row.get("source_kind") != source_kind
                    or row.get("source_id") != source_id):
                return _contract_error("wake delivery identity changed",
                                       "version_conflict")
            return {"status": "success", "duplicate": True,
                    "delivery_id": delivery_id,
                    "delivery_status": row.get("status")}
        now = _now()
        txn.create(ref, {
            "schema_version": 1, "delivery_id": delivery_id,
            "workspace_id": founder_id, "delivery_domain": "FOUNDER_WAKE",
            "founder_id": founder_id, "session_id": session_id,
            "source_kind": source_kind, "source_id": source_id,
            "notice": notice, "state_delta": dict(state_delta),
            "status": "PENDING", "attempt": 0,
            "max_attempts": 8, "next_attempt_at": None,
            "lease_owner": None, "lease_started_at": None,
            "lease_seconds": 120, "last_error_code": None,
            "created_at": now, "updated_at": now, "delivered_at": None,
        })
        return {"status": "success", "duplicate": False,
                "delivery_id": delivery_id, "delivery_status": "PENDING"}

    return await _create(transaction)


async def get_wake_delivery(founder_id: str,
                            delivery_id: str) -> Optional[dict[str, Any]]:
    snapshot = await get_client().collection("wake_deliveries").document(
        delivery_id).get()
    if not snapshot.exists:
        return None
    row = snapshot.to_dict() | {"id": snapshot.id}
    return row if (row.get("workspace_id") == founder_id
                   and row.get("founder_id") == founder_id) else None


async def claim_wake_delivery(founder_id: str, delivery_id: str,
                              lease_seconds: int = 120) -> dict[str, Any]:
    """Lease a pending/failed/expired wake for one bounded delivery attempt."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("wake_deliveries").document(delivery_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _claim(txn):
        snapshot = await ref.get(transaction=txn)
        if (not snapshot.exists
                or snapshot.to_dict().get("founder_id") != founder_id):
            return _contract_error("wake delivery not found", "owner_mismatch")
        row = snapshot.to_dict()
        if row.get("status") == "DELIVERED":
            return {"status": "success", "duplicate": True,
                    "delivery_status": "DELIVERED"}
        if row.get("status") == "DEAD_LETTER":
            return {"status": "success", "duplicate": True,
                    "delivery_status": "DEAD_LETTER"}
        if row.get("status") == "ENQUEUED" and _lease_is_active(row):
            return {"status": "success", "duplicate": True,
                    "delivery_status": "ENQUEUED"}
        now = _now()
        owner = uuid.uuid4().hex
        txn.update(ref, {
            "status": "ENQUEUED", "lease_owner": owner,
            "lease_started_at": now,
            "lease_seconds": max(1, min(lease_seconds, 900)),
            "attempt": int(row.get("attempt") or 0) + 1,
            "updated_at": now,
        })
        return {"status": "success", "claimed": True,
                "lease_owner": owner, "delivery": row}

    return await _claim(transaction)


async def finish_wake_delivery(founder_id: str, delivery_id: str,
                               lease_owner: str, *, delivered: bool,
                               error_code: str | None = None) -> dict[str, Any]:
    """Commit a fenced wake result; FAILED remains durably retryable."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("wake_deliveries").document(delivery_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _finish(txn):
        snapshot = await ref.get(transaction=txn)
        if (not snapshot.exists
                or snapshot.to_dict().get("founder_id") != founder_id):
            return _contract_error("wake delivery not found", "owner_mismatch")
        row = snapshot.to_dict()
        if row.get("status") == "DELIVERED":
            return {"status": "success", "duplicate": True,
                    "delivery_status": "DELIVERED"}
        if (row.get("status") != "ENQUEUED"
                or row.get("lease_owner") != lease_owner):
            return _contract_error("wake delivery lease changed",
                                   "lease_conflict")
        now = _now()
        exhausted = (not delivered
                     and int(row.get("attempt") or 0)
                     >= int(row.get("max_attempts") or 8))
        value = ("DELIVERED" if delivered else
                 "DEAD_LETTER" if exhausted else "FAILED")
        txn.update(ref, {
            "status": value, "lease_owner": None, "lease_started_at": None,
            "last_error_code": None if delivered else (error_code or "dispatch_failed"),
            "updated_at": now, "delivered_at": now if delivered else None,
        })
        inbox_item_id = None
        if exhausted:
            inbox_item_id = "inbox_" + hashlib.sha256(
                f"wake-dead-letter\x1f{founder_id}\x1f{delivery_id}".encode()
            ).hexdigest()[:32]
            inbox_ref = get_client().collection("founder_inbox").document(
                inbox_item_id)
            txn.set(inbox_ref, {
                "schema_version": 1, "inbox_item_id": inbox_item_id,
                "workspace_id": founder_id, "inbox_domain": "DELIVERY_EXCEPTION",
                "founder_id": founder_id, "event_id": None,
                "delivery_id": delivery_id,
                "item_kind": "DELIVERY_DEAD_LETTER", "status": "UNREAD",
                "title": "A workflow update could not be delivered",
                "summary": "Retry the durable delivery after checking the session.",
                "candidate_refs": [], "resolved_resource_id": None,
                "resolved_session_id": row.get("session_id"),
                "resolution": None, "created_at": now, "updated_at": now,
                "resolved_at": None,
            })
        return {"status": "success", "delivery_status": value,
                "inbox_item_id": inbox_item_id}

    return await _finish(transaction)


async def requeue_wake_delivery(founder_id: str,
                                delivery_id: str) -> dict[str, Any]:
    """Explicitly drain one dead letter; never fabricates a new delivery."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection("wake_deliveries").document(delivery_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _requeue(txn):
        snapshot = await ref.get(transaction=txn)
        if (not snapshot.exists
                or snapshot.to_dict().get("workspace_id") != founder_id):
            return _contract_error("wake delivery not found", "owner_mismatch")
        row = snapshot.to_dict()
        if row.get("status") != "DEAD_LETTER":
            return _contract_error(
                "only a dead-letter delivery can be retried", "version_conflict")
        now = _now()
        txn.update(ref, {
            "status": "FAILED", "attempt": 0,
            "lease_owner": None, "lease_started_at": None,
            "next_attempt_at": now, "updated_at": now,
        })
        return {"status": "success", "delivery_id": delivery_id,
                "delivery_status": "FAILED"}

    return await _requeue(transaction)


async def get_founder_inbox_item(founder_id: str,
                                 inbox_item_id: str) -> Optional[dict[str, Any]]:
    snapshot = await get_client().collection("founder_inbox").document(
        inbox_item_id).get()
    if not snapshot.exists:
        return None
    row = snapshot.to_dict() | {"id": snapshot.id}
    return row if _workspace_owner_matches(row, founder_id) else None


async def list_founder_inbox(
        founder_id: str, *, status: str = "UNREAD", limit: int = 30,
        start_after: tuple[str, str] | None = None) -> list[dict[str, Any]]:
    from services import data_source_contracts as dsc

    try:
        state = dsc.require_closed(status, dsc.FounderInboxStatus)
    except ValueError:
        return []
    query = (get_client().collection("founder_inbox")
             .where("workspace_id", "==", founder_id)
             .where("status", "==", state.value)
             .order_by("created_at", direction="DESCENDING")
             .limit(max(1, min(limit, 100))))
    rows = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]
    rows.sort(key=lambda row: (row.get("created_at", ""),
                               row.get("inbox_item_id", "")), reverse=True)
    if start_after:
        rows = [row for row in rows
                if (row.get("created_at", ""), row.get("inbox_item_id", ""))
                < start_after]
    return rows[:limit]


async def resolve_founder_inbox_item(
        founder_id: str, inbox_item_id: str, *, application_id: str,
        resource_id: str, session_id: str,
        session_verified: bool = False) -> dict[str, Any]:
    """Resolve one item and apply its event effect exactly once in one txn."""
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    if not session_verified:
        return _contract_error("founder session not found", "owner_mismatch")
    inbox_ref = get_client().collection("founder_inbox").document(inbox_item_id)
    event_collection = get_client().collection("external_events")
    app_ref = get_client().collection("applications").document(application_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _resolve(txn):
        inbox_snapshot = await inbox_ref.get(transaction=txn)
        app_snapshot = await app_ref.get(transaction=txn)
        if (not inbox_snapshot.exists or not app_snapshot.exists
                or inbox_snapshot.to_dict().get("founder_id") != founder_id
                or app_snapshot.to_dict().get("founder_id") != founder_id):
            return _contract_error("inbox item not found", "owner_mismatch")
        inbox = inbox_snapshot.to_dict()
        if inbox.get("status") == dsc.FounderInboxStatus.RESOLVED.value:
            same = (inbox.get("resolved_resource_id") == resource_id
                    and inbox.get("resolved_session_id") == session_id)
            return ({"status": "success", "duplicate": True, **inbox}
                    if same else _contract_error(
                        "inbox item was already resolved", "version_conflict"))
        if inbox.get("status") != dsc.FounderInboxStatus.UNREAD.value:
            return _contract_error("inbox item is not resolvable", "version_conflict")
        candidates = inbox.get("candidate_refs") or []
        if candidates and not any(
                candidate.get("application_id") == application_id
                and candidate.get("resource_id") == resource_id
                for candidate in candidates):
            return _contract_error("resolution is not an authorized candidate",
                                   "owner_mismatch")
        event_ref = event_collection.document(str(inbox.get("event_id") or ""))
        event_snapshot = await event_ref.get(transaction=txn)
        if (not event_snapshot.exists
                or event_snapshot.to_dict().get("founder_id") != founder_id):
            return _contract_error("inbox item not found", "owner_mismatch")
        event = event_snapshot.to_dict()
        application = app_snapshot.to_dict()
        followups = list(application.get("followups") or [])
        event_id = str(event.get("event_id") or event_ref.id)
        duplicate = any(row.get("external_event_id") == event_id
                        for row in followups if isinstance(row, dict))
        if not duplicate:
            display = event.get("safe_display") or {}
            followups = [*followups[-199:], {
                "kind": f"email_{event.get('event_kind', 'update')}",
                "due_at": "", "status": "PENDING",
                "note": ("[provider message] " + str(display.get("title") or "")
                         + " — " + str(display.get("excerpt") or ""))[:500],
                "source": event.get("connector_id"),
                "external_event_id": event_id,
            }]
            txn.update(app_ref, {"followups": followups, "updated_at": _now()})
        now = _now()
        effect_ref = f"applications/{application_id}/followups/{event_id}"
        txn.update(event_ref, {
            "processing_status": dsc.EventProcessingStatus.APPLIED.value,
            "correlation_status": dsc.CorrelationStatus.EXACT.value,
            "correlation_basis": dsc.CorrelationBasis.FOUNDER_RESOLUTION.value,
            "application_id": application_id, "resource_id": resource_id,
            "session_id": session_id, "effect_ref": effect_ref,
            "delivery_status": dsc.DeliveryStatus.NOT_REQUIRED.value,
            "lease_owner": None, "lease_started_at": None, "updated_at": now,
        })
        fields = {
            "status": dsc.FounderInboxStatus.RESOLVED.value,
            "resolved_resource_id": resource_id,
            "resolved_session_id": session_id,
            "resolution": dsc.InboxResolution.LINKED_TO_APPLICATION.value,
            "updated_at": now, "resolved_at": now,
        }
        txn.update(inbox_ref, fields)
        return {"status": "success", "duplicate": duplicate,
                "inbox_item_id": inbox_item_id, "effect_ref": effect_ref,
                **fields}

    return await _resolve(transaction)


async def dismiss_founder_inbox_item(founder_id: str,
                                     inbox_item_id: str) -> dict[str, Any]:
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    ref = get_client().collection("founder_inbox").document(inbox_item_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _dismiss(txn):
        snapshot = await ref.get(transaction=txn)
        if not snapshot.exists or snapshot.to_dict().get("founder_id") != founder_id:
            return _contract_error("inbox item not found", "owner_mismatch")
        row = snapshot.to_dict()
        if row.get("status") == dsc.FounderInboxStatus.DISMISSED.value:
            return {"status": "success", "duplicate": True, **row}
        if row.get("status") != dsc.FounderInboxStatus.UNREAD.value:
            return _contract_error("inbox item is already resolved", "version_conflict")
        now = _now()
        fields = {
            "status": dsc.FounderInboxStatus.DISMISSED.value,
            "resolution": dsc.InboxResolution.DISMISSED_BY_FOUNDER.value,
            "updated_at": now, "resolved_at": now,
        }
        txn.update(ref, fields)
        return {"status": "success", "duplicate": False,
                "inbox_item_id": inbox_item_id, **fields}

    return await _dismiss(transaction)


async def prepare_external_action(
        founder_id: str, connection_id: str, action_kind: str,
        idempotency_key: str, request_hash: str, *, session_id: str | None = None,
        application_id: str | None = None, resource_id: str | None = None,
        subject_hash: str | None = None, approval_id: str | None = None,
        consume_approval: bool = False,
        approval_gate: str | None = None,
        approval_target: str | None = None,
        capability_id: str | None = None,
        capability_version: str | None = None,
        approval_bindings: dict[str, Any] | None = None,
        sandbox_context: dict[str, Any] | None = None,
        lease_seconds: int = 120) -> dict[str, Any]:
    """T1: claim exact approval and create PREPARED; never call a provider.

    When ``consume_approval`` is true, the exact GRANTED approval becomes
    CLAIMED in the same transaction that creates the PREPARED action.  T2
    (``start_external_action``) spends that claim immediately before provider
    execution.  A crash on either side is therefore distinguishable and
    recoverable without minting a second action or reusing an approval.
    """
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    connection = await get_data_connection(founder_id, connection_id)
    if not connection:
        return _contract_error("action connection not found", "owner_mismatch")
    try:
        kind = dsc.require_closed(action_kind, dsc.ExternalActionKind)
        action_id = dsc.external_action_id(founder_id, kind.value,
                                           idempotency_key)
        if len(request_hash) != 64:
            raise ValueError("invalid hash")
    except ValueError:
        return _contract_error("invalid external action")
    context = dict(sandbox_context or {})
    exact_bindings = dict(approval_bindings or {})
    # H4S effect kinds exist only inside a provisioned sandbox: the context is
    # mandatory for them and forbidden for every other kind, so neither an
    # omission nor a stray context can widen an action's authority.
    if kind.value in dsc.SANDBOX_ONLY_ACTION_KINDS:
        if not context:
            return _contract_error("sandbox action context required")
    elif context:
        return _contract_error("sandbox context is not valid for this action")
    if context:
        required_context = {"sandbox_run_id", "connector_binding_id",
                            "destination_ids", "fixture_id"}
        if (set(context) != required_context
                or not all(isinstance(context[key], str) and context[key]
                           for key in ("sandbox_run_id", "connector_binding_id", "fixture_id"))
                or not isinstance(context["destination_ids"], list)
                or not context["destination_ids"]
                or any(not isinstance(item, str) or not item
                       for item in context["destination_ids"])):
            return _contract_error("invalid sandbox action context")
    ref = get_client().collection("external_actions").document(action_id)
    approval_ref = (get_client().collection("approvals").document(approval_id)
                    if consume_approval and approval_id else None)
    if consume_approval and (approval_ref is None or not approval_gate
                             or not (approval_target or application_id)
                             or not subject_hash):
        return _contract_error("approved action binding is incomplete",
                               "approval_binding_missing")
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _prepare(txn):
        snapshot = await ref.get(transaction=txn)
        now = _now()
        if snapshot.exists:
            row = snapshot.to_dict()
            immutable = {
                "request_hash": request_hash,
                "founder_id": founder_id,
                "connection_id": connection_id,
                "session_id": session_id,
                "application_id": application_id,
                "resource_id": resource_id,
                "subject_hash": subject_hash,
            }
            if any(row.get(key) != value for key, value in immutable.items()):
                return _contract_error("idempotency key payload changed",
                                       "version_conflict")
            if row.get("status") in {
                    dsc.ExternalActionStatus.SUCCEEDED.value,
                    dsc.ExternalActionStatus.FAILED.value}:
                return {"status": "success", "duplicate": True, **row}
            if row.get("status") == dsc.ExternalActionStatus.UNCERTAIN.value:
                return _contract_error("action requires reconciliation",
                                       "reconciliation_required") | {
                                           "action_id": action_id}
            if row.get("status") == dsc.ExternalActionStatus.EXECUTING.value:
                if _lease_is_active(row):
                    return {"status": "success", "in_progress": True,
                            "action_id": action_id}
                fields = {
                    "status": dsc.ExternalActionStatus.UNCERTAIN.value,
                    "uncertainty_reason": "executing_lease_expired",
                    "error_code": dsc.SafeErrorCode.RECONCILIATION_REQUIRED.value,
                    "lease_owner": None, "lease_started_at": None,
                    "updated_at": now, "completed_at": now,
                }
                txn.update(ref, fields)
                return _contract_error("action requires reconciliation",
                                       "reconciliation_required") | {
                                           "action_id": action_id, **fields}
            if _lease_is_active(row):
                return {"status": "success", "in_progress": True,
                        "action_id": action_id}
            # PREPARED proves T2 never committed, so no provider call was
            # authorized. Reclaim the same action and same approval claim.
            approval_id_existing = row.get("approval_id")
            approval_existing_ref = (
                get_client().collection("approvals").document(
                    approval_id_existing) if row.get("approval_consumed")
                and approval_id_existing else None)
            approval_existing = None
            if approval_existing_ref is not None:
                approval_existing_snapshot = await approval_existing_ref.get(
                    transaction=txn)
                if not approval_existing_snapshot.exists:
                    return _contract_error("approval claim is missing",
                                           "approval_binding_missing")
                approval_existing = approval_existing_snapshot.to_dict()
                if (approval_existing.get("status") != "CLAIMED"
                        or approval_existing.get("claimed_action_id") != action_id
                        or approval_existing.get("claim_id") != row.get("claim_id")):
                    return _contract_error("approval claim changed",
                                           "approval_binding_mismatch")
            owner = uuid.uuid4().hex
            generation = int(row.get("lease_generation") or 0) + 1
            fields = {
                "lease_owner": owner, "lease_started_at": now,
                "lease_generation": generation, "updated_at": now,
            }
            txn.update(ref, fields)
            if approval_existing_ref is not None:
                txn.update(approval_existing_ref, {
                    "claim_lease_generation": generation,
                    "claim_lease_started_at": now, "updated_at": now,
                })
            return {"status": "success", "claimed": True,
                    "reclaimed": True, "action_id": action_id,
                    "lease_owner": owner}
        approval = None
        authority_fields: dict[str, Any] = {}
        if approval_ref is not None:
            approval_snapshot = await approval_ref.get(transaction=txn)
            if not approval_snapshot.exists:
                return _contract_error("approval not found", "approval_missing")
            approval = approval_snapshot.to_dict()
            if (approval.get("status") != "GRANTED"
                    or approval.get("expires_at", "") <= now):
                return _contract_error(
                    "approval is not granted and current", "approval_terminal")
            expected = {
                "founder_id": founder_id,
                "session_id": session_id,
                "application_id": approval_target or application_id,
                "gate": approval_gate,
                "subject_hash": subject_hash,
            }
            if int(approval.get("schema_version") or 1) >= 2:
                required_binding_keys = {
                    "action_kind", "capability_id", "capability_version",
                    "target_hash", "normalized_payload_hash", "policy_id",
                    "policy_version", "connector_id",
                    "connector_binding_version",
                }
                if set(exact_bindings) != required_binding_keys:
                    return _contract_error(
                        "approved action binding is incomplete",
                        "approval_binding_missing")
                expected.update(exact_bindings)
                # Rows minted by PlatformApprovalService carry the aggregate
                # v2 approval subject. Older migration-window rows may have
                # schema_version=2 bindings but no real run/step; they remain
                # dual-readable locally and are forbidden from new production
                # creation by approval_service.
                if approval.get("approval_subject_hash"):
                    run_id = str(approval.get("run_id") or "")
                    step_id = str(approval.get("step_id") or "")
                    plan_hash = str(approval.get("plan_hash") or "")
                    deciding_actor_id = str(
                        approval.get("decided_by_actor_id") or "")
                    if not all((run_id, step_id, plan_hash, deciding_actor_id)):
                        return _contract_error(
                            "approval control authority is incomplete",
                            "approval_binding_missing")
                    from services.workflow_contracts import stable_id

                    membership_id = stable_id(
                        "membership", founder_id, deciding_actor_id)
                    run_ref = get_client().collection("workflow_runs").document(run_id)
                    step_ref = get_client().collection("workflow_steps").document(step_id)
                    membership_ref = get_client().collection(
                        "workspace_members").document(membership_id)
                    run_snapshot = await run_ref.get(transaction=txn)
                    step_snapshot = await step_ref.get(transaction=txn)
                    membership_snapshot = await membership_ref.get(transaction=txn)
                    if (not run_snapshot.exists or not step_snapshot.exists
                            or not membership_snapshot.exists):
                        return _contract_error(
                            "approval run, step, or deciding membership is missing",
                            "approval_authority_missing")
                    run = run_snapshot.to_dict()
                    step = step_snapshot.to_dict()
                    membership = membership_snapshot.to_dict()
                    if (run.get("workspace_id") != founder_id
                            or step.get("workspace_id") != founder_id
                            or step.get("run_id") != run_id
                            or run.get("plan_hash") != plan_hash
                            or membership.get("workspace_id") != founder_id
                            or membership.get("actor_id") != deciding_actor_id
                            or membership.get("status") != "ACTIVE"):
                        return _contract_error(
                            "approval control authority changed",
                            "approval_authority_missing")
                    runtime_status = str(
                        run.get("runtime_status") or run.get("status") or "")
                    if runtime_status in {
                            "CANCELLING", "CANCELLED", "SUCCEEDED", "COMPLETED",
                            "FAILED", "REJECTED"}:
                        return _contract_error(
                            "workflow run no longer authorizes consequences",
                            "run_fenced")
                    authority_fields = {
                        "run_id": run_id, "step_id": step_id,
                        "plan_hash": plan_hash,
                        "observed_cancellation_generation": int(
                            run.get("cancellation_generation") or 0),
                        "deciding_actor_id": deciding_actor_id,
                        "deciding_membership_id": membership_id,
                    }
            if any((approval.get(key) or "") != (value or "")
                   for key, value in expected.items()):
                return _contract_error(
                    "approval does not cover this exact action",
                    "approval_binding_mismatch")
        owner = uuid.uuid4().hex
        claim_id = dsc.canonical_hash({
            "approval_id": approval_id or "", "action_id": action_id,
            "request_hash": request_hash,
        })
        row = {
            "schema_version": 2, "action_id": action_id,
            "workspace_id": founder_id, "action_domain": "CONNECTOR_ACTION",
            "founder_id": founder_id, "connection_id": connection_id,
            "session_id": session_id, "application_id": application_id,
            "resource_id": resource_id, "action_kind": kind.value,
            "idempotency_key": str(idempotency_key)[:512],
            "request_hash": request_hash, "subject_hash": subject_hash,
            "approval_id": approval_id,
            "approval_target": approval_target,
            "capability_id": capability_id,
            "capability_version": capability_version,
            "target_hash": exact_bindings.get("target_hash"),
            "normalized_payload_hash": exact_bindings.get(
                "normalized_payload_hash"),
            "policy_id": exact_bindings.get("policy_id"),
            "policy_version": exact_bindings.get("policy_version"),
            "connector_id": exact_bindings.get("connector_id"),
            "connector_binding_version": exact_bindings.get(
                "connector_binding_version"),
            **authority_fields,
            "approval_consumed": bool(approval_ref), "claim_id": claim_id,
            "sandbox_context": context or None,
            "status": dsc.ExternalActionStatus.PREPARED.value,
            "provider_request_id": None,
            "provider_idempotency_key": dsc.canonical_hash({
                "workspace_id": founder_id, "action_id": action_id}),
            "consequence_start_committed_at": None,
            "provider_effect_id": None, "result_ref": {},
            "uncertainty_reason": None, "error_code": None,
            "lease_owner": owner, "lease_started_at": now,
            "lease_generation": 1,
            "lease_seconds": max(1, min(lease_seconds, 900)),
            "created_at": now, "updated_at": now, "completed_at": None,
        }
        if approval_ref is not None:
            txn.update(approval_ref, {
                "status": "CLAIMED", "claim_id": claim_id,
                "claimed_action_id": action_id, "claimed_at": now,
                "claim_lease_generation": 1,
                "claim_lease_started_at": now, "updated_at": now,
            })
        txn.create(ref, row)
        return {"status": "success", "claimed": True,
                "action_id": action_id, "lease_owner": owner}

    return await _prepare(transaction)


async def start_external_action(
        founder_id: str, action_id: str, lease_owner: str) -> dict[str, Any]:
    """T2: atomically spend a claim and authorize exactly one provider call."""
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    ref = get_client().collection("external_actions").document(action_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _start(txn):
        snapshot = await ref.get(transaction=txn)
        if not snapshot.exists:
            return _contract_error("external action not found", "owner_mismatch")
        row = snapshot.to_dict()
        if not _workspace_owner_matches(row, founder_id):
            return _contract_error("external action not found", "owner_mismatch")
        if row.get("status") == dsc.ExternalActionStatus.EXECUTING.value:
            if row.get("lease_owner") == lease_owner:
                return {**row, "status": "success", "duplicate": True,
                        "claimed": True, "receipt_status": "EXECUTING"}
            return _contract_error("external action lease changed", "lease_conflict")
        if row.get("status") != dsc.ExternalActionStatus.PREPARED.value:
            if row.get("status") == dsc.ExternalActionStatus.UNCERTAIN.value:
                return _contract_error("action requires reconciliation",
                                       "reconciliation_required")
            return {"status": "success", "duplicate": True, **row}
        if row.get("lease_owner") != lease_owner:
            return _contract_error("external action lease changed", "lease_conflict")
        approval_ref = (get_client().collection("approvals").document(
            row["approval_id"]) if row.get("approval_consumed")
            and row.get("approval_id") else None)
        approval = None
        run_ref = step_ref = membership_ref = None
        run = step = membership = None
        next_budget_usage = None
        if approval_ref is not None:
            approval_snapshot = await approval_ref.get(transaction=txn)
            if not approval_snapshot.exists:
                return _contract_error("approval claim is missing",
                                       "approval_binding_missing")
            approval = approval_snapshot.to_dict()
            if (approval.get("status") != "CLAIMED"
                    or approval.get("claimed_action_id") != action_id
                    or approval.get("claim_id") != row.get("claim_id")):
                return _contract_error("approval claim changed",
                                       "approval_binding_mismatch")
            if (int(approval.get("schema_version") or 1) >= 2
                    and approval.get("approval_subject_hash")):
                run_id = str(row.get("run_id") or "")
                step_id = str(row.get("step_id") or "")
                membership_id = str(row.get("deciding_membership_id") or "")
                if not all((run_id, step_id, membership_id, row.get("plan_hash"))):
                    return _contract_error(
                        "action control authority is incomplete",
                        "approval_binding_missing")
                run_ref = get_client().collection("workflow_runs").document(run_id)
                step_ref = get_client().collection("workflow_steps").document(step_id)
                membership_ref = get_client().collection(
                    "workspace_members").document(membership_id)
                run_snapshot = await run_ref.get(transaction=txn)
                step_snapshot = await step_ref.get(transaction=txn)
                membership_snapshot = await membership_ref.get(transaction=txn)
                run = run_snapshot.to_dict() if run_snapshot.exists else None
                step = step_snapshot.to_dict() if step_snapshot.exists else None
                membership = (membership_snapshot.to_dict()
                              if membership_snapshot.exists else None)
                runtime_status = str(
                    (run or {}).get("runtime_status")
                    or (run or {}).get("status") or "")
                budget_usage = dict((run or {}).get("budget_usage") or {})
                budget_limits = dict((run or {}).get("budgets") or {})
                provider_budget_exhausted = int(
                    budget_usage.get("provider_calls") or 0) >= int(
                        budget_limits.get("max_provider_calls", 20))
                fenced = (
                    not run or not step or not membership
                    or run.get("workspace_id") != founder_id
                    or step.get("workspace_id") != founder_id
                    or step.get("run_id") != run_id
                    or run.get("plan_hash") != row.get("plan_hash")
                    or int(run.get("cancellation_generation") or 0)
                    != int(row.get("observed_cancellation_generation") or 0)
                    or runtime_status in {
                        "CANCELLING", "CANCELLED", "SUCCEEDED", "COMPLETED",
                        "FAILED", "REJECTED"}
                    or membership.get("workspace_id") != founder_id
                    or membership.get("actor_id") != row.get("deciding_actor_id")
                    or membership.get("status") != "ACTIVE"
                    or provider_budget_exhausted)
                if fenced:
                    now = _now()
                    fence_reason = ("budget_exhausted"
                                    if provider_budget_exhausted
                                    else "authority_fenced_before_provider")
                    txn.update(ref, {
                        "status": dsc.ExternalActionStatus.FAILED.value,
                        "error_code": dsc.SafeErrorCode.PERMISSION_DENIED.value,
                        "uncertainty_reason": fence_reason,
                        "lease_owner": None, "lease_started_at": None,
                        "updated_at": now, "completed_at": now,
                    })
                    txn.update(approval_ref, {
                        "status": "VOIDED", "voided_at": now,
                        "void_reason": fence_reason,
                        "terminal_action_id": action_id, "updated_at": now,
                    })
                    return _contract_error(
                        "run, plan, or deciding membership no longer authorizes this action",
                        fence_reason)
                next_budget_usage = {
                    **budget_usage,
                    "provider_calls": int(
                        budget_usage.get("provider_calls") or 0) + 1,
                }
        now = _now()
        provider_request_id = dsc.canonical_hash({
            "action_id": action_id,
            "lease_generation": int(row.get("lease_generation") or 1),
        })
        fields = {
            "status": dsc.ExternalActionStatus.EXECUTING.value,
            "provider_request_id": provider_request_id,
            "consequence_start_committed_at": now,
            "provider_started_at": now, "updated_at": now,
        }
        txn.update(ref, fields)
        if run_ref is not None and next_budget_usage is not None:
            txn.update(run_ref, {
                "budget_usage": next_budget_usage,
                "version": int((run or {}).get("version") or 0) + 1,
                "updated_at": now})
        if approval_ref is not None:
            txn.update(approval_ref, {
                "status": "CONSUMED", "consumed_at": now,
                "terminal_action_id": action_id, "updated_at": now,
            })
        return {**fields, "status": "success", "duplicate": False,
                "claimed": True, "receipt_status": "EXECUTING",
                "action_id": action_id, "lease_owner": lease_owner}

    return await _start(transaction)


async def finish_external_action(
        founder_id: str, action_id: str, lease_owner: str, status: str, *,
        provider_effect_id: str | None = None,
        result_ref: dict[str, Any] | None = None,
        uncertainty_reason: str | None = None,
        error_code: str | None = None) -> dict[str, Any]:
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    try:
        terminal = dsc.require_closed(status, dsc.ExternalActionStatus)
        if terminal in {dsc.ExternalActionStatus.PREPARED,
                        dsc.ExternalActionStatus.EXECUTING}:
            raise ValueError("not terminal")
        if error_code:
            dsc.require_closed(error_code, dsc.SafeErrorCode)
    except ValueError:
        return _contract_error("invalid action completion")
    ref = get_client().collection("external_actions").document(action_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _finish(txn):
        snapshot = await ref.get(transaction=txn)
        if not snapshot.exists or snapshot.to_dict().get("founder_id") != founder_id:
            return _contract_error("external action not found", "owner_mismatch")
        current = snapshot.to_dict()
        allowed = {dsc.ExternalActionStatus.EXECUTING.value}
        # Dual-read compatibility for a worker deployed under schema v1 that
        # prepared and consumed authority before this protocol rolled out.
        if int(current.get("schema_version") or 1) == 1:
            allowed.add(dsc.ExternalActionStatus.PREPARED.value)
        if current.get("status") not in allowed:
            return {"status": "success", "duplicate": True, **current}
        if current.get("lease_owner") != lease_owner:
            return _contract_error("external action lease changed", "lease_conflict")
        application_ref = None
        application = None
        if (current.get("action_kind") == "submit_application"
                and terminal is dsc.ExternalActionStatus.SUCCEEDED):
            application_id = str(current.get("application_id") or "")
            application_ref = get_client().collection("applications").document(
                application_id)
            application_snapshot = await application_ref.get(transaction=txn)
            if (not application_snapshot.exists
                    or application_snapshot.to_dict().get("founder_id") != founder_id):
                # The provider may already have accepted the submission. Leave
                # EXECUTING to expire into UNCERTAIN so reconciliation can
                # repair the coupled domain transition without a blind retry.
                return _contract_error(
                    "submission domain authority is missing",
                    "reconciliation_required")
            application = application_snapshot.to_dict()
        safe_result = {str(key)[:64]: str(value)[:280]
                       for key, value in (result_ref or {}).items()}
        now = _now()
        fields = {
            "status": terminal.value,
            "provider_effect_id": (str(provider_effect_id)[:512]
                                   if provider_effect_id else None),
            "result_ref": safe_result,
            "uncertainty_reason": (str(uncertainty_reason)[:160]
                                   if uncertainty_reason else None),
            "error_code": error_code, "lease_owner": None,
            "lease_started_at": None, "updated_at": now,
            "completed_at": now,
        }
        txn.update(ref, fields)
        if application_ref is not None and application is not None:
            txn.update(application_ref, {
                "state": "SUBMITTED",
                "submission": {
                    "confirmation_id": str(
                        safe_result.get("confirmation_id")
                        or provider_effect_id or "")[:512],
                    "portal_url": str(safe_result.get("portal_url") or "")[:1000],
                    "external_action_id": action_id,
                },
                "updated_at": now,
                "version": int(application.get("version") or 0) + 1,
            })
        return {"status": "success", "duplicate": False,
                "action_id": action_id, **fields}

    return await _finish(transaction)


async def reconcile_external_action(
        founder_id: str, action_id: str, status: str, *,
        provider_effect_id: str | None = None,
        result_ref: dict[str, Any] | None = None,
        error_code: str | None = None) -> dict[str, Any]:
    """Resolve an UNCERTAIN receipt from provider reconciliation evidence."""
    from google.cloud import firestore as gc_firestore

    from services import data_source_contracts as dsc

    try:
        terminal = dsc.require_closed(status, dsc.ExternalActionStatus)
        if terminal not in {dsc.ExternalActionStatus.SUCCEEDED,
                            dsc.ExternalActionStatus.FAILED}:
            raise ValueError("reconciliation must be definitive")
        if error_code:
            dsc.require_closed(error_code, dsc.SafeErrorCode)
    except ValueError:
        return _contract_error("invalid reconciliation")
    ref = get_client().collection("external_actions").document(action_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _resolve(txn):
        snapshot = await ref.get(transaction=txn)
        if not snapshot.exists or snapshot.to_dict().get("founder_id") != founder_id:
            return _contract_error("external action not found", "owner_mismatch")
        current = snapshot.to_dict()
        if current.get("status") in {
                dsc.ExternalActionStatus.SUCCEEDED.value,
                dsc.ExternalActionStatus.FAILED.value}:
            return {"status": "success", "duplicate": True, **current}
        if current.get("status") != dsc.ExternalActionStatus.UNCERTAIN.value:
            return _contract_error("action is not awaiting reconciliation",
                                   "version_conflict")
        application_ref = None
        application = None
        if (current.get("action_kind") == "submit_application"
                and terminal is dsc.ExternalActionStatus.SUCCEEDED):
            application_id = str(current.get("application_id") or "")
            application_ref = get_client().collection("applications").document(
                application_id)
            application_snapshot = await application_ref.get(transaction=txn)
            if (not application_snapshot.exists
                    or application_snapshot.to_dict().get("founder_id") != founder_id):
                return _contract_error(
                    "submission domain authority is missing",
                    "reconciliation_required")
            application = application_snapshot.to_dict()
        safe_result = {str(key)[:64]: str(value)[:280]
                       for key, value in (result_ref or {}).items()}
        now = _now()
        fields = {
            "status": terminal.value,
            "provider_effect_id": (str(provider_effect_id)[:512]
                                   if provider_effect_id else None),
            "result_ref": safe_result,
            "uncertainty_reason": None, "error_code": error_code,
            "updated_at": now, "completed_at": now,
        }
        txn.update(ref, fields)
        if application_ref is not None and application is not None:
            txn.update(application_ref, {
                "state": "SUBMITTED",
                "submission": {
                    "confirmation_id": str(
                        safe_result.get("confirmation_id")
                        or provider_effect_id or "")[:512],
                    "portal_url": str(safe_result.get("portal_url") or "")[:1000],
                    "external_action_id": action_id,
                },
                "updated_at": now,
                "version": int(application.get("version") or 0) + 1,
            })
        return {"status": "success", "duplicate": False,
                "action_id": action_id, **fields}

    return await _resolve(transaction)


async def get_external_action(founder_id: str,
                              action_id: str) -> Optional[dict[str, Any]]:
    snapshot = await get_client().collection("external_actions").document(
        action_id).get()
    if not snapshot.exists:
        return None
    row = snapshot.to_dict() | {"id": snapshot.id}
    return row if _workspace_owner_matches(row, founder_id) else None


async def list_external_actions(founder_id: str, *,
                                limit: int = 100) -> list[dict[str, Any]]:
    query = get_client().collection("external_actions").where(
        "workspace_id", "==", founder_id)
    rows = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]
    rows.sort(key=lambda row: row.get("created_at", ""), reverse=True)
    return rows[:limit]

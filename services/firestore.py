"""Pipeline store accessors (docs/02).

The client is lazy: importing this module never requires credentials; the first
call does. Collections: opportunities, applications, profiles, feedback,
approvals, audit, ingestions, browser_runs.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

_client = None


def get_client():
    """Lazy Firestore client (raises on first call if credentials are missing)."""
    global _client
    if _client is None:
        from google.cloud import firestore

        _client = firestore.AsyncClient(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            database=os.environ.get("FIRESTORE_DATABASE", "(default)"),
        )
    return _client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# opportunities
# ---------------------------------------------------------------------------

async def find_opportunity_by_hash(dedup_hash: str) -> Optional[str]:
    """Return the existing opportunity id for a dedup hash, else None."""
    query = get_client().collection("opportunities").where("dedup_hash", "==", dedup_hash).limit(1)
    async for doc in query.stream():
        return doc.id
    return None


async def create_opportunity(record: dict[str, Any]) -> str:
    """Create an opportunity in DISCOVERED, deduping on dedup_hash."""
    existing = await find_opportunity_by_hash(record["dedup_hash"])
    if existing:
        return existing
    doc_id = _new_id()
    await get_client().collection("opportunities").document(doc_id).set(
        {**record, "state": "DISCOVERED", "created_at": _now(), "updated_at": _now()}
    )
    return doc_id


async def get_opportunity(opportunity_id: str) -> Optional[dict[str, Any]]:
    if not opportunity_id:  # empty id would build an invalid document path
        return None
    doc = await get_client().collection("opportunities").document(opportunity_id).get()
    return doc.to_dict() | {"id": doc.id} if doc.exists else None


async def list_unscored_opportunities(limit: int = 10) -> list[dict[str, Any]]:
    # single-field filter only; sort client-side — no composite index required
    query = get_client().collection("opportunities").where("state", "==", "DISCOVERED")
    docs = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]
    docs.sort(key=lambda d: d.get("created_at", ""))
    return docs[:limit]


async def set_opportunity_state(opportunity_id: str, state: str, **fields: Any) -> None:
    await get_client().collection("opportunities").document(opportunity_id).update(
        {"state": state, **fields, "updated_at": _now()}
    )


# ---------------------------------------------------------------------------
# applications
# ---------------------------------------------------------------------------

async def create_application(founder_id: str, opportunity_id: str, checklist: list[dict]) -> str:
    doc_id = _new_id()
    await get_client().collection("applications").document(doc_id).set(
        {
            "opportunity_id": opportunity_id,
            "founder_id": founder_id,
            "state": "INTERVIEWING",
            "checklist": checklist,
            "interview_qa": [],
            "draft_sections": [],
            "form_fill_report": None,
            "submit_idempotency_key": None,
            "submission": None,
            "followups": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
    )
    return doc_id


async def get_application(application_id: str) -> Optional[dict[str, Any]]:
    doc = await get_client().collection("applications").document(application_id).get()
    return doc.to_dict() | {"id": doc.id} if doc.exists else None


async def update_application(application_id: str, **fields: Any) -> None:
    await get_client().collection("applications").document(application_id).update(
        {**fields, "updated_at": _now()}
    )


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
        "voice_rules": [],
        "canonical_answers": [],
        "rejection_history": [],
        "decision_patterns": [],
    }


async def apply_profile_update(founder_id: str, kind: str, payload: dict, evidence: str) -> int:
    """Append a profile mutation and bump version. Returns the new version."""
    profile = await get_profile(founder_id)
    collection = {
        "voice_rule": "voice_rules",
        "canonical_answer_update": "canonical_answers",
        "fact_update": "facts",
        "decision_pattern": "decision_patterns",
    }[kind]
    entry = {**payload, "evidence": evidence, "created_at": _now()}
    if collection == "facts":
        profile["facts"].update(payload)
    else:
        entry["id"] = _new_id()[:12]
        profile[collection].append(entry)
    profile["version"] = profile.get("version", 0) + 1
    await get_client().collection("profiles").document(founder_id).set(profile)
    await audit(
        actor="agent:distiller",
        action="profile_update",
        target=f"profiles/{founder_id}",
        result="success",
        detail=f"{kind}: {evidence[:200]}",
    )
    return profile["version"]


# ---------------------------------------------------------------------------
# board / pipeline views
# ---------------------------------------------------------------------------

async def list_opportunities(limit: int = 40) -> list[dict[str, Any]]:
    query = get_client().collection("opportunities").order_by("created_at", direction="DESCENDING").limit(limit)
    return [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]


async def list_inflight_applications(founder_id: str) -> list[dict[str, Any]]:
    # single-field filter only; CLOSED filtered and sorted client-side —
    # no composite index required (demo-scale data, reproducible setup)
    query = get_client().collection("applications").where("founder_id", "==", founder_id)
    docs = [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]
    docs = [d for d in docs if d.get("state") != "CLOSED"]
    docs.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return docs


# ---------------------------------------------------------------------------
# feedback
# ---------------------------------------------------------------------------

async def create_feedback(founder_id: str, application_id: str, section_id: str,
                          feedback_type: str, original: str, reason: str,
                          edited_text: str) -> str:
    doc_id = _new_id()
    await get_client().collection("feedback").document(doc_id).set(
        {
            "founder_id": founder_id,
            "application_id": application_id,
            "section_id": section_id,
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


async def get_feedback(feedback_id: str) -> Optional[dict[str, Any]]:
    doc = await get_client().collection("feedback").document(feedback_id).get()
    return doc.to_dict() | {"id": doc.id} if doc.exists else None


async def mark_distilled(feedback_id: str, rule_ids: list[str]) -> None:
    await get_client().collection("feedback").document(feedback_id).update(
        {"distilled": True, "distilled_rule_ids": rule_ids}
    )


# ---------------------------------------------------------------------------
# approvals (docs/12: tokens exist only here — never in model context)
# ---------------------------------------------------------------------------

async def create_approval(application_id: str, gate: str, ttl_minutes: int,
                          details: Optional[dict[str, Any]] = None) -> str:
    from datetime import timedelta

    doc_id = _new_id()
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    await get_client().collection("approvals").document(doc_id).set(
        {
            "application_id": application_id,
            "gate": gate,
            "details": details or {},  # what the founder is approving (e.g. email to/subject/body)
            "token": None,
            "status": "PENDING",
            "expires_at": expires.isoformat(),
            "granted_by": None,
            "consumed_at": None,
            "created_at": _now(),
        }
    )
    return doc_id


async def list_pending_approvals() -> list[dict[str, Any]]:
    """All PENDING, unexpired approvals, newest first — the global approval inbox."""
    now = _now()
    query = get_client().collection("approvals").where("status", "==", "PENDING")
    out = []
    async for doc in query.stream():
        record = doc.to_dict()
        if record.get("expires_at", "") > now:
            out.append(record | {"id": doc.id})
    return sorted(out, key=lambda a: a.get("created_at", ""), reverse=True)


async def grant_approval(approval_id: str, founder_id: str) -> None:
    await get_client().collection("approvals").document(approval_id).update(
        {"token": _new_id(), "status": "GRANTED", "granted_by": founder_id}
    )


async def deny_approval(approval_id: str) -> None:
    await get_client().collection("approvals").document(approval_id).update({"status": "DENIED"})


async def get_approval(approval_id: str) -> Optional[dict[str, Any]]:
    doc = await get_client().collection("approvals").document(approval_id).get()
    return doc.to_dict() | {"id": doc.id} if doc.exists else None


async def find_valid_approval(application_id: str) -> Optional[dict[str, Any]]:
    """Server-side lookup for submit: GRANTED, unexpired, unconsumed."""
    now = _now()
    query = (
        get_client()
        .collection("approvals")
        .where("application_id", "==", application_id)
        .where("status", "==", "GRANTED")
    )
    async for doc in query.stream():
        record = doc.to_dict()
        if record.get("expires_at", "") > now:
            return record | {"id": doc.id}
    return None


async def find_pending_approval(application_id: str) -> Optional[dict[str, Any]]:
    """The open PENDING approval for an application (UI approval gate)."""
    query = (
        get_client()
        .collection("approvals")
        .where("application_id", "==", application_id)
        .where("status", "==", "PENDING")
        .limit(1)
    )
    async for doc in query.stream():
        return doc.to_dict() | {"id": doc.id}
    return None


async def consume_approval(approval_id: str) -> None:
    await get_client().collection("approvals").document(approval_id).update(
        {"status": "CONSUMED", "consumed_at": _now()}
    )


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
    """Update mutable BrowserRun fields; immutable identity and goal are ignored."""
    fields.pop("goal", None)
    fields.pop("run_id", None)
    await get_client().collection("browser_runs").document(run_id).update(
        {**fields, "updated_at": _now()}
    )


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


async def update_browser_action(run_id: str, action_id: str, **fields: Any) -> None:
    ref = (get_client().collection("browser_runs").document(run_id)
           .collection("actions").document(action_id))
    await ref.update({**fields, "updated_at": _now()})


# ---------------------------------------------------------------------------
# ingestions (docs/02, 06 §bootstrap)
# ---------------------------------------------------------------------------

async def create_ingestion(founder_id: str, source_type: str, source_ref: str,
                           artifact: str, proposed_updates: list[dict]) -> str:
    doc_id = _new_id()
    await get_client().collection("ingestions").document(doc_id).set(
        {
            "founder_id": founder_id,
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


async def get_processed_gmail_ids() -> list[str]:
    doc = await get_client().collection("gmail_state").document("processed").get()
    return doc.to_dict().get("ids", []) if doc.exists else []


async def add_processed_gmail_ids(ids: list[str]) -> None:
    ref = get_client().collection("gmail_state").document("processed")
    doc = await ref.get()
    existing = doc.to_dict().get("ids", []) if doc.exists else []
    await ref.set({"ids": (existing + ids)[-2000:]})  # bounded, idempotent rescans


async def set_last_gmail_scan(summary: dict[str, Any]) -> None:
    await get_client().collection("gmail_state").document("last_scan").set(
        {**summary, "at": _now()})


async def get_last_gmail_scan() -> Optional[dict[str, Any]]:
    doc = await get_client().collection("gmail_state").document("last_scan").get()
    return doc.to_dict() if doc.exists else None


# alex_mail_state (adr/001 v2): same pattern as gmail_state, separate collection
# — Alex's mailbox is a different account with its own idempotency store.

async def get_processed_alex_ids() -> list[str]:
    doc = await get_client().collection("alex_mail_state").document("processed").get()
    return doc.to_dict().get("ids", []) if doc.exists else []


async def add_processed_alex_ids(ids: list[str]) -> None:
    ref = get_client().collection("alex_mail_state").document("processed")
    doc = await ref.get()
    existing = doc.to_dict().get("ids", []) if doc.exists else []
    await ref.set({"ids": (existing + ids)[-2000:]})


async def get_alex_history_id() -> Optional[str]:
    doc = await get_client().collection("alex_mail_state").document("watch").get()
    return doc.to_dict().get("history_id") if doc.exists else None


async def set_alex_history_id(history_id: str) -> None:
    await get_client().collection("alex_mail_state").document("watch").set(
        {"history_id": history_id, "at": _now()})


async def set_last_alex_scan(summary: dict[str, Any]) -> None:
    await get_client().collection("alex_mail_state").document("last_scan").set(
        {**summary, "at": _now()})


async def get_last_alex_scan() -> Optional[dict[str, Any]]:
    doc = await get_client().collection("alex_mail_state").document("last_scan").get()
    return doc.to_dict() if doc.exists else None


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


async def next_document_version(founder_id: str, doc_key: str) -> int:
    docs = await list_documents(founder_id)
    return sum(1 for d in docs if d.get("doc_key") == doc_key) + 1

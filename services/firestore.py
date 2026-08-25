"""Pipeline store accessors (docs/02).

The client is lazy: importing this module never requires credentials; the first
call does. Collections: opportunities, applications, profiles, feedback,
approvals, audit, ingestions, browser_runs.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_client = None


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
# opportunities
# ---------------------------------------------------------------------------

async def find_opportunity_by_hash(dedup_hash: str) -> Optional[str]:
    """Return the existing opportunity id for a dedup hash, else None."""
    query = get_client().collection("opportunities").where("dedup_hash", "==", dedup_hash).limit(1)
    async for doc in query.stream():
        return doc.id
    return None


async def create_opportunity(record: dict[str, Any]) -> str:
    """Create an opportunity in DISCOVERED, deduping on dedup_hash.

    The doc id is derived from dedup_hash and the create runs in a transaction,
    so two concurrent sweeps that discover the same program can never produce
    duplicate opportunities (the old query-then-write was racy)."""
    from google.cloud import firestore as gc_firestore

    doc_id = hashlib.sha256(record["dedup_hash"].encode()).hexdigest()[:24]
    ref = get_client().collection("opportunities").document(doc_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _create(txn) -> str:
        snap = await ref.get(transaction=txn)
        if snap.exists:
            return doc_id  # already discovered — never a duplicate
        txn.set(ref, {**record, "state": "DISCOVERED",
                      "created_at": _now(), "updated_at": _now()})
        return doc_id

    return await _create(transaction)


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
                             account: str, ttl_minutes: int = 10) -> None:
    """Persist one PKCE consent transaction across restarts and instances."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    await get_client().collection("oauth_states").document(state).set({
        "verifier": verifier,
        "scopes": scopes,
        "account": account,
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
        "voice_rules": [],
        "canonical_answers": [],
        "rejection_history": [],
        "decision_patterns": [],
    }


def _default_profile() -> dict[str, Any]:
    return {"version": 0, "facts": {}, "voice_rules": [], "canonical_answers": [],
            "rejection_history": [], "decision_patterns": []}


async def apply_profile_update(founder_id: str, kind: str, payload: dict, evidence: str) -> int:
    """Append a profile mutation and bump version, transactionally.

    Read-modify-write inside a Firestore transaction so concurrent distiller /
    ingestion writers cannot clobber each other's rules/answers or duplicate a
    version number — the version is assigned inside the transaction."""
    from google.cloud import firestore as gc_firestore

    collection = {
        "voice_rule": "voice_rules",
        "canonical_answer_update": "canonical_answers",
        "fact_update": "facts",
        "decision_pattern": "decision_patterns",
    }[kind]
    entry = {**payload, "evidence": evidence, "created_at": _now()}
    if collection != "facts":
        entry["id"] = _new_id()[:12]

    ref = get_client().collection("profiles").document(founder_id)
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _apply(txn) -> int:
        snap = await ref.get(transaction=txn)
        profile = snap.to_dict() if snap.exists else _default_profile()
        if collection == "facts":
            facts = dict(profile.get("facts", {}))
            facts.update(payload)
            profile["facts"] = facts
        else:
            items = list(profile.get(collection, []))
            items.append(entry)
            profile[collection] = items
        profile["version"] = int(profile.get("version", 0)) + 1
        txn.set(ref, profile)
        return profile["version"]

    version = await _apply(transaction)
    await audit(
        actor="agent:distiller",
        action="profile_update",
        target=f"profiles/{founder_id}",
        result="success",
        detail=f"{kind}: {evidence[:200]}",
    )
    return version


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
                          details: Optional[dict[str, Any]] = None,
                          founder_id: str = "", session_id: str = "") -> str:
    from datetime import timedelta

    doc_id = _new_id()
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    await get_client().collection("approvals").document(doc_id).set(
        {
            "application_id": application_id,
            "gate": gate,
            "founder_id": founder_id,
            "session_id": session_id,
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


async def list_pending_approvals(founder_id: str = "", session_id: str = "") -> list[dict[str, Any]]:
    """Pending approvals scoped to one founder session, newest first."""
    now = _now()
    query = get_client().collection("approvals").where("status", "==", "PENDING")
    out = []
    async for doc in query.stream():
        record = doc.to_dict()
        if record.get("expires_at", "") <= now:
            continue
        if founder_id and record.get("founder_id") != founder_id:
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


async def get_approval(approval_id: str) -> Optional[dict[str, Any]]:
    doc = await get_client().collection("approvals").document(approval_id).get()
    return doc.to_dict() | {"id": doc.id} if doc.exists else None


async def find_valid_approval(application_id: str, gate: str = "",
                              founder_id: str = "", session_id: str = "") -> Optional[dict[str, Any]]:
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
        if (record.get("expires_at", "") > now
                and (not gate or record.get("gate") == gate)
                and (not founder_id or record.get("founder_id") == founder_id)
                and (not session_id or record.get("session_id") == session_id)):
            return record | {"id": doc.id}
    return None


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


async def _append_processed_ids(collection: str, ids: list[str]) -> None:
    """Atomic, bounded append. A transaction (not a bare read-modify-write) so
    concurrent scans cannot drop each other's ids and re-emit duplicate
    follow-ups; the last-2000 bound keeps rescans idempotent yet finite."""
    from google.cloud import firestore as gc_firestore

    ref = get_client().collection(collection).document("processed")
    transaction = get_client().transaction()

    @gc_firestore.async_transactional
    async def _append(txn) -> None:
        snap = await ref.get(transaction=txn)
        existing = (snap.to_dict() or {}).get("ids", []) if snap.exists else []
        seen = set(existing)
        merged = existing + [i for i in ids if i not in seen]
        txn.set(ref, {"ids": merged[-2000:]})

    await _append(transaction)


async def add_processed_gmail_ids(ids: list[str]) -> None:
    await _append_processed_ids("gmail_state", ids)


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
    await _append_processed_ids("alex_mail_state", ids)


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


# Pending portal verification routing is non-secret and must survive Cloud Run
# scale-to-zero. Passwords remain exclusively in Secret Manager.
async def save_pending_portal_registration(host: str, founder_id: str,
                                           session_id: str, portal_url: str,
                                           email: str) -> None:
    doc_id = hashlib.sha256(host.lower().encode()).hexdigest()[:24]
    await get_client().collection("portal_registrations").document(doc_id).set({
        "host": host, "founder_id": founder_id, "session_id": session_id,
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

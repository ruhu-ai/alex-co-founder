"""Shared fixtures: an in-memory fake of the services.firestore module so
service-layer tests run without credentials or an emulator."""

import hashlib
import uuid
from datetime import datetime, timezone

import pytest


async def bind_fill_report(store, app_id, signature="sha256:sig", mapping=None):
    """Write the bound `form_fill_report` a real fill leaves behind.

    Both hashes present is the normal, post-fix shape (docs/02): tests that skip
    it would be testing an unbound gate, which now fails closed.
    """
    from services import approval_service, firestore

    mapping = {"a": "1"} if mapping is None else mapping
    fill_mapping_hash = approval_service.mapping_hash(mapping)
    await firestore.update_application(
        app_id,
        form_fill_report={"filled": len(mapping), "total": len(mapping),
                          "needs_human": [], "portal_state_hash": signature,
                          "mapping_hash": fill_mapping_hash,
                          "screenshot_artifact": "", "ran_at": ""},
        last_fill_mapping=mapping)
    return {"subject_hash": approval_service.submit_subject_hash(
                app_id, signature, fill_mapping_hash),
            "mapping_hash": fill_mapping_hash, "mapping": mapping,
            "signature": signature}


async def arm_submit_binding(store, app_id, signature="sha256:sig",
                             mapping=None, founder_id="founder",
                             session_id="session-1", grant=True,
                             subject_hash=None):
    """Put an application at the submit gate the way a real fill leaves it:
    bound fill report + the matching `submit_application` approval.

    `subject_hash` overrides the derived one to model a stale or legacy grant.
    """
    from services import firestore

    bound = await bind_fill_report(store, app_id, signature, mapping)
    approval_id = await firestore.create_approval(
        app_id, "submit_application", 30, founder_id=founder_id,
        session_id=session_id,
        subject_hash=bound["subject_hash"] if subject_hash is None
        else subject_hash)
    if grant:
        await firestore.grant_approval(approval_id, founder_id)
    return {**bound, "approval_id": approval_id}


class FakeStore:
    def __init__(self):
        self.opportunities = {}
        self.applications = {}
        self.profiles = {}
        self.profile_update_receipts = {}
        self.feedback = {}
        self.approvals = {}
        self.audit = []
        self.ingestions = {}
        self.artifacts = {}
        self.artifact_chunks = {}
        self.documents = {}
        self.source_hashes = {}
        self.portal_registrations = {}
        self.evidence_checks = {}
        self.discovery_requests = {}
        self.resource_index = {}
        self.session_resource_links = {}
        self.session_catalog = {}
        self.data_connections = {}
        self.source_grants = {}
        self.external_events = {}
        self.founder_inbox = {}
        self.external_actions = {}
        self.integrations = {}
        self.processed_gmail_ids = []
        self.processed_alex_ids = []

    def _now(self):
        return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()

    async def _get_profile(founder_id):
        return store.profiles.get(founder_id, {
            "version": 0, "facts": {}, "voice_rules": [],
            "fact_provenance": {}, "fact_history": [], "canonical_answers": [],
            "rejection_history": [], "decision_patterns": []})

    async def _apply_profile_update(founder_id, kind, payload, evidence,
                                    idempotency_key=None, *,
                                    verification_level="FOUNDER_CONFIRMED",
                                    provenance=None):
        receipt_key = (founder_id, idempotency_key)
        if idempotency_key and receipt_key in store.profile_update_receipts:
            return store.profile_update_receipts[receipt_key]
        profile = await _get_profile(founder_id)
        profile = {**profile}
        col = {"voice_rule": "voice_rules", "canonical_answer_update": "canonical_answers",
               "fact_update": "facts", "decision_pattern": "decision_patterns"}[kind]
        if col == "facts":
            facts = {**profile.get("facts", {})}
            fact_provenance = {**profile.get("fact_provenance", {})}
            history = list(profile.get("fact_history", []))
            for key, value in payload.items():
                if key in facts and facts[key] != value:
                    history.append({"key": key, "value": facts[key],
                                    **fact_provenance.get(key, {}),
                                    "verification_level": "SUPERSEDED"})
                facts[key] = value
                fact_provenance[key] = {
                    "source": kind, "evidence": evidence,
                    "verification_level": verification_level,
                    **(provenance or {}),
                }
            profile["facts"] = facts
            profile["fact_provenance"] = fact_provenance
            profile["fact_history"] = history
        else:
            entry = {**payload, "evidence": evidence, "id": uuid.uuid4().hex[:12],
                     "created_at": store._now(), "active": True,
                     "verification_level": verification_level,
                     **(provenance or {})}
            profile[col] = [*profile.get(col, []), entry]
        profile["version"] = profile.get("version", 0) + 1
        store.profiles[founder_id] = profile
        if idempotency_key:
            store.profile_update_receipts[receipt_key] = profile["version"]
        store.audit.append({"id": uuid.uuid4().hex, "actor": "agent:distiller",
                            "action": "profile_update", "target": f"profiles/{founder_id}",
                            "result": "success", "detail": f"{kind}: {evidence[:200]}",
                            "idempotency_key": idempotency_key,
                            "created_at": store._now()})
        return profile["version"]

    async def _audit(actor, action, target, result, detail="", idempotency_key=None):
        row = {"id": uuid.uuid4().hex, "actor": actor, "action": action, "target": target,
               "result": result, "detail": detail, "idempotency_key": idempotency_key,
               "created_at": store._now()}
        store.audit.append(row)
        return row["id"]

    async def _create_feedback(founder_id, application_id, section_id, feedback_type,
                               original, reason, edited_text, section_key=""):
        fid = uuid.uuid4().hex
        store.feedback[fid] = {"id": fid, "founder_id": founder_id,
                               "application_id": application_id, "section_id": section_id,
                               "section_key": section_key,
                               "type": feedback_type, "original": original, "reason": reason,
                               "edited_text": edited_text, "distilled": False,
                               "distilled_rule_ids": [], "created_at": store._now()}
        return fid

    async def _get_feedback(fid):
        return store.feedback.get(fid)

    async def _mark_distilled(fid, rule_ids):
        store.feedback[fid]["distilled"] = True
        store.feedback[fid]["distilled_rule_ids"] = rule_ids

    async def _find_application_by_founder_opportunity(founder_id, opportunity_id):
        matches = [app for app in store.applications.values()
                   if app["founder_id"] == founder_id
                   and app["opportunity_id"] == opportunity_id]
        return matches[0] if matches else None

    async def _get_or_create_application(founder_id, opportunity_id, checklist):
        import hashlib

        aid = "app_" + hashlib.sha256(
            f"{founder_id}:{opportunity_id}".encode()).hexdigest()[:28]
        existing = store.applications.get(aid)
        if existing:
            return {"application_id": aid, "created": False,
                    "application": existing}
        record = {"id": aid, "founder_id": founder_id,
                  "opportunity_id": opportunity_id, "state": "INTERVIEWING",
                  "checklist": checklist, "interview_qa": [],
                  "draft_sections": [], "form_fill_report": None,
                  "submit_idempotency_key": None, "submission": None,
                  "followups": [], "created_at": store._now(),
                  "updated_at": store._now()}
        store.applications[aid] = record
        return {"application_id": aid, "created": True,
                "application": record}

    async def _create_application(founder_id, opportunity_id, checklist):
        return (await _get_or_create_application(
            founder_id, opportunity_id, checklist))["application_id"]

    async def _get_application(aid):
        return store.applications.get(aid)

    async def _append_application_followup(aid, entry, dedupe_key=""):
        app = store.applications.get(aid)
        if not app:
            return {"status": "error", "error": True,
                    "message": f"application {aid} not found"}
        followups = list(app.get("followups") or [])
        if dedupe_key and any(
                str(item.get("external_event_id") or "") == dedupe_key
                for item in followups if isinstance(item, dict)):
            return {"status": "success", "duplicate": True,
                    "followups": len(followups)}
        followups.append(entry)
        app["followups"] = followups
        app["updated_at"] = store._now()
        return {"status": "success", "duplicate": False,
                "followups": len(followups)}

    async def _update_application(aid, **fields):
        store.applications[aid].update(fields, updated_at=store._now())

    async def _record_interview_answer(founder_id, application_id, question_key,
                                       question, answer):
        app = store.applications.get(application_id)
        if not app:
            return {"status": "error", "error": True,
                    "message": "no active application"}
        if app.get("founder_id") != founder_id:
            return {"status": "error", "error": True,
                    "message": "application does not belong to this founder"}
        if app.get("state") != "INTERVIEWING":
            return {"status": "error", "error": True,
                    "message": "interview answers can only be recorded in INTERVIEWING"}
        qa = app.setdefault("interview_qa", [])
        if any(row.get("question_key") == question_key
               and row.get("question") == question and row.get("answer") == answer
               for row in qa):
            return {"status": "success", "application_id": application_id,
                    "question_key": question_key, "already_recorded": True}
        qa.append({"question_key": question_key, "question": question,
                   "answer": answer, "source": "founder_turn",
                   "recorded_at": store._now()})
        profile = dict(await _get_profile(founder_id))
        facts = {**profile.get("facts", {})}
        provenance = {**profile.get("fact_provenance", {})}
        history = list(profile.get("fact_history", []))
        if question_key in facts and facts[question_key] != answer:
            history.append({"key": question_key, "value": facts[question_key],
                            **provenance.get(question_key, {}),
                            "verification_level": "SUPERSEDED"})
        profile["facts"] = {**profile.get("facts", {}), question_key: answer}
        profile["fact_provenance"] = {
            **provenance,
            question_key: {"source": "interview_answer",
                           "application_id": application_id,
                           "question": question, "recorded_at": store._now(),
                           "verification_level": "FOUNDER_CONFIRMED",
                           "source_available": True},
        }
        profile["fact_history"] = history
        profile["version"] = profile.get("version", 0) + 1
        store.profiles[founder_id] = profile
        return {"status": "success", "application_id": application_id,
                "question_key": question_key,
                "profile_version": profile["version"], "already_recorded": False}

    async def _guarded_transition(aid, expected_state, to_state, **fields):
        app = store.applications.get(aid)
        if not app:
            return {"status": "error", "error": True,
                    "message": f"application {aid} not found"}
        if app["state"] != expected_state:
            return {"status": "error", "error": True,
                    "message": "application changed concurrently",
                    "current_step": app["state"]}
        app.update(fields, state=to_state, updated_at=store._now())
        return {"status": "success", "from_step": expected_state,
                "current_step": to_state}

    async def _update_draft_section(aid, founder_id, section_id, status,
                                    edited_text=""):
        app = store.applications.get(aid)
        if not app:
            return {"status": "error", "error": True,
                    "message": f"application {aid} not found"}
        if app.get("founder_id") != founder_id:
            return {"status": "error", "error": True,
                    "message": "application does not belong to this founder"}
        if app.get("state") != "AWAITING_REVIEW":
            return {"status": "error", "error": True,
                    "message": "section feedback is accepted only in AWAITING_REVIEW"}
        sections = [dict(section) for section in app.get("draft_sections", [])]
        section = next((item for item in sections
                        if item.get("section_id") == section_id), None)
        if section is None:
            return {"status": "error", "error": True,
                    "message": f"section {section_id} not found in application"}
        original = section.get("content", "")
        section["status"] = status
        if edited_text:
            section["content"] = edited_text
        app["draft_sections"] = sections
        app["updated_at"] = store._now()
        return {"status": "success", "state": app["state"],
                "section": section, "original": original, "sections": sections}

    async def _create_approval(application_id, gate, ttl_minutes, details=None,
                               founder_id="", session_id="", subject_hash=""):
        from datetime import timedelta
        aid = uuid.uuid4().hex
        expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        store.approvals[aid] = {"id": aid, "application_id": application_id, "gate": gate,
                                "founder_id": founder_id, "session_id": session_id,
                                "details": details or {},
                                "subject_hash": subject_hash or None,
                                "token": None, "status": "PENDING",
                                "expires_at": expires.isoformat(), "granted_by": None,
                                "consumed_at": None, "created_at": store._now()}
        return aid

    async def _list_pending_approvals(founder_id="", session_id=""):
        now = store._now()
        return sorted((a for a in store.approvals.values()
                       if (a["status"] == "PENDING" and a["expires_at"] > now
                           and (not founder_id or a.get("founder_id") == founder_id)
                           and (not session_id or a.get("session_id") == session_id))),
                      key=lambda a: a.get("created_at", ""), reverse=True)

    async def _get_approval(aid):
        return store.approvals.get(aid)

    async def _grant_approval(aid, founder_id):
        store.approvals[aid].update(token=uuid.uuid4().hex, status="GRANTED",
                                    granted_by=founder_id)

    async def _deny_approval(aid):
        store.approvals[aid]["status"] = "DENIED"

    async def _find_valid_approval(application_id, gate="", founder_id="", session_id="",
                                   subject_hash=None):
        now = store._now()
        for a in store.approvals.values():
            if (a["application_id"] == application_id and a["status"] == "GRANTED"
                    and a["expires_at"] > now
                    and (not gate or a.get("gate") == gate)
                    and (not founder_id or a.get("founder_id") == founder_id)
                    and (not session_id or a.get("session_id") == session_id)
                    and (subject_hash is None
                         or (a.get("subject_hash") or "") == subject_hash)):
                return a
        return None

    async def _expire_stale_approvals(application_id, gate, subject_hash=""):
        expired = []
        for aid, a in store.approvals.items():
            if a["application_id"] != application_id or a.get("gate") != gate:
                continue
            if a["status"] not in ("PENDING", "GRANTED"):
                continue
            if subject_hash and (a.get("subject_hash") or "") == subject_hash:
                continue
            a.update(status="EXPIRED", token=None)
            expired.append(aid)
        return expired

    async def _find_pending_approval(application_id, gate="", session_id="",
                                     founder_id=""):
        for a in store.approvals.values():
            if (a["application_id"] == application_id and a["status"] == "PENDING"
                    and a["expires_at"] > store._now()
                    and (not gate or a.get("gate") == gate)
                    and (not session_id or a.get("session_id") == session_id)
                    and (not founder_id or a.get("founder_id") == founder_id)):
                return a
        return None

    async def _consume_approval(aid):
        store.approvals[aid].update(status="CONSUMED", consumed_at=store._now())

    async def _claim_approval(aid):
        approval = store.approvals.get(aid)
        if not approval or approval["status"] != "GRANTED" \
                or approval["expires_at"] <= store._now():
            return False
        await _consume_approval(aid)
        return True

    async def _find_successful_action(key):
        for row in store.audit:
            if row["idempotency_key"] == key and row["result"] == "success":
                return row
        return None

    async def _create_ingestion(founder_id, source_type, source_ref, artifact, proposed_updates):
        iid = uuid.uuid4().hex
        store.ingestions[iid] = {"id": iid, "founder_id": founder_id, "source_type": source_type,
                                 "source_ref": source_ref, "artifact": artifact,
                                 "status": "EXTRACTED", "proposed_updates": proposed_updates,
                                 "confirmed_at": None, "created_at": store._now()}
        return iid

    async def _get_ingestion(iid):
        return store.ingestions.get(iid)

    async def _update_ingestion(iid, **fields):
        store.ingestions[iid].update(fields)

    async def _register_artifact_ingestion(**fields):
        fields["authority"] = ("profile_candidate" if fields.get("scope") == "profile"
                               else "reference_only")
        iid = fields.get("document_id") or uuid.uuid4().hex
        existing = store.ingestions.get(iid)
        if existing:
            identity = ("founder_id", "session_id", "scope", "source_type", "source_ref",
                        "source_grant_id", "sha256", "occurrence_key")
            if any(existing.get(key) != fields.get(key) for key in identity):
                raise ValueError("ingestion occurrence conflicts with existing receipt")
            return iid
        now = store._now()
        store.artifacts[iid] = {
            "id": iid, "founder_id": fields["founder_id"],
            "session_id": fields["session_id"], "scope": fields["scope"],
            "source_type": fields["source_type"],
            "source_ref": fields["source_ref"], "storage_name": fields["storage_name"],
            "artifact": fields["storage_name"],
            "declared_content_type": fields["declared_content_type"],
            "detected_content_type": fields["detected_content_type"],
            "detected_extension": fields["detected_extension"],
            "size_bytes": fields["size_bytes"], "sha256": fields["sha256"],
            "connection_id": fields.get("connection_id"),
            "source_grant_id": fields.get("source_grant_id"),
            "provider_source_id": fields.get("provider_source_id"),
            "provider_version": fields.get("provider_version"),
            "provider_modified_at": fields.get("provider_modified_at"),
            "provider_content_type": fields.get("provider_content_type"),
            "authority": fields.get("authority", "reference_only"),
            "occurrence_key": fields.get("occurrence_key"),
            "provenance_status": "PENDING",
            "status": "QUEUED", "index_generation": "",
            "created_at": now, "updated_at": now,
        }
        store.ingestions[iid] = {
            "id": iid, "artifact_id": iid, "founder_id": fields["founder_id"],
            "session_id": fields["session_id"], "scope": fields["scope"],
            "source_type": fields["source_type"], "source_ref": fields["source_ref"],
            "artifact": fields["storage_name"], "content_type": fields["detected_content_type"],
            "size_bytes": fields["size_bytes"], "sha256": fields["sha256"],
            "connection_id": fields.get("connection_id"),
            "source_grant_id": fields.get("source_grant_id"),
            "provider_source_id": fields.get("provider_source_id"),
            "provider_version": fields.get("provider_version"),
            "provider_modified_at": fields.get("provider_modified_at"),
            "authority": fields.get("authority", "reference_only"),
            "occurrence_key": fields.get("occurrence_key"),
            "provenance_status": "PENDING",
            "status": "QUEUED", "proposed_updates": [], "auto_applied": 0,
            "needs_founder_count": 0, "chunk_count": 0, "created_at": now,
        }
        store.audit.append({
            "id": uuid.uuid4().hex, "actor": f"founder:{fields['founder_id']}",
            "action": "register_attachment", "target": f"artifacts/{iid}",
            "result": "success", "detail": f"scope={fields['scope']}",
            "idempotency_key": iid, "created_at": now,
        })
        return iid

    async def _get_artifact(iid):
        return store.artifacts.get(iid)

    async def _update_artifact(iid, **fields):
        store.artifacts[iid].update(fields)

    async def _replace_artifact_chunks(iid, chunks):
        generation = hashlib.sha256("|".join(
            f"{int(chunk.get('ordinal', 0))}:{chunk.get('content_sha256', '')}"
            for chunk in chunks).encode()).hexdigest()[:20]
        stored = []
        for chunk in chunks:
            cid = f"chunk_{int(chunk.get('ordinal', 0)):04d}_{chunk['content_sha256'][:16]}"
            chunk["id"] = cid
            stored.append({**chunk, "id": cid, "artifact_id": iid,
                           "generation": generation})
        store.artifact_chunks[iid] = stored
        store.artifacts[iid]["index_generation"] = generation

    async def _list_artifact_chunks(iid, limit=400):
        return list(store.artifact_chunks.get(iid, []))[:limit]

    async def _claim_ingestion(iid, lease_owner="", lease_seconds=900):
        ingestion = store.ingestions.get(iid)
        if not ingestion:
            return {"status": "error", "error": True, "message": "ingestion not found"}
        if ingestion.get("status") in {
            "READY", "NEEDS_FOUNDER", "CONFIRMED", "NO_TEXT", "UNSUPPORTED", "FAILED"
        }:
            return {"status": "success", "duplicate": True,
                    "ingestion_status": ingestion["status"]}
        if ingestion.get("lease_owner"):
            return {"status": "success", "in_progress": True}
        owner = lease_owner or uuid.uuid4().hex
        ingestion.update(status="VALIDATING", lease_owner=owner,
                         attempt=int(ingestion.get("attempt") or 0) + 1)
        store.artifacts[iid]["status"] = "VALIDATING"
        return {"status": "success", "claimed": True, "lease_owner": owner}

    async def _set_ingestion_stage(iid, lease_owner, status):
        ingestion = store.ingestions.get(iid)
        if not ingestion or ingestion.get("lease_owner") != lease_owner:
            return False
        ingestion["status"] = status
        store.artifacts[iid]["status"] = status
        return True

    async def _update_ingestion_leased(iid, lease_owner, **fields):
        ingestion = store.ingestions.get(iid)
        if not ingestion or ingestion.get("lease_owner") != lease_owner:
            return False
        ingestion.update(fields)
        return True

    async def _finish_ingestion(iid, lease_owner, status, **fields):
        ingestion = store.ingestions.get(iid)
        if not ingestion or ingestion.get("lease_owner") != lease_owner:
            return False
        ingestion.update(fields, status=status, lease_owner=None)
        store.artifacts[iid].update(
            {key: value for key, value in fields.items()
             if key in {"chunk_count", "error_code", "message"}})
        store.artifacts[iid]["status"] = status
        store.audit.append({
            "id": uuid.uuid4().hex, "actor": "agent:document_ingestion",
            "action": "ingest_document", "target": f"artifacts/{iid}",
            "result": "success" if status in {"READY", "NEEDS_FOUNDER", "CONFIRMED"} else "error",
            "detail": f"terminal_status={status}", "idempotency_key": iid,
            "created_at": store._now(),
        })
        return True

    async def _retry_ingestion(iid, lease_owner, error_code, message, max_attempts=3):
        ingestion = store.ingestions.get(iid)
        if not ingestion or ingestion.get("lease_owner") != lease_owner:
            return {"status": "error", "error": True, "message": "ingestion lease changed"}
        attempt = int(ingestion.get("attempt") or 1)
        terminal = attempt >= max_attempts
        status = "FAILED" if terminal else "QUEUED"
        ingestion.update(status=status, lease_owner=None, error_code=error_code, message=message)
        store.artifacts[iid].update(status=status, error_code=error_code, message=message)
        store.audit.append({
            "id": uuid.uuid4().hex, "actor": "agent:document_ingestion",
            "action": "ingest_document_attempt", "target": f"artifacts/{iid}",
            "result": "error" if terminal else "retrying",
            "detail": f"attempt={attempt}; error_code={error_code}",
            "idempotency_key": f"{iid}:{attempt}", "created_at": store._now(),
        })
        return {"status": "success", "retryable": not terminal,
                "ingestion_status": status, "attempt": attempt}

    async def _get_opportunity(oid):
        return store.opportunities.get(oid)

    async def _get_evidence_check(report_id, founder_id, application_id):
        row = store.evidence_checks.get(report_id)
        if not row or row.get("founder_id") != founder_id \
                or row.get("application_id") != application_id:
            return None
        return row

    async def _claim_evidence_check(report_id, row, lease_seconds):
        now = datetime.now(timezone.utc).timestamp()
        current = store.evidence_checks.get(report_id)
        if current:
            if current.get("execution_status") == "COMPLETE":
                return {"claimed": False, "existing": current}
            age = now - float(current.get("lease_started_epoch") or 0)
            if age <= float(current.get("lease_seconds") or lease_seconds):
                return {"claimed": False, "existing": None}
        owner = uuid.uuid4().hex
        store.evidence_checks[report_id] = {
            **row, "report_id": report_id, "execution_status": "PREPARED",
            "lease_owner": owner, "lease_started_epoch": now,
            "lease_seconds": lease_seconds,
        }
        return {"claimed": True, "existing": None, "lease_owner": owner}

    async def _complete_evidence_check(report_id, report, lease_owner, application_id):
        current = store.evidence_checks.get(report_id)
        if not current or current.get("execution_status") == "COMPLETE" \
                or current.get("lease_owner") != lease_owner:
            return False
        store.evidence_checks[report_id] = {
            **report, "report_id": report_id, "execution_status": "COMPLETE"}
        store.applications[application_id].update(
            latest_evidence_check_id=report_id, updated_at=store._now())
        return True

    async def _create_opportunity(record):
        for oid, opp in store.opportunities.items():
            if opp.get("dedup_hash") == record.get("dedup_hash"):
                return oid
        oid = uuid.uuid4().hex
        store.opportunities[oid] = {**record, "id": oid, "state": "DISCOVERED",
                                    "created_at": store._now(), "updated_at": store._now()}
        return oid

    async def _set_opportunity_state(oid, state, **fields):
        store.opportunities[oid].update({"state": state, **fields, "updated_at": store._now()})

    async def _list_opportunities(limit=40, start_after=None):
        rows = sorted(store.opportunities.values(),
                      key=lambda o: o.get("created_at", ""), reverse=True)
        if start_after is not None:
            rows = [o for o in rows if o.get("created_at", "") < start_after]
        return rows[:limit]

    async def _list_unscored(limit=10):
        return [o for o in store.opportunities.values() if o.get("state") == "DISCOVERED"][:limit]

    async def _find_by_hash(digest):
        for oid, opp in store.opportunities.items():
            if opp.get("dedup_hash") == digest:
                return oid
        return None

    async def _create_document_record(founder_id, artifact_name, kind, title,
                                      session_id, application_id, opportunity_name,
                                      spec_hash, version, doc_key):
        did = uuid.uuid4().hex
        store.documents[did] = {
            "id": did, "founder_id": founder_id, "artifact_name": artifact_name,
            "kind": kind, "title": title, "session_id": session_id,
            "application_id": application_id, "opportunity_name": opportunity_name,
            "spec_hash": spec_hash, "version": version, "doc_key": doc_key,
            "created_at": store._now()}
        return did

    async def _list_documents(founder_id, session_id=None, application_id=None):
        docs = [d for d in store.documents.values() if d["founder_id"] == founder_id]
        if session_id:
            docs = [d for d in docs if d["session_id"] == session_id]
        if application_id:
            docs = [d for d in docs if d["application_id"] == application_id]
        return sorted(docs, key=lambda d: d.get("created_at", ""), reverse=True)

    async def _get_document_by_artifact(founder_id, artifact_name):
        return next((d for d in store.documents.values()
                     if d.get("founder_id") == founder_id
                     and d.get("artifact_name") == artifact_name), None)

    async def _next_document_version(founder_id, doc_key):
        return sum(1 for d in store.documents.values()
                   if d["founder_id"] == founder_id and d.get("doc_key") == doc_key) + 1

    async def _get_source_hash(url):
        return store.source_hashes.get(url)

    async def _set_source_hash(url, content_hash):
        store.source_hashes[url] = content_hash

    async def _save_pending_portal_registration(host, founder_id, session_id,
                                                portal_url, email,
                                                application_id=""):
        store.portal_registrations[host] = {
            "host": host, "founder_id": founder_id, "session_id": session_id,
            "application_id": application_id,
            "portal_url": portal_url, "email": email, "status": "PENDING",
        }

    async def _list_pending_portal_registrations(founder_id):
        return [record for record in store.portal_registrations.values()
                if record["founder_id"] == founder_id
                and record["status"] == "PENDING"]

    async def _complete_portal_registration(host):
        if host in store.portal_registrations:
            store.portal_registrations[host]["status"] = "COMPLETED"

    def _receipt_key(founder_id, request_id):
        import hashlib as _hashlib
        return _hashlib.sha256(
            f"{founder_id}:{request_id}".encode()).hexdigest()[:32]

    async def _claim_discovery_request(request_id, founder_id, context_hash,
                                       lease_seconds=900):
        key = _receipt_key(founder_id, request_id)
        current = store.discovery_requests.get(key)
        if current:
            if current["context_hash"] != context_hash:
                return {"claimed": False, "conflict": True,
                        "status": current["status"]}
            if current["status"] == "COMPLETE":
                return {"claimed": False, "duplicate": True, "status": "COMPLETE"}
            if current["status"] == "RUNNING":
                return {"claimed": False, "in_progress": True, "status": "RUNNING"}
        owner = uuid.uuid4().hex
        # Merge-safe claim, mirroring production: ACCEPTED-boundary fields
        # survive the transition to RUNNING.
        preserved = {k: current[k] for k in (
            "origin_session_id", "origin_message_id", "display_query",
            "context", "executed_queries", "result_opportunity_ids",
            "resource_id", "dispatch_status", "dispatch_error") if current and k in current}
        store.discovery_requests[key] = {
            **preserved,
            "request_id": request_id, "founder_id": founder_id,
            "context_hash": context_hash, "status": "RUNNING",
            "lease_owner": owner, "lease_seconds": lease_seconds,
        }
        return {"claimed": True, "lease_owner": owner, "status": "RUNNING",
                "receipt": {**preserved, "request_id": request_id}}

    async def _finish_discovery_request(request_id, founder_id, lease_owner,
                                        status, summary=None):
        key = _receipt_key(founder_id, request_id)
        current = store.discovery_requests.get(key)
        if not current or current["lease_owner"] != lease_owner:
            return False
        current.update(status=status, summary=summary or {})
        return True

    async def _create_discovery_receipt(request_id, founder_id, context_hash,
                                        *, origin_session_id, display_query,
                                        context="", origin_message_id=None):
        key = _receipt_key(founder_id, request_id)
        current = store.discovery_requests.get(key)
        if current:
            if current["context_hash"] != context_hash:
                return {"accepted": False, "conflict": True,
                        "discovery_request_id": key,
                        "status": current.get("status", "UNKNOWN")}
            return {"accepted": True, "duplicate": True,
                    "discovery_request_id": key,
                    "status": current.get("status", "ACCEPTED"),
                    "resource_id": current.get("resource_id", "")}
        store.discovery_requests[key] = {
            "request_id": request_id, "founder_id": founder_id,
            "context_hash": context_hash, "status": "ACCEPTED",
            "origin_session_id": origin_session_id,
            "origin_message_id": origin_message_id,
            "display_query": display_query,
            "context": context,
            "executed_queries": [], "result_opportunity_ids": [],
            "resource_id": "", "dispatch_status": "pending",
            "dispatch_error": "", "lease_owner": "",
            "created_at": store._now(), "updated_at": store._now(),
        }
        return {"accepted": True, "duplicate": False,
                "discovery_request_id": key, "status": "ACCEPTED",
                "resource_id": ""}

    async def _get_discovery_request(request_id, founder_id):
        key = _receipt_key(founder_id, request_id)
        row = store.discovery_requests.get(key)
        return ({**row, "id": key}) if row else None

    async def _create_voice_note_artifact(founder_id, session_id, storage_name,
                                          *, content_type="audio/webm",
                                          size_bytes=0, transcript_preview=""):
        doc_id = uuid.uuid4().hex
        store.artifacts[doc_id] = {
            "founder_id": founder_id, "session_id": session_id,
            "scope": "reference_only", "source_type": "voice_note",
            "source_ref": "Voice note", "storage_name": storage_name,
            "artifact": storage_name, "detected_content_type": content_type,
            "size_bytes": size_bytes, "status": "READY",
            "transcript_preview": transcript_preview[:240],
            "retention_policy": "session", "created_at": store._now()}
        return doc_id

    async def _list_active_discovery_requests(founder_id, *, limit=10):
        rows = [{**r, "id": k} for k, r in store.discovery_requests.items()
                if r.get("founder_id") == founder_id
                and str(r.get("status") or "") in ("ACCEPTED", "RUNNING")]
        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return rows[:limit]

    async def _get_discovery_request_by_id(discovery_request_id):
        row = store.discovery_requests.get(discovery_request_id)
        return ({**row, "id": discovery_request_id}) if row else None

    async def _update_discovery_receipt(request_id, founder_id, fields):
        key = _receipt_key(founder_id, request_id)
        row = store.discovery_requests.get(key)
        if row is None:
            return False
        for k, v in fields.items():
            if k in ("dispatch_status", "dispatch_error", "resource_id",
                     "executed_queries", "result_opportunity_ids"):
                row[k] = v
        row["updated_at"] = store._now()
        return True

    # ---- session-resource projections (docs/23) -------------------------
    # Query-capable fakes: these mirror the REAL semantics (equality + one
    # array-contains + immutable-keyset order + start_after + tombstone
    # exclusion) so search/pagination tests exercise actual filter logic.

    def _keyset_after(rows, order_field, id_field, start_after):
        if not start_after:
            return rows
        key = (start_after[0], start_after[1])
        return [r for r in rows
                if (r.get(order_field, ""), r.get(id_field, "")) < key]

    async def _upsert_resource_and_link(resource, link):
        existing = store.session_resource_links.get(link["link_id"])
        if existing is not None:
            return {"resource_created": False, "link_created": False,
                    "replayed": True,
                    "tombstoned": bool(existing.get("deleted_at"))}
        now = store._now()
        row = store.resource_index.get(resource["resource_id"])
        if row is not None:
            for k in ("title", "summary", "status", "visibility",
                      "search_terms", "search_prefixes",
                      "representation_refs", "content_hash"):
                if k in resource:
                    row[k] = resource[k]
            row["updated_at"] = now
            created = False
        else:
            store.resource_index[resource["resource_id"]] = {
                **resource, "created_at": now, "updated_at": now}
            created = True
        store.session_resource_links[link["link_id"]] = {
            **link, "occurred_at": link.get("occurred_at") or now,
            "updated_at": now, "deleted_at": None}
        return {"resource_created": created, "link_created": True,
                "replayed": False, "tombstoned": False}

    async def _update_resource_projection(resource_id, fields):
        row = store.resource_index.get(resource_id)
        if row is None:
            return False
        for k, v in fields.items():
            if k in ("title", "summary", "status", "visibility",
                     "search_terms", "search_prefixes",
                     "representation_refs", "content_hash"):
                row[k] = v
        row["updated_at"] = store._now()
        return True

    async def _get_resource(resource_id):
        row = store.resource_index.get(resource_id)
        return ({**row, "id": resource_id}) if row else None

    async def _tombstone_session_links(founder_id, session_id):
        count = 0
        for row in store.session_resource_links.values():
            if (row["founder_id"] == founder_id
                    and row["session_id"] == session_id
                    and not row.get("deleted_at")):
                row["deleted_at"] = store._now()
                count += 1
        return count

    async def _upsert_session_catalog(session_id, fields):
        now = store._now()
        row = store.session_catalog.get(session_id)
        if row is not None:
            row.update(fields)
            row["updated_at"] = now
        else:
            store.session_catalog[session_id] = {
                "schema_version": 1, "session_id": session_id,
                "status": "active", "message_count": 0, "resource_count": 0,
                "resource_types": [], "search_terms": [],
                "search_prefixes": [], **fields,
                "created_at": now, "updated_at": now}

    async def _get_session_catalog(session_id):
        return store.session_catalog.get(session_id)

    async def _bump_session_catalog_resources(session_id, resource_type,
                                              count_delta=1):
        row = store.session_catalog.get(session_id)
        if row is None:
            return
        types = list(row.get("resource_types") or [])
        if resource_type not in types and len(types) < 16:
            types.append(resource_type)
        row["resource_count"] = int(row.get("resource_count") or 0) + count_delta
        row["resource_types"] = types
        row["updated_at"] = store._now()

    def _live_links(founder_id):
        return [{**r, "id": lid}
                for lid, r in store.session_resource_links.items()
                if r["founder_id"] == founder_id and not r.get("deleted_at")]

    async def _search_session_links(founder_id, prefix, *, resource_type=None,
                                    session_id=None, limit=50,
                                    start_after=None):
        # Mirrors the production array_contains query exactly: prefixes
        # only. Never make the fake more permissive than Firestore.
        rows = [r for r in _live_links(founder_id)
                if prefix in (r.get("search_prefixes") or [])]
        if resource_type:
            rows = [r for r in rows if r.get("resource_type") == resource_type]
        if session_id:
            rows = [r for r in rows if r.get("session_id") == session_id]
        rows.sort(key=lambda r: (r.get("occurred_at", ""),
                                 r.get("link_id", "")), reverse=True)
        return _keyset_after(rows, "occurred_at", "link_id",
                             start_after)[:limit]

    async def _list_session_links(founder_id, session_id, *, limit=100,
                                  start_after=None):
        rows = [r for r in _live_links(founder_id)
                if r.get("session_id") == session_id]
        rows.sort(key=lambda r: (r.get("occurred_at", ""),
                                 r.get("link_id", "")), reverse=True)
        return _keyset_after(rows, "occurred_at", "link_id",
                             start_after)[:limit]

    async def _list_resource_links(founder_id, resource_id, *, limit=50):
        rows = [r for r in _live_links(founder_id)
                if r.get("resource_id") == resource_id]
        rows.sort(key=lambda r: (r.get("occurred_at", ""),
                                 r.get("link_id", "")), reverse=True)
        return rows[:limit]

    async def _search_resources(founder_id, prefix, *, visibility="primary",
                                resource_type=None, limit=50,
                                start_after=None):
        rows = [{**r, "id": rid} for rid, r in store.resource_index.items()
                if r["founder_id"] == founder_id
                and r.get("visibility") == visibility
                and prefix in (r.get("search_prefixes") or [])]
        if resource_type:
            rows = [r for r in rows if r.get("resource_type") == resource_type]
        rows.sort(key=lambda r: (r.get("created_at", ""),
                                 r.get("resource_id", "")), reverse=True)
        return _keyset_after(rows, "created_at", "resource_id",
                             start_after)[:limit]

    async def _search_session_catalog(founder_id, prefix, *, limit=50,
                                      start_after=None):
        rows = [{**r, "id": sid} for sid, r in store.session_catalog.items()
                if r.get("founder_id") == founder_id
                and r.get("status", "active") == "active"
                and prefix in (r.get("search_prefixes") or [])]
        rows.sort(key=lambda r: (r.get("created_at", ""),
                                 r.get("session_id", "")), reverse=True)
        return _keyset_after(rows, "created_at", "session_id",
                             start_after)[:limit]

    async def _list_recent_session_catalog(founder_id, *, limit=30):
        rows = [{**r, "id": sid} for sid, r in store.session_catalog.items()
                if r.get("founder_id") == founder_id
                and r.get("status", "active") == "active"]
        rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        return rows[:limit]

    async def _list_recent_resources(founder_id, *, visibility="primary",
                                     limit=30):
        rows = [{**r, "id": rid} for rid, r in store.resource_index.items()
                if r["founder_id"] == founder_id
                and r.get("visibility") == visibility]
        rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        return rows[:limit]

    async def _upsert_data_connection(
            founder_id, connector_id, *, account_ref="default", account_hint="",
            roles=None, auth_kind="google_oauth", credential_ref=None,
            granted_scopes=None, status="CONNECTED", expected_version=None):
        from services import data_source_contracts as dsc

        try:
            connector = dsc.require_closed(connector_id, dsc.ConnectorId)
            auth = dsc.require_closed(auth_kind, dsc.ConnectionAuthKind)
            state = dsc.require_closed(status, dsc.ConnectionStatus)
            contract = dsc.CONNECTOR_REGISTRY[connector]
            closed_roles = [dsc.require_closed(role, dsc.DataSourceRole)
                            for role in (roles or [r.value for r in contract.roles])]
            if not set(closed_roles).issubset(contract.roles):
                raise ValueError("role")
            cid = dsc.data_connection_id(founder_id, connector_id, account_ref)
        except ValueError:
            return {"status": "error", "error": True,
                    "error_code": "invalid_contract",
                    "message": "unknown connection contract"}
        previous = store.data_connections.get(cid, {})
        version = int(previous.get("version") or 0)
        if expected_version is not None and version != expected_version:
            return {"status": "error", "error": True,
                    "error_code": "version_conflict",
                    "message": "connection changed concurrently"}
        now = store._now()
        row = {
            "schema_version": 1, "connection_id": cid,
            "founder_id": founder_id, "connector_id": connector_id,
            "account_ref": account_ref, "account_hint": account_hint,
            "roles": sorted(role.value for role in closed_roles),
            "auth_kind": auth.value, "credential_ref": credential_ref,
            "granted_scopes": sorted(set(granted_scopes or [])),
            "status": state.value,
            "last_verified_at": previous.get("last_verified_at"),
            "last_success_at": previous.get("last_success_at"),
            "last_error_code": previous.get("last_error_code"),
            "last_error_at": previous.get("last_error_at"),
            "disconnected_at": (now if state.value == "DISCONNECTED"
                                else previous.get("disconnected_at")),
            "version": version + 1,
            "created_at": previous.get("created_at") or now, "updated_at": now,
        }
        store.data_connections[cid] = row
        return {"status": "success", "created": not bool(previous), **row}

    async def _get_data_connection(founder_id, connection_id):
        row = store.data_connections.get(connection_id)
        return row if row and row.get("founder_id") == founder_id else None

    async def _list_data_connections(founder_id):
        return [row for row in store.data_connections.values()
                if row.get("founder_id") == founder_id]

    async def _transition_data_connection(
            founder_id, connection_id, *, status=None, expected_version=None,
            verified=False, successful_operation=None, error_code=None,
            disconnect_outcome=None):
        from services import data_source_contracts as dsc

        row = store.data_connections.get(connection_id)
        if not row or row.get("founder_id") != founder_id:
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch",
                    "message": "connection not found"}
        try:
            closed_status = (dsc.require_closed(status, dsc.ConnectionStatus)
                             if status else None)
            closed_error = (dsc.require_closed(error_code, dsc.SafeErrorCode)
                            if error_code else None)
        except ValueError:
            return {"status": "error", "error": True,
                    "error_code": "invalid_contract",
                    "message": "invalid connection transition"}
        version = int(row.get("version") or 0)
        if expected_version is not None and version != expected_version:
            return {"status": "error", "error": True,
                    "error_code": "version_conflict",
                    "message": "connection changed concurrently"}
        if row.get("status") == "DISCONNECTED" and \
                closed_status not in {dsc.ConnectionStatus.CONNECTED,
                                      dsc.ConnectionStatus.DISCONNECTING}:
            return {"status": "error", "error": True,
                    "error_code": "auth_required",
                    "message": "connection is disconnected"}
        now = store._now()
        if closed_status:
            row["status"] = closed_status.value
            row["disconnected_at"] = (now if closed_status.value == "DISCONNECTED"
                                      else None)
        if verified:
            row["last_verified_at"] = now
        if successful_operation:
            row.update(last_success_at=now,
                       last_successful_operation=successful_operation,
                       last_error_code=None, last_error_at=None)
        if closed_error:
            row.update(last_error_code=closed_error.value, last_error_at=now)
        if disconnect_outcome:
            row["disconnect_outcome"] = disconnect_outcome
        row.update(version=version + 1, updated_at=now)
        return {"status": "success", **row}

    async def _revoke_connection_source_grants(founder_id, connection_id):
        if not await _get_data_connection(founder_id, connection_id):
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch",
                    "message": "connection not found"}
        revoked = 0
        for grant in store.source_grants.values():
            if (grant.get("founder_id") == founder_id
                    and grant.get("connection_id") == connection_id
                    and grant.get("status") == "ACTIVE"):
                grant.update(status="REVOKED",
                             revoked_at=grant.get("revoked_at") or store._now(),
                             updated_at=store._now())
                revoked += 1
        return {"status": "success", "revoked_count": revoked}

    async def _create_source_grant(
            founder_id, connection_id, provider_source_id, *, display_name,
            allowed_ingestion_scopes, selected_session_id=None,
            provider_content_type=None, provider_version=None,
            provider_modified_at=None):
        from services import data_source_contracts as dsc

        connection = await _get_data_connection(founder_id, connection_id)
        if (not connection or connection.get("connector_id") != "drive"
                or connection.get("status") not in {"CONNECTED", "DEGRADED"}):
            return {"status": "error", "error": True,
                    "error_code": "source_not_selected",
                    "message": "source connection not found"}
        try:
            scopes = sorted({dsc.require_closed(s, dsc.IngestionScope).value
                             for s in allowed_ingestion_scopes})
            if not scopes:
                raise ValueError("scope")
            gid = dsc.source_grant_id(founder_id, connection_id,
                                      provider_source_id)
        except ValueError:
            return {"status": "error", "error": True,
                    "error_code": "invalid_contract",
                    "message": "invalid source grant"}
        now = store._now()
        created = gid not in store.source_grants
        row = {
            "schema_version": 1, "source_grant_id": gid,
            "founder_id": founder_id, "connection_id": connection_id,
            "connector_id": "drive", "provider_source_id": provider_source_id,
            "display_name": display_name, "source_kind": "file",
            "allowed_ingestion_scopes": scopes,
            "selected_session_id": selected_session_id,
            "provider_content_type": provider_content_type,
            "provider_version": provider_version,
            "provider_modified_at": provider_modified_at, "status": "ACTIVE",
            "selected_by": f"founder:{founder_id}", "selected_at": now,
            "revoked_at": None, "updated_at": now,
        }
        store.source_grants[gid] = row
        return {"status": "success", "created": created, **row}

    async def _get_source_grant(founder_id, source_grant_id):
        row = store.source_grants.get(source_grant_id)
        return row if row and row.get("founder_id") == founder_id else None

    async def _list_source_grants(founder_id, *, connection_id=None):
        rows = [row for row in store.source_grants.values()
                if row.get("founder_id") == founder_id]
        return [row for row in rows
                if not connection_id or row.get("connection_id") == connection_id]

    async def _revoke_source_grant(founder_id, source_grant_id):
        row = await _get_source_grant(founder_id, source_grant_id)
        if not row:
            return {"status": "error", "error": True,
                    "error_code": "source_not_selected",
                    "message": "source grant not found"}
        duplicate = row.get("status") == "REVOKED"
        row.update(status="REVOKED", revoked_at=row.get("revoked_at") or store._now(),
                   updated_at=store._now())
        return {"status": "success", "duplicate": duplicate,
                "source_grant_id": source_grant_id}

    async def _mark_source_grant_missing(founder_id, source_grant_id):
        row = await _get_source_grant(founder_id, source_grant_id)
        if not row:
            return {"status": "error", "error": True,
                    "error_code": "source_not_selected",
                    "message": "source grant not found"}
        row.update(status="SOURCE_MISSING", updated_at=store._now())
        return {"status": "success", "source_grant_id": source_grant_id,
                "grant_status": "SOURCE_MISSING"}

    async def _create_external_event(
            founder_id, connection_id, connector_id, provider_event_id,
            event_kind, *, payload_hash, provider_thread_id=None,
            source_ref=None, safe_display=None, content_risk="CLEAR",
            occurred_at=None, delivery_status="PENDING"):
        from services import data_source_contracts as dsc

        connection = await _get_data_connection(founder_id, connection_id)
        try:
            dsc.require_closed(event_kind, dsc.ExternalEventKind)
            dsc.require_closed(content_risk, dsc.ContentRisk)
            dsc.require_closed(delivery_status, dsc.DeliveryStatus)
            eid = dsc.external_event_id(founder_id, connection_id,
                                        provider_event_id)
            if not connection or connection.get("connector_id") != connector_id:
                raise ValueError("connection")
        except ValueError:
            return {"status": "error", "error": True,
                    "error_code": "invalid_contract",
                    "message": "invalid external event"}
        if eid in store.external_events:
            return {"status": "success", "duplicate": True,
                    **store.external_events[eid]}
        now = store._now()
        row = {
            "schema_version": 1, "event_id": eid, "founder_id": founder_id,
            "connection_id": connection_id, "connector_id": connector_id,
            "provider_event_id": provider_event_id,
            "provider_thread_id": provider_thread_id, "event_kind": event_kind,
            "payload_hash": payload_hash, "source_ref": source_ref or {},
            "safe_display": safe_display or {}, "content_risk": content_risk,
            "processing_status": "RECEIVED", "lease_owner": None,
            "lease_started_at": None, "lease_seconds": 0,
            "correlation_status": "PENDING", "application_id": None,
            "session_id": None, "resource_id": None, "correlation_basis": None,
            "delivery_status": delivery_status, "effect_ref": None,
            "attempt_count": 0, "received_at": now,
            "occurred_at": occurred_at or now, "updated_at": now,
        }
        store.external_events[eid] = row
        return {"status": "success", "duplicate": False, **row}

    async def _get_external_event(founder_id, event_id):
        row = store.external_events.get(event_id)
        return row if row and row.get("founder_id") == founder_id else None

    async def _list_external_events_by_thread(founder_id, provider_thread_id, *,
                                              limit=20):
        rows = [row for row in store.external_events.values()
                if row.get("founder_id") == founder_id
                and row.get("provider_thread_id") == provider_thread_id]
        return rows[:limit]

    async def _claim_external_event(founder_id, event_id, *, lease_seconds=120,
                                    lease_owner=""):
        row = await _get_external_event(founder_id, event_id)
        if not row:
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch", "message": "event not found"}
        if row.get("processing_status") in {"APPLIED", "INBOXED"}:
            return {"status": "success", "duplicate": True, **row}
        if row.get("lease_owner"):
            return {"status": "success", "in_progress": True,
                    "event_id": event_id}
        owner = lease_owner or uuid.uuid4().hex
        row.update(processing_status="APPLYING", lease_owner=owner,
                   lease_started_at=store._now(), lease_seconds=lease_seconds,
                   attempt_count=int(row.get("attempt_count") or 0) + 1,
                   updated_at=store._now())
        return {"status": "success", "claimed": True,
                "lease_owner": owner, "event_id": event_id}

    async def _create_founder_inbox_item(
            founder_id, event_id, item_kind, *, title, summary,
            candidate_refs=None, lease_owner=""):
        from services import data_source_contracts as dsc

        event = await _get_external_event(founder_id, event_id)
        if not event or (lease_owner and event.get("lease_owner") != lease_owner):
            return {"status": "error", "error": True,
                    "error_code": "lease_conflict", "message": "event not found"}
        try:
            iid = dsc.founder_inbox_id(founder_id, event_id, item_kind)
        except ValueError:
            return {"status": "error", "error": True,
                    "error_code": "invalid_contract", "message": "invalid inbox item"}
        duplicate = iid in store.founder_inbox
        if not duplicate:
            now = store._now()
            store.founder_inbox[iid] = {
                "schema_version": 1, "inbox_item_id": iid,
                "founder_id": founder_id, "event_id": event_id,
                "item_kind": item_kind, "status": "UNREAD", "title": title,
                "summary": summary, "candidate_refs": (candidate_refs or [])[:5],
                "resolved_resource_id": None, "resolved_session_id": None,
                "resolution": None, "created_at": now, "updated_at": now,
                "resolved_at": None,
            }
        event.update(processing_status="INBOXED",
                     correlation_status=("AMBIGUOUS" if item_kind == "AMBIGUOUS_EVENT"
                                         else "UNMATCHED"),
                     delivery_status="NOT_REQUIRED", lease_owner=None,
                     lease_started_at=None, updated_at=store._now())
        return {"status": "success", "duplicate": duplicate,
                **store.founder_inbox[iid]}

    async def _get_founder_inbox_item(founder_id, inbox_item_id):
        row = store.founder_inbox.get(inbox_item_id)
        return row if row and row.get("founder_id") == founder_id else None

    async def _list_founder_inbox(founder_id, *, status="UNREAD", limit=30,
                                  start_after=None):
        rows = [row for row in store.founder_inbox.values()
                if row.get("founder_id") == founder_id
                and row.get("status") == status]
        rows.sort(key=lambda row: (row.get("created_at", ""),
                                   row.get("inbox_item_id", "")), reverse=True)
        if start_after:
            rows = [row for row in rows
                    if (row.get("created_at", ""), row.get("inbox_item_id", ""))
                    < start_after]
        return rows[:limit]

    async def _resolve_founder_inbox_item(
            founder_id, inbox_item_id, *, application_id, resource_id,
            session_id, session_verified=False):
        inbox = await _get_founder_inbox_item(founder_id, inbox_item_id)
        app = store.applications.get(application_id)
        if not inbox or not app or app.get("founder_id") != founder_id \
                or not session_verified:
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch", "message": "item not found"}
        if inbox.get("status") == "RESOLVED":
            if (inbox.get("resolved_resource_id") == resource_id
                    and inbox.get("resolved_session_id") == session_id):
                return {"status": "success", "duplicate": True, **inbox}
            return {"status": "error", "error": True,
                    "error_code": "version_conflict", "message": "already resolved"}
        candidates = inbox.get("candidate_refs") or []
        if candidates and not any(
                row.get("application_id") == application_id
                and row.get("resource_id") == resource_id for row in candidates):
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch", "message": "candidate not allowed"}
        event = await _get_external_event(founder_id, inbox["event_id"])
        if not event:
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch", "message": "item not found"}
        event_id = event["event_id"]
        duplicate = any(row.get("external_event_id") == event_id
                        for row in app.get("followups", []))
        if not duplicate:
            display = event.get("safe_display") or {}
            app.setdefault("followups", []).append({
                "kind": f"email_{event.get('event_kind', 'update')}",
                "due_at": "", "status": "PENDING",
                "note": ("[provider message] " + display.get("title", ""))[:500],
                "source": event.get("connector_id"),
                "external_event_id": event_id,
            })
        effect_ref = f"applications/{application_id}/followups/{event_id}"
        event.update(processing_status="APPLIED", correlation_status="EXACT",
                     correlation_basis="founder_resolution",
                     application_id=application_id, resource_id=resource_id,
                     session_id=session_id, effect_ref=effect_ref,
                     delivery_status="NOT_REQUIRED")
        now = store._now()
        inbox.update(status="RESOLVED", resolved_resource_id=resource_id,
                     resolved_session_id=session_id,
                     resolution="LINKED_TO_APPLICATION", updated_at=now,
                     resolved_at=now)
        return {"status": "success", "duplicate": duplicate,
                "inbox_item_id": inbox_item_id, "effect_ref": effect_ref}

    async def _dismiss_founder_inbox_item(founder_id, inbox_item_id):
        inbox = await _get_founder_inbox_item(founder_id, inbox_item_id)
        if not inbox:
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch", "message": "item not found"}
        if inbox.get("status") == "DISMISSED":
            return {"status": "success", "duplicate": True, **inbox}
        if inbox.get("status") != "UNREAD":
            return {"status": "error", "error": True,
                    "error_code": "version_conflict", "message": "already resolved"}
        inbox.update(status="DISMISSED", resolution="DISMISSED_BY_FOUNDER",
                     updated_at=store._now(), resolved_at=store._now())
        return {"status": "success", "duplicate": False, **inbox}

    async def _apply_external_event_to_application(
            founder_id, event_id, lease_owner, application_id, session_id,
            correlation_basis, followup):
        event = await _get_external_event(founder_id, event_id)
        app = store.applications.get(application_id)
        if (not event or not app or app.get("founder_id") != founder_id
                or event.get("lease_owner") != lease_owner):
            return {"status": "error", "error": True,
                    "error_code": "lease_conflict", "message": "lease changed"}
        if event.get("processing_status") == "APPLIED":
            return {"status": "success", "duplicate": True,
                    "effect_ref": event.get("effect_ref")}
        followups = list(app.get("followups") or [])
        duplicate = any(row.get("external_event_id") == event_id
                        for row in followups)
        if not duplicate:
            app["followups"] = [*followups[-199:],
                                {**followup, "external_event_id": event_id}]
        effect_ref = f"applications/{application_id}/followups/{event_id}"
        event.update(processing_status="APPLIED", correlation_status="EXACT",
                     application_id=application_id, session_id=session_id,
                     resource_id=application_id,
                     correlation_basis=correlation_basis, effect_ref=effect_ref,
                     lease_owner=None, lease_started_at=None,
                     updated_at=store._now())
        return {"status": "success", "duplicate": duplicate,
                "effect_ref": effect_ref, "session_id": session_id,
                "application_id": application_id}

    async def _apply_external_event_signal(
            founder_id, event_id, lease_owner, *, session_id,
            correlation_basis, resource_id=None):
        event = await _get_external_event(founder_id, event_id)
        if not event or event.get("lease_owner") != lease_owner:
            return {"status": "error", "error": True,
                    "error_code": "lease_conflict", "message": "lease changed"}
        effect_ref = f"signals/{event_id}"
        event.update(processing_status="APPLIED", correlation_status="EXACT",
                     session_id=session_id, resource_id=resource_id,
                     correlation_basis=correlation_basis, effect_ref=effect_ref,
                     lease_owner=None, lease_started_at=None,
                     updated_at=store._now())
        return {"status": "success", "duplicate": False,
                "effect_ref": effect_ref, "session_id": session_id}

    async def _claim_external_event_delivery(founder_id, event_id):
        event = await _get_external_event(founder_id, event_id)
        if not event:
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch", "message": "event not found"}
        if event.get("delivery_status") in {"ENQUEUED", "DELIVERED", "NOT_REQUIRED"}:
            return {"status": "success", "duplicate": True,
                    "delivery_status": event.get("delivery_status")}
        event["delivery_status"] = "ENQUEUED"
        return {"status": "success", "claimed": True}

    async def _finish_external_event_delivery(founder_id, event_id, *, delivered):
        event = await _get_external_event(founder_id, event_id)
        if not event:
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch", "message": "event not found"}
        event["delivery_status"] = "DELIVERED" if delivered else "FAILED"
        return {"status": "success", "delivery_status": event["delivery_status"]}

    async def _prepare_external_action(
            founder_id, connection_id, action_kind, idempotency_key,
            request_hash, *, session_id=None, application_id=None,
            resource_id=None, subject_hash=None, approval_id=None,
            sandbox_context=None, lease_seconds=120):
        from services import data_source_contracts as dsc

        if not await _get_data_connection(founder_id, connection_id):
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch", "message": "connection not found"}
        try:
            aid = dsc.external_action_id(founder_id, action_kind,
                                         idempotency_key)
        except ValueError:
            return {"status": "error", "error": True,
                    "error_code": "invalid_contract", "message": "invalid action"}
        # Mirror the production sandbox-context contract so a test can never
        # pass an action shape the real store would refuse.
        context = dict(sandbox_context or {})
        if action_kind in dsc.SANDBOX_ONLY_ACTION_KINDS:
            if not context:
                return {"status": "error", "error": True,
                        "error_code": "invalid_contract",
                        "message": "sandbox action context required"}
        elif context:
            return {"status": "error", "error": True,
                    "error_code": "invalid_contract",
                    "message": "sandbox context is not valid for this action"}
        existing = store.external_actions.get(aid)
        if existing:
            if existing.get("request_hash") != request_hash:
                return {"status": "error", "error": True,
                        "error_code": "version_conflict", "message": "payload changed"}
            if existing.get("status") == "UNCERTAIN":
                return {"status": "error", "error": True,
                        "error_code": "reconciliation_required",
                        "message": "action requires reconciliation", "action_id": aid}
            if existing.get("status") in {"SUCCEEDED", "FAILED"}:
                return {"status": "success", "duplicate": True, **existing}
            started = datetime.fromisoformat(existing["lease_started_at"])
            age = (datetime.now(timezone.utc) - started).total_seconds()
            if age <= int(existing.get("lease_seconds") or 1):
                return {"status": "success", "in_progress": True,
                        "action_id": aid}
            existing.update(
                status="UNCERTAIN", uncertainty_reason="prepared_lease_expired",
                error_code="reconciliation_required", lease_owner=None,
                lease_started_at=None, updated_at=store._now(),
                completed_at=store._now())
            return {"status": "error", "error": True,
                    "error_code": "reconciliation_required",
                    "message": "action requires reconciliation",
                    "action_id": aid, **existing}
        now = store._now()
        owner = uuid.uuid4().hex
        row = {
            "schema_version": 1, "action_id": aid, "founder_id": founder_id,
            "connection_id": connection_id, "session_id": session_id,
            "application_id": application_id, "resource_id": resource_id,
            "action_kind": action_kind, "idempotency_key": idempotency_key,
            "request_hash": request_hash, "subject_hash": subject_hash,
            "approval_id": approval_id, "sandbox_context": context or None,
            "status": "PREPARED",
            "provider_effect_id": None, "result_ref": {},
            "uncertainty_reason": None, "error_code": None,
            "lease_owner": owner, "lease_started_at": now,
            "lease_seconds": lease_seconds, "created_at": now,
            "updated_at": now, "completed_at": None,
        }
        store.external_actions[aid] = row
        return {"status": "success", "claimed": True,
                "action_id": aid, "lease_owner": owner}

    async def _finish_external_action(
            founder_id, action_id, lease_owner, status, *,
            provider_effect_id=None, result_ref=None, uncertainty_reason=None,
            error_code=None):
        row = store.external_actions.get(action_id)
        if not row or row.get("founder_id") != founder_id:
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch", "message": "action not found"}
        if row.get("status") != "PREPARED":
            return {"status": "success", "duplicate": True, **row}
        if row.get("lease_owner") != lease_owner:
            return {"status": "error", "error": True,
                    "error_code": "lease_conflict", "message": "lease changed"}
        row.update(status=status, provider_effect_id=provider_effect_id,
                   result_ref=result_ref or {}, uncertainty_reason=uncertainty_reason,
                   error_code=error_code, lease_owner=None, lease_started_at=None,
                   updated_at=store._now(), completed_at=store._now())
        return {"status": "success", "duplicate": False, **row}

    async def _get_external_action(founder_id, action_id):
        row = store.external_actions.get(action_id)
        return row if row and row.get("founder_id") == founder_id else None

    async def _reconcile_external_action(
            founder_id, action_id, status, *, provider_effect_id=None,
            result_ref=None, error_code=None):
        row = store.external_actions.get(action_id)
        if not row or row.get("founder_id") != founder_id:
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch", "message": "action not found"}
        if row.get("status") in {"SUCCEEDED", "FAILED"}:
            return {"status": "success", "duplicate": True, **row}
        if row.get("status") != "UNCERTAIN":
            return {"status": "error", "error": True,
                    "error_code": "version_conflict", "message": "not uncertain"}
        row.update(status=status, provider_effect_id=provider_effect_id,
                   result_ref=result_ref or {}, uncertainty_reason=None,
                   error_code=error_code, updated_at=store._now(),
                   completed_at=store._now())
        return {"status": "success", "duplicate": False, **row}

    async def _list_external_actions(founder_id, *, limit=100):
        return [row for row in store.external_actions.values()
                if row.get("founder_id") == founder_id][:limit]

    async def _get_legacy_data_source_snapshot(founder_id):
        integrations = store.integrations.get(founder_id, {})
        rows = list(store.ingestions.values()) + list(store.applications.values()) \
            + list(store.portal_registrations.values())
        return {
            "integrations_exists": founder_id in store.integrations,
            "drive_files": list(integrations.get("drive_files") or [])[:500],
            "gmail_label_configured": bool(integrations.get("gmail_label")),
            "processed_gmail_count": len(store.processed_gmail_ids),
            "processed_alex_count": len(store.processed_alex_ids),
            "missing_owner_count": sum(not row.get("founder_id") for row in rows),
            "missing_session_count": sum(
                not row.get("session_id") for row in
                list(store.ingestions.values()) + list(store.portal_registrations.values())),
        }

    for name, fn in {
        "get_profile": _get_profile, "apply_profile_update": _apply_profile_update,
        "audit": _audit, "create_feedback": _create_feedback, "get_feedback": _get_feedback,
        "mark_distilled": _mark_distilled, "create_application": _create_application,
        "find_application_by_founder_opportunity": _find_application_by_founder_opportunity,
        "get_or_create_application": _get_or_create_application,
        "get_application": _get_application, "update_application": _update_application,
        "append_application_followup": _append_application_followup,
        "record_interview_answer": _record_interview_answer,
        "guarded_application_transition": _guarded_transition,
        "update_draft_section": _update_draft_section,
        "create_approval": _create_approval, "get_approval": _get_approval,
        "grant_approval": _grant_approval, "deny_approval": _deny_approval,
        "find_valid_approval": _find_valid_approval, "consume_approval": _consume_approval,
        "claim_approval": _claim_approval,
        "expire_stale_approvals": _expire_stale_approvals,
        "find_pending_approval": _find_pending_approval,
        "list_pending_approvals": _list_pending_approvals,
        "find_successful_action": _find_successful_action,
        "create_ingestion": _create_ingestion, "get_ingestion": _get_ingestion,
        "update_ingestion": _update_ingestion, "get_opportunity": _get_opportunity,
        "register_artifact_ingestion": _register_artifact_ingestion,
        "get_artifact": _get_artifact, "update_artifact": _update_artifact,
        "replace_artifact_chunks": _replace_artifact_chunks,
        "list_artifact_chunks": _list_artifact_chunks,
        "claim_ingestion": _claim_ingestion,
        "set_ingestion_stage": _set_ingestion_stage,
        "update_ingestion_leased": _update_ingestion_leased,
        "finish_ingestion": _finish_ingestion,
        "retry_ingestion": _retry_ingestion,
        "get_evidence_check": _get_evidence_check,
        "claim_evidence_check": _claim_evidence_check,
        "complete_evidence_check": _complete_evidence_check,
        "create_opportunity": _create_opportunity, "set_opportunity_state": _set_opportunity_state,
        "list_opportunities": _list_opportunities, "list_unscored_opportunities": _list_unscored,
        "find_opportunity_by_hash": _find_by_hash,
        "list_inflight_applications": lambda fid: _list_inflight(fid),
        "create_document_record": _create_document_record,
        "list_documents": _list_documents,
        "get_document_by_artifact": _get_document_by_artifact,
        "next_document_version": _next_document_version,
        "get_source_hash": _get_source_hash, "set_source_hash": _set_source_hash,
        "save_pending_portal_registration": _save_pending_portal_registration,
        "list_pending_portal_registrations": _list_pending_portal_registrations,
        "complete_portal_registration": _complete_portal_registration,
        "claim_discovery_request": _claim_discovery_request,
        "finish_discovery_request": _finish_discovery_request,
        "create_discovery_receipt": _create_discovery_receipt,
        "get_discovery_request": _get_discovery_request,
        "get_discovery_request_by_id": _get_discovery_request_by_id,
        "list_active_discovery_requests": _list_active_discovery_requests,
        "create_voice_note_artifact": _create_voice_note_artifact,
        "update_discovery_receipt": _update_discovery_receipt,
        "upsert_resource_and_link": _upsert_resource_and_link,
        "update_resource_projection": _update_resource_projection,
        "get_resource": _get_resource,
        "tombstone_session_links": _tombstone_session_links,
        "upsert_session_catalog": _upsert_session_catalog,
        "get_session_catalog": _get_session_catalog,
        "bump_session_catalog_resources": _bump_session_catalog_resources,
        "search_session_links": _search_session_links,
        "list_session_links": _list_session_links,
        "list_resource_links": _list_resource_links,
        "search_resources": _search_resources,
        "search_session_catalog": _search_session_catalog,
        "list_recent_session_catalog": _list_recent_session_catalog,
        "list_recent_resources": _list_recent_resources,
        "upsert_data_connection": _upsert_data_connection,
        "get_data_connection": _get_data_connection,
        "list_data_connections": _list_data_connections,
        "transition_data_connection": _transition_data_connection,
        "revoke_connection_source_grants": _revoke_connection_source_grants,
        "create_source_grant": _create_source_grant,
        "get_source_grant": _get_source_grant,
        "list_source_grants": _list_source_grants,
        "revoke_source_grant": _revoke_source_grant,
        "mark_source_grant_missing": _mark_source_grant_missing,
        "create_external_event": _create_external_event,
        "get_external_event": _get_external_event,
        "list_external_events_by_thread": _list_external_events_by_thread,
        "claim_external_event": _claim_external_event,
        "create_founder_inbox_item": _create_founder_inbox_item,
        "get_founder_inbox_item": _get_founder_inbox_item,
        "list_founder_inbox": _list_founder_inbox,
        "resolve_founder_inbox_item": _resolve_founder_inbox_item,
        "dismiss_founder_inbox_item": _dismiss_founder_inbox_item,
        "apply_external_event_to_application": _apply_external_event_to_application,
        "apply_external_event_signal": _apply_external_event_signal,
        "claim_external_event_delivery": _claim_external_event_delivery,
        "finish_external_event_delivery": _finish_external_event_delivery,
        "prepare_external_action": _prepare_external_action,
        "finish_external_action": _finish_external_action,
        "reconcile_external_action": _reconcile_external_action,
        "get_external_action": _get_external_action,
        "list_external_actions": _list_external_actions,
        "get_legacy_data_source_snapshot": _get_legacy_data_source_snapshot,
    }.items():
        monkeypatch.setattr("services.firestore." + name, fn)

    async def _list_inflight(fid):
        return [a for a in store.applications.values()
                if a["founder_id"] == fid and a["state"] != "CLOSED"]

    monkeypatch.setattr("services.firestore.list_inflight_applications", _list_inflight)
    return store

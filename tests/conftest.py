"""Shared fixtures: an in-memory fake of the services.firestore module so
service-layer tests run without credentials or an emulator."""

import uuid
from datetime import datetime, timezone

import pytest


class FakeStore:
    def __init__(self):
        self.opportunities = {}
        self.applications = {}
        self.profiles = {}
        self.feedback = {}
        self.approvals = {}
        self.audit = []
        self.ingestions = {}
        self.documents = {}
        self.source_hashes = {}
        self.portal_registrations = {}
        self.evidence_checks = {}

    def _now(self):
        return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()

    async def _get_profile(founder_id):
        return store.profiles.get(founder_id, {
            "version": 0, "facts": {}, "voice_rules": [],
            "canonical_answers": [], "rejection_history": [], "decision_patterns": []})

    async def _apply_profile_update(founder_id, kind, payload, evidence):
        profile = await _get_profile(founder_id)
        profile = {**profile}
        col = {"voice_rule": "voice_rules", "canonical_answer_update": "canonical_answers",
               "fact_update": "facts", "decision_pattern": "decision_patterns"}[kind]
        if col == "facts":
            profile["facts"] = {**profile.get("facts", {}), **payload}
        else:
            entry = {**payload, "evidence": evidence, "id": uuid.uuid4().hex[:12],
                     "created_at": store._now(), "active": True}
            profile[col] = [*profile.get(col, []), entry]
        profile["version"] = profile.get("version", 0) + 1
        store.profiles[founder_id] = profile
        store.audit.append({"id": uuid.uuid4().hex, "actor": "agent:distiller",
                            "action": "profile_update", "target": f"profiles/{founder_id}",
                            "result": "success", "detail": f"{kind}: {evidence[:200]}",
                            "idempotency_key": None, "created_at": store._now()})
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

    async def _create_application(founder_id, opportunity_id, checklist):
        aid = uuid.uuid4().hex
        store.applications[aid] = {"id": aid, "founder_id": founder_id,
                                   "opportunity_id": opportunity_id, "state": "INTERVIEWING",
                                   "checklist": checklist, "interview_qa": [],
                                   "draft_sections": [], "form_fill_report": None,
                                   "submit_idempotency_key": None, "submission": None,
                                   "followups": [], "created_at": store._now(),
                                   "updated_at": store._now()}
        return aid

    async def _get_application(aid):
        return store.applications.get(aid)

    async def _update_application(aid, **fields):
        store.applications[aid].update(fields, updated_at=store._now())

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
                               founder_id="", session_id=""):
        from datetime import timedelta
        aid = uuid.uuid4().hex
        expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        store.approvals[aid] = {"id": aid, "application_id": application_id, "gate": gate,
                                "founder_id": founder_id, "session_id": session_id,
                                "details": details or {},
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

    async def _find_valid_approval(application_id, gate="", founder_id="", session_id=""):
        now = store._now()
        for a in store.approvals.values():
            if (a["application_id"] == application_id and a["status"] == "GRANTED"
                    and a["expires_at"] > now
                    and (not gate or a.get("gate") == gate)
                    and (not founder_id or a.get("founder_id") == founder_id)
                    and (not session_id or a.get("session_id") == session_id)):
                return a
        return None

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

    async def _next_document_version(founder_id, doc_key):
        return sum(1 for d in store.documents.values()
                   if d["founder_id"] == founder_id and d.get("doc_key") == doc_key) + 1

    async def _get_source_hash(url):
        return store.source_hashes.get(url)

    async def _set_source_hash(url, content_hash):
        store.source_hashes[url] = content_hash

    async def _save_pending_portal_registration(host, founder_id, session_id,
                                                portal_url, email):
        store.portal_registrations[host] = {
            "host": host, "founder_id": founder_id, "session_id": session_id,
            "portal_url": portal_url, "email": email, "status": "PENDING",
        }

    async def _list_pending_portal_registrations(founder_id):
        return [record for record in store.portal_registrations.values()
                if record["founder_id"] == founder_id
                and record["status"] == "PENDING"]

    async def _complete_portal_registration(host):
        if host in store.portal_registrations:
            store.portal_registrations[host]["status"] = "COMPLETED"

    for name, fn in {
        "get_profile": _get_profile, "apply_profile_update": _apply_profile_update,
        "audit": _audit, "create_feedback": _create_feedback, "get_feedback": _get_feedback,
        "mark_distilled": _mark_distilled, "create_application": _create_application,
        "get_application": _get_application, "update_application": _update_application,
        "guarded_application_transition": _guarded_transition,
        "update_draft_section": _update_draft_section,
        "create_approval": _create_approval, "get_approval": _get_approval,
        "grant_approval": _grant_approval, "deny_approval": _deny_approval,
        "find_valid_approval": _find_valid_approval, "consume_approval": _consume_approval,
        "claim_approval": _claim_approval,
        "find_pending_approval": _find_pending_approval,
        "list_pending_approvals": _list_pending_approvals,
        "find_successful_action": _find_successful_action,
        "create_ingestion": _create_ingestion, "get_ingestion": _get_ingestion,
        "update_ingestion": _update_ingestion, "get_opportunity": _get_opportunity,
        "get_evidence_check": _get_evidence_check,
        "claim_evidence_check": _claim_evidence_check,
        "complete_evidence_check": _complete_evidence_check,
        "create_opportunity": _create_opportunity, "set_opportunity_state": _set_opportunity_state,
        "list_opportunities": _list_opportunities, "list_unscored_opportunities": _list_unscored,
        "find_opportunity_by_hash": _find_by_hash,
        "list_inflight_applications": lambda fid: _list_inflight(fid),
        "create_document_record": _create_document_record,
        "list_documents": _list_documents,
        "next_document_version": _next_document_version,
        "get_source_hash": _get_source_hash, "set_source_hash": _set_source_hash,
        "save_pending_portal_registration": _save_pending_portal_registration,
        "list_pending_portal_registrations": _list_pending_portal_registrations,
        "complete_portal_registration": _complete_portal_registration,
    }.items():
        monkeypatch.setattr("services.firestore." + name, fn)

    async def _list_inflight(fid):
        return [a for a in store.applications.values()
                if a["founder_id"] == fid and a["state"] != "CLOSED"]

    monkeypatch.setattr("services.firestore.list_inflight_applications", _list_inflight)
    return store

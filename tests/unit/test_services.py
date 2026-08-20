"""Service-layer unit tests (docs/05, 06, 08, 09, 12)."""

import pytest

from services import (alex_mailbox, approval_service, calendar_adapter,
                      discovery_service,
                      drive_adapter, gmail_adapter, pipeline_service,
                      profile_service, recon_service)

pytestmark = pytest.mark.asyncio


# ---- pipeline_service -----------------------------------------------------

class TestUrgency:
    def test_rolling(self):
        assert pipeline_service.compute_urgency(None, [])["tier"] == "ROLLING"

    def test_critical(self):
        from datetime import datetime, timedelta
        d = (datetime.now() + timedelta(days=2)).date().isoformat()
        u = pipeline_service.compute_urgency(d, ["essay", "deck"])
        assert u["tier"] == "CRITICAL" and u["days_left"] == 2 and "start now" in u["note"]

    def test_urgent(self):
        from datetime import datetime, timedelta
        d = (datetime.now() + timedelta(days=9)).date().isoformat()
        assert pipeline_service.compute_urgency(d, [])["tier"] == "URGENT"

    def test_normal_and_overdue(self):
        from datetime import datetime, timedelta
        future = (datetime.now() + timedelta(days=60)).date().isoformat()
        past = (datetime.now() - timedelta(days=1)).date().isoformat()
        assert pipeline_service.compute_urgency(future, [])["tier"] == "NORMAL"
        assert pipeline_service.compute_urgency(past, [])["tier"] == "OVERDUE"


class TestDerivedKey:
    def test_deterministic(self):
        k1 = pipeline_service.derive_submit_key("f1", "app1")
        k2 = pipeline_service.derive_submit_key("f1", "app1")
        k3 = pipeline_service.derive_submit_key("f1", "app2")
        assert k1 == k2 and k1 != k3 and len(k1) == 24


class TestChecklist:
    def test_fixed_steps_plus_materials(self):
        items = pipeline_service.initial_checklist(["2 essays", "deck"])
        keys = [i["key"] for i in items]
        assert keys[:2] == ["eligibility_check", "interview"]
        assert "material:2 essays" in keys and "material:deck" in keys
        assert all(i["status"] == "PENDING" for i in items)


# ---- approval gate (docs/12) ----------------------------------------------

class TestApprovalGate:
    async def test_full_lifecycle(self, fake_store):
        aid = "app1"
        req = await approval_service.request_approval(aid)
        assert "token" not in str(req)  # never the token

        blocked = await approval_service.resolve_for_submit(aid)
        assert blocked["status"] == "error"  # PENDING ≠ GRANTED

        await approval_service.resolve(req["approval_id"], "grant", "founder")
        ok = await approval_service.resolve_for_submit(aid)
        assert ok["status"] == "success"
        await approval_service.consume(ok["approval_id"])

        blocked2 = await approval_service.resolve_for_submit(aid)
        assert blocked2["status"] == "error"  # CONSUMED is final
        refused = [r for r in fake_store.audit if r["result"] == "refused"]
        assert len(refused) == 2  # both blocked attempts audited

    async def test_double_resolve_refused(self, fake_store):
        req = await approval_service.request_approval("app1")
        await approval_service.resolve(req["approval_id"], "deny", "founder")
        again = await approval_service.resolve(req["approval_id"], "grant", "founder")
        assert again["status"] == "error"


# ---- discovery (docs/08) ---------------------------------------------------

class TestOpportunityValidation:
    async def test_extra_fields_rejected(self, fake_store):
        schema = {"name": "str", "application_url": "str"}
        record = {"name": "X", "application_url": "https://x", "bogus": 1}
        result = await discovery_service.save_opportunity_record(record, schema)
        assert result["status"] == "error" and "bogus" in result["message"]

    async def test_missing_fields_become_none(self, fake_store):
        schema = {"name": "str", "application_url": "str", "award": "str"}
        record = {"name": "X", "application_url": "https://x"}
        result = await discovery_service.save_opportunity_record(record, schema)
        assert result["status"] == "success"
        opp = fake_store.opportunities[result["opportunity_id"]]
        assert opp["award"] is None and opp["state"] == "DISCOVERED"

    async def test_dedupe(self, fake_store):
        schema = {"name": "str", "application_url": "str"}
        record = {"name": "X", "application_url": "https://x"}
        r1 = await discovery_service.save_opportunity_record(record, schema)
        r2 = await discovery_service.save_opportunity_record(record, schema)
        assert r1["opportunity_id"] == r2["opportunity_id"]


class TestHtmlParsing:
    def test_text_and_links(self):
        html = """<html><body><h1>Programs</h1><script>var x=1;</script>
        <a href='/apply'>Apply now</a><p>Deadline: Oct 1</p></body></html>"""
        text, links = discovery_service._parse_html(html, "https://example.org")
        assert "Programs" in text and "var x" not in text
        assert links == [{"url": "https://example.org/apply", "anchor_text": "Apply now"}]


# ---- profile service (docs/06) ---------------------------------------------

class TestRetrieval:
    async def test_ranking(self, fake_store):
        for qk, tags, ts in [("describe_traction", ["traction"], "2026-08-01"),
                             ("team", ["team"], "2026-08-10"),
                             ("other", ["describe_traction"], "2026-08-05")]:
            await profile_service.apply_update(
                "f1", "canonical_answer_update",
                {"question_key": qk, "text": f"answer for {qk}", "tags": tags,
                 "approved_at": ts}, "evidence")
        answers = await profile_service.get_relevant_answers("f1", "describe_traction")
        assert answers[0]["question_key"] == "describe_traction"  # exact match first
        assert answers[1]["question_key"] == "other"              # tag overlap second
        assert answers[2]["question_key"] == "team"               # recency last


class TestSemanticRetrieval:
    """Embeddings retrieval (docs/06, 19 §P1.6): semantic when wired,
    deterministic fallback otherwise."""

    async def _seed(self):
        for qk, text in [("describe_traction", "1,200 patients on follow-up plans"),
                         ("team", "two founders, one PhD"),
                         ("market_size", "SAM $9B across MEA")]:
            await profile_service.apply_update(
                "f1", "canonical_answer_update",
                {"question_key": qk, "text": text, "tags": [], "approved_at": "2026-08-01"},
                "evidence")

    def teardown_method(self):
        profile_service.set_embed_fn(None)

    async def test_semantic_ranking_when_wired(self, fake_store):
        await self._seed()
        # fake embedder: the traction text vector points at the section vector
        def fake_embed(texts):
            return [[1.0, 0.0]] + [[1.0, 0.0] if "patients" in t else [0.0, 1.0]
                                   for t in texts[1:]]
        profile_service.set_embed_fn(fake_embed)
        answers = await profile_service.get_relevant_answers("f1", "describe_traction")
        assert answers[0]["question_key"] == "describe_traction"

    async def test_fallback_on_backend_error(self, fake_store):
        await self._seed()
        profile_service.set_embed_fn(lambda texts: (_ for _ in ()).throw(RuntimeError("down")))
        answers = await profile_service.get_relevant_answers("f1", "describe_traction")
        assert answers  # deterministic path still returns, never an outage

    async def test_deterministic_primary_when_unwired(self, fake_store):
        await self._seed()
        profile_service.set_embed_fn(None)
        answers = await profile_service.get_relevant_answers("f1", "describe_traction")
        assert answers[0]["question_key"] == "describe_traction"  # exact match first


class TestIngestionGate:
    async def test_proposals_never_write_directly(self, fake_store):
        profile_service.set_extract_fn(lambda artifact, fid: [
            {"kind": "fact_update", "payload": {"sector": "fintech"},
             "evidence_quote": "we build payment rails"}])
        result = await profile_service.ingest_document("f1", "upload", "deck.pdf", "companydoc_x")
        assert result["proposed_count"] == 1
        profile = await profile_service.get_profile("f1")
        assert profile.get("facts", {}) == {}  # nothing written without confirmation

        proposals = (await profile_service.propose_profile_updates(result["ingestion_id"]))["proposals"]
        confirm = await profile_service.confirm_profile_updates(
            "f1", result["ingestion_id"], approved=[proposals[0]["id"]], rejected=[],
            rejection_reasons=[])
        assert confirm["applied"] == 1
        profile = await profile_service.get_profile("f1")
        assert profile["facts"]["sector"] == "fintech"
        profile_service.set_extract_fn(None)

    async def test_rejection_becomes_feedback(self, fake_store):
        profile_service.set_extract_fn(lambda a, f: [
            {"id": "p1", "kind": "fact_update", "payload": {"arr": "$1m"}, "evidence_quote": "…"}])
        result = await profile_service.ingest_document("f1", "upload", "model.xlsx", "companydoc_y")
        confirm = await profile_service.confirm_profile_updates(
            "f1", result["ingestion_id"], approved=[], rejected=["p1"],
            rejection_reasons=["that's pre-money projections, not ARR"])
        assert confirm["rejected"] == 1
        fb = list(fake_store.feedback.values())[0]
        assert fb["reason"] == "that's pre-money projections, not ARR"  # verbatim
        profile_service.set_extract_fn(None)


class TestAutoApply:
    """Autonomous ingestion (docs/06 §bootstrap): confident + non-conflicting
    proposals apply themselves; conflicts and low-confidence items wait."""

    async def test_clean_proposals_apply_themselves(self, fake_store):
        profile_service.set_extract_fn(lambda artifact, fid: [
            {"kind": "fact_update", "payload": {"sector": "fintech"},
             "evidence_quote": "we build payment rails", "confidence": "high"},
            {"kind": "voice_rule", "payload": {"rule": "no buzzwords"},
             "evidence_quote": "…", "confidence": "high"}])
        result = await profile_service.ingest_document("f1", "upload", "deck.pdf", "companydoc_a")
        auto = await profile_service.auto_apply_profile_updates("f1", result["ingestion_id"])
        assert auto["auto_applied"] == 2
        assert auto["needs_founder"] == []
        profile = await profile_service.get_profile("f1")
        assert profile["facts"]["sector"] == "fintech"
        assert profile["voice_rules"][0]["rule"] == "no buzzwords"
        profile_service.set_extract_fn(None)

    async def test_conflict_is_held_for_the_founder(self, fake_store):
        await profile_service.apply_update("f1", "fact_update", {"sector": "fintech"}, "seed")
        profile_service.set_extract_fn(lambda a, f: [
            {"kind": "fact_update", "payload": {"sector": "healthtech"},
             "evidence_quote": "…", "confidence": "high"}])
        result = await profile_service.ingest_document("f1", "upload", "deck.pdf", "companydoc_b")
        auto = await profile_service.auto_apply_profile_updates("f1", result["ingestion_id"])
        assert auto["auto_applied"] == 0
        assert len(auto["needs_founder"]) == 1
        assert "fintech" in auto["needs_founder"][0]["reason"]
        assert "healthtech" in auto["needs_founder"][0]["reason"]
        profile = await profile_service.get_profile("f1")
        assert profile["facts"]["sector"] == "fintech"  # never silently overwritten
        profile_service.set_extract_fn(None)

    async def test_low_confidence_is_held(self, fake_store):
        profile_service.set_extract_fn(lambda a, f: [
            {"id": "p9", "kind": "fact_update", "payload": {"arr": "$1m"},
             "evidence_quote": "…", "confidence": "low"}])
        result = await profile_service.ingest_document("f1", "upload", "model.xlsx", "companydoc_c")
        auto = await profile_service.auto_apply_profile_updates("f1", result["ingestion_id"])
        assert auto["auto_applied"] == 0
        assert auto["needs_founder"][0]["id"] == "p9"
        # held items remain resolvable via the confirm flow
        confirm = await profile_service.confirm_profile_updates(
            "f1", result["ingestion_id"], approved=["p9"], rejected=[], rejection_reasons=[])
        assert confirm["applied"] == 1
        profile_service.set_extract_fn(None)

    async def test_same_value_is_not_a_conflict(self, fake_store):
        await profile_service.apply_update("f1", "fact_update", {"sector": "fintech"}, "seed")
        profile_service.set_extract_fn(lambda a, f: [
            {"kind": "fact_update", "payload": {"sector": "fintech"},
             "evidence_quote": "…", "confidence": "high"}])
        result = await profile_service.ingest_document("f1", "upload", "deck.pdf", "companydoc_d")
        auto = await profile_service.auto_apply_profile_updates("f1", result["ingestion_id"])
        assert auto["auto_applied"] == 1
        assert auto["needs_founder"] == []
        profile_service.set_extract_fn(None)


class TestDedupHash:
    def test_none_safe(self):
        h = pipeline_service.dedup_hash(None, None)
        assert isinstance(h, str) and len(h) == 64

    def test_deterministic_and_case_insensitive(self):
        assert pipeline_service.dedup_hash(" Meridian ", "HTTP://X ") == \
            pipeline_service.dedup_hash("meridian", "http://x")


class TestSweep:
    """Discovery sweep (docs/08): search lane + record-level fault isolation."""

    def _workflow(self):
        from types import SimpleNamespace
        return SimpleNamespace(
            sources=[{"type": "search", "queries_from_profile": True,
                      "max_results_per_query": 5}],
            entity_schema={"name": {}, "application_url": {}, "award": {},
                           "deadline": {}})

    def _wire(self, monkeypatch, search_results, records_by_artifact):
        async def _fetch(url, source_type, artifact):
            return {"status": "success", "artifact": artifact, "summary": "",
                    "chars": 100, "rendered": False, "links": []}
        monkeypatch.setattr(discovery_service, "fetch_source", _fetch)
        monkeypatch.setattr("services.storage.read_text", lambda a: a)
        discovery_service.set_search_fn(lambda q: search_results)
        discovery_service.set_extract_fn(lambda text, schema: records_by_artifact)

    def teardown_method(self):
        discovery_service.set_search_fn(None)
        discovery_service.set_extract_fn(None)

    async def test_search_lane_saves_relevant_programs(self, fake_store, monkeypatch):
        self._wire(monkeypatch,
                   search_results=[{"title": "Acme Grant", "url": "https://a.example",
                                    "snippet": "grant program for founders"},
                                   {"title": "Cooking blog", "url": "https://b.example",
                                    "snippet": "best recipes"}],
                   records_by_artifact=[{"name": "Acme Grant",
                                         "application_url": "https://a.example/apply",
                                         "award": "$10k", "deadline": "2026-12-01"}])
        result = await discovery_service.run_sweep(self._workflow(), "f1")
        assert result["status"] == "success"
        assert result["new"] == 1  # irrelevant result filtered before fetch
        names = [o["name"] for o in fake_store.opportunities.values()]
        assert names == ["Acme Grant"]

    async def test_second_sweep_dedupes(self, fake_store, monkeypatch):
        self._wire(monkeypatch,
                   search_results=[{"title": "Acme Grant", "url": "https://a.example",
                                    "snippet": "grant program"}],
                   records_by_artifact=[{"name": "Acme Grant",
                                         "application_url": "https://a.example/apply"}])
        await discovery_service.run_sweep(self._workflow(), "f1")
        second = await discovery_service.run_sweep(self._workflow(), "f1")
        assert second["new"] == 0
        assert second["unchanged"] > 0  # identical content → extraction skipped
        assert len(fake_store.opportunities) == 1

    async def test_bad_record_does_not_kill_sweep(self, fake_store, monkeypatch):
        self._wire(monkeypatch,
                   search_results=[{"title": "Grants", "url": "https://a.example",
                                    "snippet": "grant program"}],
                   records_by_artifact=[
                       {"name": None, "application_url": None},  # malformed
                       {"name": "Good Program", "application_url": "https://g.example"}])
        result = await discovery_service.run_sweep(self._workflow(), "f1")
        assert result["status"] == "success"
        assert result["saved"] == 1
        assert result["errors"]  # the malformed record was reported, not fatal

    async def test_unchanged_source_skips_extraction(self, fake_store, monkeypatch):
        """The model is never paid twice for identical bytes (docs/08)."""
        calls = {"n": 0}
        async def _fetch(url, source_type, artifact):
            return {"status": "success", "artifact": artifact, "summary": "",
                    "chars": 100, "rendered": False, "links": []}
        monkeypatch.setattr(discovery_service, "fetch_source", _fetch)
        monkeypatch.setattr("services.storage.read_text", lambda a: "same page content")
        discovery_service.set_search_fn(lambda q: [{"title": "Grant", "url": "https://g.example",
                                                    "snippet": "grant program"}])
        def _counting_extract(text, schema):
            calls["n"] += 1
            return [{"name": "Grant", "application_url": "https://g.example/apply"}]
        discovery_service.set_extract_fn(_counting_extract)
        await discovery_service.run_sweep(self._workflow(), "f1")
        await discovery_service.run_sweep(self._workflow(), "f1")
        assert calls["n"] == 1  # second sweep: content unchanged → no extraction

    async def test_deadline_scan_recomputes_urgency(self, fake_store):
        from datetime import datetime, timedelta
        fake_store.opportunities["o1"] = {
            "id": "o1", "state": "SHORTLISTED",
            "deadline": (datetime.now() + timedelta(days=2)).date().isoformat(),
            "required_materials": ["essay"], "urgency": {"tier": "NORMAL"}}
        result = await discovery_service.deadline_scan()
        assert result["scanned"] == 1
        assert result["newly_critical"] == ["o1"]
        assert fake_store.opportunities["o1"]["urgency"]["tier"] == "CRITICAL"


# ---- integrations: Drive + Gmail adapters (docs/06, 07, 12) ---------------

class _FakeReq:
    def __init__(self, value): self._v = value
    def execute(self): return self._v


class _FakeDrive:
    def __init__(self, meta, data): self._meta, self._data = meta, data
    def files(self): return self
    def get(self, **kw): return _FakeReq(self._meta)
    def export(self, **kw): return _FakeReq(self._data)
    def list(self, **kw): return _FakeReq({"files": [dict(self._meta, id="f1")]})
    def get_media(self, **kw):
        from googleapiclient.http import MediaIoBaseDownload  # noqa: F401
        raise AssertionError("binary path not exercised in this fake")


class _FakeGmailUsers:
    def __init__(self, stubs, messages): self._stubs, self._msgs = stubs, messages
    def messages(self): return self
    def list(self, **kw): return _FakeReq({"messages": self._stubs})
    def get(self, userId, id, format): return _FakeReq(self._msgs[id])


class _FakeGmail:
    def __init__(self, stubs, messages): self._u = _FakeGmailUsers(stubs, messages)
    def users(self): return self._u


def _gmail_msg(subject, sender="portal@program.org", body="we received your application"):
    import base64
    return {"payload": {"mimeType": "text/plain",
                        "headers": [{"name": "Subject", "value": subject},
                                    {"name": "From", "value": sender}],
                        "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}}


class TestDriveAdapter:
    def teardown_method(self):
        drive_adapter.set_service_factory(None)

    def test_list_files(self):
        drive_adapter.set_service_factory(lambda: _FakeDrive(
            {"name": "deck.pdf", "mimeType": "application/pdf"}, b""))
        result = drive_adapter.list_files("folder1")
        assert result["status"] == "success"
        assert result["files"][0]["name"] == "deck.pdf"

    def test_fetch_google_doc_exports_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.storage._root", lambda: str(tmp_path))
        drive_adapter.set_service_factory(lambda: _FakeDrive(
            {"name": "Business plan", "mimeType": "application/vnd.google-apps.document"},
            b"Kweli Health plan"))
        result = drive_adapter.fetch_file("doc1")
        assert result["status"] == "success"
        assert result["artifact"].endswith(".txt")
        from services import storage
        assert "Kweli Health" in storage.read_text(result["artifact"])

    def test_unconfigured_oauth_is_error_data(self, monkeypatch):
        drive_adapter.set_service_factory(None)
        monkeypatch.setattr("services.google_oauth.get_credentials", lambda: None)
        result = drive_adapter.fetch_file("doc1")
        assert result["status"] == "error" and result["error"] is True


class TestGmailAdapter:
    def teardown_method(self):
        gmail_adapter.set_service_factory(None)

    def test_classify_deterministic(self):
        assert gmail_adapter.classify("Congratulations!", "") == "result_positive"
        assert gmail_adapter.classify("Unfortunately", "not selected") == "result_negative"
        assert gmail_adapter.classify("Application received", "") == "confirmation"
        assert gmail_adapter.classify("Action required", "") == "request"
        assert gmail_adapter.classify("Newsletter", "hello there") == "update"

    async def test_scan_classifies_and_dedupes(self, monkeypatch):
        processed = []
        async def _processed(): return list(processed)
        monkeypatch.setattr("services.gmail_adapter.firestore.get_processed_gmail_ids",
                            _processed)
        async def _mark(ids): processed.extend(ids)
        monkeypatch.setattr("services.gmail_adapter.firestore.add_processed_gmail_ids", _mark)
        gmail_adapter.set_service_factory(lambda: _FakeGmail(
            [{"id": "m1"}, {"id": "m2"}],
            {"m1": _gmail_msg("Your application was received"),
             "m2": _gmail_msg("Congratulations — you are shortlisted")}))
        result = await gmail_adapter.scan()
        assert result["status"] == "success"
        kinds = {e["id"]: e["kind"] for e in result["events"]}
        assert kinds == {"m1": "confirmation", "m2": "result_positive"}
        # rescan: everything already processed → no events
        second = await gmail_adapter.scan()
        assert second["events"] == []

    async def test_unconfigured_oauth_is_error_data(self, monkeypatch):
        gmail_adapter.set_service_factory(None)
        monkeypatch.setattr("services.google_oauth.get_credentials", lambda: None)
        result = await gmail_adapter.scan()
        assert result["status"] == "error" and result["error"] is True


# ---- recon guardrails (docs/09) --------------------------------------------

class _FakePage:
    async def screenshot(self):
        return b"png"


class TestVisionAllowlist:
    async def test_submit_action_refused(self, fake_store):
        async def fake_model(shot, goal, history):
            return {"action": "submit_form", "selector": "button[type=submit]"}
        recon_service.set_model_fn(fake_model)
        result = await recon_service.vision_step(_FakePage(), "submit the form", [], "app1")
        assert result["status"] == "error" and "not permitted" in result["message"]
        assert any(r["result"] == "refused" for r in fake_store.audit)
        recon_service.set_model_fn(None)

    async def test_recon_completes_on_done(self, fake_store):
        async def fake_model(shot, goal, history):
            return {"action": "done", "note": "mapped 16 fields"}
        recon_service.set_model_fn(fake_model)
        result = await recon_service.run_recon(_FakePage(), "map the form", "app1")
        assert result["status"] == "success" and result["steps"] == 1
        from services import storage
        assert storage.exists(result["artifact"])
        recon_service.set_model_fn(None)


class _FakeCalendarEvents:
    def __init__(self, items): self._items = items
    def list(self, **kw): return _FakeReq({"items": self._items})


class _FakeCalendarFreebusy:
    def __init__(self, busy): self._busy = busy
    def query(self, body): return _FakeReq({"calendars": {"primary": {"busy": self._busy}}})


class _FakeCalendar:
    def __init__(self, items=None, busy=None):
        self._e = _FakeCalendarEvents(items or [])
        self._f = _FakeCalendarFreebusy(busy or [])
    def events(self): return self._e
    def freebusy(self): return self._f


class TestCalendarAdapter:
    def teardown_method(self):
        calendar_adapter.set_service_factory(None)

    async def test_list_upcoming(self):
        calendar_adapter.set_service_factory(lambda: _FakeCalendar(items=[
            {"summary": "Program interview",
             "start": {"dateTime": "2026-08-21T10:00:00Z"},
             "end": {"dateTime": "2026-08-21T10:30:00Z"},
             "attendees": [{}, {}]}]))
        result = await calendar_adapter.list_upcoming()
        assert result["status"] == "success"
        assert result["events"][0]["summary"] == "Program interview"
        assert result["events"][0]["attendees"] == 2

    async def test_check_availability_returns_busy_blocks(self):
        calendar_adapter.set_service_factory(lambda: _FakeCalendar(
            busy=[{"start": "2026-08-21T10:00:00Z", "end": "2026-08-21T11:00:00Z"}]))
        result = await calendar_adapter.check_availability()
        assert result["status"] == "success" and len(result["busy"]) == 1

    async def test_unconfigured_oauth_is_error_data(self, monkeypatch):
        calendar_adapter.set_service_factory(None)
        monkeypatch.setattr("services.google_oauth.get_credentials", lambda: None)
        result = await calendar_adapter.check_availability()
        assert result["status"] == "error" and result["error"] is True


class _FakeAlexMessages:
    def __init__(self, stubs=None, messages=None, sent=None):
        self._stubs = stubs or []
        self._msgs = messages or {}
        self.sent = sent if sent is not None else []

    def list(self, **kw): return _FakeReq({"messages": self._stubs})
    def get(self, userId, id, format, **kw): return _FakeReq(self._msgs[id])
    def send(self, userId, body):
        self.sent.append(body)
        return _FakeReq({"id": "sent-1"})


class _FakeAlexUsers:
    def __init__(self, msgs): self._m = msgs
    def messages(self): return self._m


class _FakeAlexGmail:
    def __init__(self, msgs): self._u = _FakeAlexUsers(msgs)
    def users(self): return self._u


class TestAlexMailbox:
    def teardown_method(self):
        alex_mailbox.set_service_factory(None)

    async def test_send_refused_without_approval(self, monkeypatch):
        sent = []
        alex_mailbox.set_service_factory(lambda: _FakeAlexGmail(_FakeAlexMessages(sent=sent)))
        async def _none(target): return None
        monkeypatch.setattr("services.alex_mailbox.firestore.find_valid_approval", _none)
        monkeypatch.setattr("services.alex_mailbox.firestore.find_pending_approval", _none)
        async def _req(target, gate, details=None):
            assert gate == "send_email"
            assert details and details["to"] == "program@example.org"
            return {"status": "success", "approval_id": "ap1"}
        monkeypatch.setattr("services.approval_service.request_approval", _req)
        async def _audit(*a, **k): pass
        monkeypatch.setattr("services.alex_mailbox.firestore.audit", _audit)
        result = await alex_mailbox.send_email("program@example.org", "Question", "Hi")
        assert result["status"] == "needs_approval" and result["error"] is True
        assert sent == []  # nothing left the building

    async def test_send_with_approval_sends_and_consumes(self, monkeypatch):
        sent = []
        consumed = []
        alex_mailbox.set_service_factory(lambda: _FakeAlexGmail(_FakeAlexMessages(sent=sent)))
        async def _valid(target): return {"id": "ap1"}
        monkeypatch.setattr("services.alex_mailbox.firestore.find_valid_approval", _valid)
        async def _consume(aid): consumed.append(aid)
        monkeypatch.setattr("services.alex_mailbox.firestore.consume_approval", _consume)
        async def _audit(*a, **k): pass
        monkeypatch.setattr("services.alex_mailbox.firestore.audit", _audit)
        result = await alex_mailbox.send_email("program@example.org", "Question", "Hi")
        assert result["status"] == "success" and result["message_id"] == "sent-1"
        assert len(sent) == 1 and consumed == ["ap1"]

    async def test_send_rejects_bad_recipient(self):
        alex_mailbox.set_service_factory(lambda: _FakeAlexGmail(_FakeAlexMessages()))
        result = await alex_mailbox.send_email("not-an-email", "Hi", "Body")
        assert result["status"] == "error" and "recipient" in result["message"]

    async def test_scan_classifies_and_dedupes(self, monkeypatch):
        processed = []
        async def _processed(): return list(processed)
        monkeypatch.setattr("services.alex_mailbox.firestore.get_processed_alex_ids", _processed)
        async def _mark(ids): processed.extend(ids)
        monkeypatch.setattr("services.alex_mailbox.firestore.add_processed_alex_ids", _mark)
        async def _scan(s): pass
        monkeypatch.setattr("services.alex_mailbox.firestore.set_last_alex_scan", _scan)
        msgs = _FakeAlexMessages(
            stubs=[{"id": "m1"}, {"id": "m2"}],
            messages={"m1": _gmail_msg("Application received"),
                      "m2": _gmail_msg("Unfortunately not selected")})
        alex_mailbox.set_service_factory(lambda: _FakeAlexGmail(msgs))
        result = await alex_mailbox.scan_unread()
        assert result["status"] == "success"
        assert [e["kind"] for e in result["events"]] == ["confirmation", "result_negative"]
        assert set(processed) == {"m1", "m2"}
        # rescan is idempotent — same messages are skipped
        result2 = await alex_mailbox.scan_unread()
        assert result2["events"] == []

    async def test_unconfigured_oauth_is_error_data(self, monkeypatch):
        alex_mailbox.set_service_factory(None)
        monkeypatch.setattr("services.google_oauth.get_credentials", lambda account="founder": None)
        result = await alex_mailbox.scan_unread()
        assert result["status"] == "error" and result["error"] is True

    # search + full read (the mailbox is the agent's own — not privacy-narrowed)
    async def test_search_returns_summaries(self):
        msgs = _FakeAlexMessages(
            stubs=[{"id": "m1"}],
            messages={"m1": _gmail_msg("Interview invitation")})
        alex_mailbox.set_service_factory(lambda: _FakeAlexGmail(msgs))
        result = await alex_mailbox.search_messages("subject:interview")
        assert result["status"] == "success"
        assert result["results"][0]["subject"] == "Interview invitation"
        assert result["results"][0]["id"] == "m1"

    async def test_get_message_returns_full_body(self):
        msgs = _FakeAlexMessages(messages={"m1": _gmail_msg(
            "We received your application", body="Full body text here")})
        alex_mailbox.set_service_factory(lambda: _FakeAlexGmail(msgs))
        result = await alex_mailbox.get_message("m1")
        assert result["status"] == "success"
        assert result["message"]["body"] == "Full body text here"


class _FakeCalendarInsertEvents(_FakeCalendarEvents):
    def __init__(self, inserted): super().__init__([]); self.inserted = inserted
    def insert(self, calendarId, body, conferenceDataVersion, sendUpdates):
        self.inserted.append(body)
        return _FakeReq({"id": "evt-1", "hangoutLink": "https://meet.google.com/abc-defg-hij"})


class _FakeCalendarInsert:
    def __init__(self, inserted): self._e = _FakeCalendarInsertEvents(inserted)
    def events(self): return self._e
    def freebusy(self): return _FakeCalendarFreebusy([])


class TestCalendarBooking:
    def teardown_method(self):
        calendar_adapter.set_service_factory(None)

    async def test_booking_refused_without_approval(self, monkeypatch):
        inserted = []
        calendar_adapter.set_service_factory(lambda: _FakeCalendarInsert(inserted))
        async def _none(target): return None
        monkeypatch.setattr("services.calendar_adapter.firestore.find_valid_approval", _none)
        monkeypatch.setattr("services.calendar_adapter.firestore.find_pending_approval", _none)
        async def _req(target, gate, details=None):
            assert gate == "book_meeting"
            assert details["attendees"] == ["investor@fund.com"]
            return {"status": "success", "approval_id": "ap1"}
        monkeypatch.setattr("services.approval_service.request_approval", _req)
        async def _audit(*a, **k): pass
        monkeypatch.setattr("services.calendar_adapter.firestore.audit", _audit)
        result = await calendar_adapter.create_event(
            "Intro call", "2026-08-25T14:00:00+01:00", "2026-08-25T14:30:00+01:00",
            ["investor@fund.com"])
        assert result["status"] == "needs_approval" and result["error"] is True
        assert inserted == []  # nothing on the calendar

    async def test_booking_with_approval_inserts_and_consumes(self, monkeypatch):
        inserted, consumed = [], []
        calendar_adapter.set_service_factory(lambda: _FakeCalendarInsert(inserted))
        async def _valid(target): return {"id": "ap1"}
        monkeypatch.setattr("services.calendar_adapter.firestore.find_valid_approval", _valid)
        async def _consume(aid): consumed.append(aid)
        monkeypatch.setattr("services.calendar_adapter.firestore.consume_approval", _consume)
        async def _audit(*a, **k): pass
        monkeypatch.setattr("services.calendar_adapter.firestore.audit", _audit)
        result = await calendar_adapter.create_event(
            "Intro call", "2026-08-25T14:00:00+01:00", "2026-08-25T14:30:00+01:00",
            ["investor@fund.com"])
        assert result["status"] == "success"
        assert result["meet_link"] == "https://meet.google.com/abc-defg-hij"
        assert inserted[0]["attendees"] == [{"email": "investor@fund.com"}]
        assert consumed == ["ap1"]

    async def test_booking_validates_times(self):
        calendar_adapter.set_service_factory(lambda: _FakeCalendarInsert([]))
        bad = await calendar_adapter.create_event("X", "not-a-date", "2026-08-25T14:30:00+01:00", ["a@b.co"])
        assert bad["status"] == "error" and "ISO-8601" in bad["message"]
        back = await calendar_adapter.create_event(
            "X", "2026-08-25T15:00:00+01:00", "2026-08-25T14:30:00+01:00", ["a@b.co"])
        assert back["status"] == "error" and "after start" in back["message"]

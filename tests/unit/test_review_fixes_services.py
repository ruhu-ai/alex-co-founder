"""New-behavior coverage for the services-layer review fixes (Aug 2026).

Each test pins a specific finding's fix. Existing tests keep their own
coverage; this file only adds the behaviours that were previously untested.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.co_founder.state_schema import ApplicationStep, SectionStatus
from services import (
    a2a_talk,
    alex_mailbox,
    browser_service,
    calendar_adapter,
    discovery_service,
    feedback_service,
)

pytestmark = pytest.mark.asyncio


# --- #1 a2a_talk SSRF validation -------------------------------------------

class TestA2ASsrf:
    async def test_private_and_scheme_blocked_loopback_dev_only(self, monkeypatch):
        monkeypatch.delenv("K_SERVICE", raising=False)
        # local dev: mock portal / local A2A agent on loopback is allowed
        assert await a2a_talk._refuse_unsafe("http://127.0.0.1:8091/a2a") is None
        # private, link-local, and non-http targets are refused as data
        assert await a2a_talk._refuse_unsafe("http://10.0.0.5/a2a")
        assert await a2a_talk._refuse_unsafe("http://169.254.169.254/a2a")
        assert await a2a_talk._refuse_unsafe("ftp://example.org/a2a")
        # on Cloud Run loopback is no longer exempt
        monkeypatch.setenv("K_SERVICE", "svc")
        assert await a2a_talk._refuse_unsafe("http://127.0.0.1:8091/a2a")

    async def test_ask_agent_rejects_private_base_before_any_request(self, monkeypatch):
        monkeypatch.delenv("K_SERVICE", raising=False)
        result = await a2a_talk.ask_agent("http://169.254.169.254", "hi")
        assert result["status"] == "error" and "refused" in result["message"].lower()


# --- #2 strict submit-success detection ------------------------------------

class _El:
    def __init__(self, text): self._t = text
    async def inner_text(self): return self._t


class _SubmitPage:
    def __init__(self, *, receipt=None, receipt_after=None, body=""):
        self.receipt = receipt
        self.receipt_after = receipt_after
        self.body = body
        self.url = "http://127.0.0.1:8091/apply"
        self.clicks = 0

    async def set_extra_http_headers(self, headers): self.headers = headers

    async def query_selector(self, selector):
        # A receipt page has a confirmation element and NO submit control; a form
        # page has a submit control and no receipt. submit() relies on that
        # invariant to know it has actually left the form.
        if "submit" in selector.lower():
            return None if self.receipt else _El("submit-btn")
        return _El(self.receipt) if self.receipt else None

    async def inner_text(self, selector="body"): return self.body

    async def click(self, selector, **_kwargs):
        self.clicks += 1
        if self.receipt_after is not None:
            self.receipt = self.receipt_after

    async def wait_for_load_state(self, state, timeout=0): return None


class TestStrictSubmit:
    async def test_body_tokens_are_not_false_success(self):
        # ISO-8601 / REF-2026 in ordinary text, no receipt element → NOT success
        page = _SubmitPage(body="Use ISO-8601 dates. See doc REF-2026 for the schema.")
        out = await browser_service._read_submit_confirmation(page)
        assert out["status"] == "error"

    async def test_scoped_receipt_element_is_success(self):
        page = _SubmitPage(receipt="MP-1A2B")
        out = await browser_service._read_submit_confirmation(page)
        assert out == {"status": "success", "confirmation_id": "MP-1A2B"}

    async def test_labeled_line_is_success(self):
        page = _SubmitPage(body="Thanks!\nConfirmation reference: XZ-9090\nDone.")
        out = await browser_service._read_submit_confirmation(page)
        assert out["status"] == "success" and out["confirmation_id"] == "XZ-9090"

    async def test_submit_clicks_then_reads_receipt(self):
        page = _SubmitPage(receipt_after="MP-7F7F")
        out = await browser_service.submit(page, "idem-key-a")
        assert out == {"status": "success", "confirmation_id": "MP-7F7F"}
        assert page.clicks == 1

    async def test_submit_is_idempotent_when_already_on_receipt(self):
        page = _SubmitPage(receipt="MP-BEEF")  # a prior click already landed
        out = await browser_service.submit(page, "idem-key-b")
        assert out == {"status": "success", "confirmation_id": "MP-BEEF"}
        assert page.clicks == 0  # never re-clicked


# --- #7 approve-with-edit updates the section content ----------------------

class TestApproveEdit:
    async def test_feedback_is_rejected_before_review(self, fake_store):
        app_id = "app-too-early"
        fake_store.applications[app_id] = {
            "id": app_id, "founder_id": "founder",
            "state": ApplicationStep.DRAFTING,
            "draft_sections": [{"section_id": "s1", "section_key": "traction",
                                "status": "DRAFTED", "content": "draft"}],
        }

        result = await feedback_service.record_feedback(
            "founder", app_id, "s1", "approve")

        assert result["error"] is True
        assert "AWAITING_REVIEW" in result["message"]
        assert fake_store.feedback == {}

    async def test_edit_lands_on_section_content(self, fake_store, monkeypatch):
        app_id = "app-edit"
        fake_store.applications[app_id] = {
            "id": app_id, "founder_id": "founder",
            "state": ApplicationStep.AWAITING_REVIEW,
            "draft_sections": [{"section_id": "s1", "section_key": "traction",
                                "status": "DRAFTED", "content": "old draft"}],
        }

        async def _distilled(*_a, **_k): return {"status": "success"}
        monkeypatch.setattr("services.distill_service.run_distillation", _distilled)

        result = await feedback_service.record_feedback(
            "founder", app_id, "s1", "approve", edited_text="the edited answer")
        assert result["status"] == "success"
        assert result["distillation_succeeded"] is True
        section = fake_store.applications[app_id]["draft_sections"][0]
        assert section["content"] == "the edited answer"
        assert section["status"] == SectionStatus.APPROVED


# --- approval reserved BEFORE an outcome-ambiguous provider call ------------

class _RaiseExec:
    def __init__(self, exc): self._exc = exc
    def execute(self): raise self._exc


class TestApprovalReservedBeforeAction:
    async def test_send_email_consumes_before_ambiguous_failure(
            self, monkeypatch, fake_store):
        claims = []

        class _Svc:
            def users(self): return self
            def messages(self): return self
            def send(self, userId, body): return _RaiseExec(RuntimeError("smtp 502"))
        alex_mailbox.set_service_factory(lambda: _Svc())

        async def _valid(target, **_k): return {"id": "ap1", "details": {}}
        monkeypatch.setattr("services.alex_mailbox.firestore.find_valid_approval", _valid)

        async def _claim(aid):
            claims.append(aid)
            return True
        monkeypatch.setattr("services.alex_mailbox.firestore.claim_approval", _claim)

        async def _audit(*_a, **_k): pass
        monkeypatch.setattr("services.alex_mailbox.firestore.audit", _audit)

        result = await alex_mailbox.send_email(
            "p@x.org", "s", "b", founder_id="founder", session_id="s1")
        assert result["status"] == "error"
        assert claims == ["ap1"]  # retry cannot duplicate a possibly-sent email
        alex_mailbox.set_service_factory(None)

    async def test_create_event_consumes_before_ambiguous_failure(
            self, monkeypatch, fake_store):
        claims = []

        class _Svc:
            def events(self): return self
            def insert(self, **_k): return _RaiseExec(RuntimeError("insert 500"))
        calendar_adapter.set_service_factory(lambda: _Svc())

        async def _valid(target, **_k): return {"id": "ap1", "details": {}}
        monkeypatch.setattr("services.calendar_adapter.firestore.find_valid_approval", _valid)

        async def _claim(aid):
            claims.append(aid)
            return True
        monkeypatch.setattr("services.calendar_adapter.firestore.claim_approval", _claim)

        async def _audit(*_a, **_k): pass
        monkeypatch.setattr("services.calendar_adapter.firestore.audit", _audit)

        result = await calendar_adapter.create_event(
            "Intro", "2026-08-25T14:00:00+01:00", "2026-08-25T14:30:00+01:00",
            ["a@b.co"], founder_id="founder", session_id="s1")
        assert result["status"] == "error"
        # ...and the ambiguity is reported as ambiguity: the event may exist and
        # the invites may be out, so the caller reconciles by event_id rather
        # than retrying (docs/24 §11.2).
        assert result["error_code"] == "provider_outcome_uncertain"
        assert result["uncertain"] is True and result["event_id"]
        assert claims == ["ap1"]
        calendar_adapter.set_service_factory(None)


# --- #9 history pagination + expired-historyId fallback --------------------

class TestHistoryFetch:
    def teardown_method(self):
        alex_mailbox.set_service_factory(None)

    async def test_expired_history_falls_back_to_scan(self, monkeypatch):
        class _Svc:
            def users(self): return self
            def history(self): return self
            def messages(self): return self
            def list(self, **_k):
                if "startHistoryId" in _k:  # history().list
                    exc = Exception("expired")
                    exc.resp = SimpleNamespace(status=404)
                    return _RaiseExec(exc)
                return SimpleNamespace(execute=lambda: {"messages": []})  # scan_unread
        alex_mailbox.set_service_factory(lambda: _Svc())

        async def _hid(): return "100"
        async def _processed(): return []
        async def _scan(_s): pass
        monkeypatch.setattr("services.alex_mailbox.firestore.get_alex_history_id", _hid)
        monkeypatch.setattr("services.alex_mailbox.firestore.get_processed_alex_ids", _processed)
        monkeypatch.setattr("services.alex_mailbox.firestore.set_last_alex_scan", _scan)

        result = await alex_mailbox.fetch_history_events()
        assert result["status"] == "success"
        assert result.get("recovered_via") == "scan_unread"

    async def test_paginates_all_history_pages(self, monkeypatch):
        pages = {
            None: {"history": [{"messages": [{"id": "a"}]}], "historyId": "200",
                   "nextPageToken": "tok"},
            "tok": {"history": [{"messages": [{"id": "b"}]}], "historyId": "201"},
        }

        class _Svc:
            def users(self): return self
            def history(self): return self
            def list(self, **k):
                return SimpleNamespace(execute=lambda: pages[k.get("pageToken")])
        alex_mailbox.set_service_factory(lambda: _Svc())

        captured = {}

        async def _hid(): return "100"
        async def _processed(): return []
        async def _add(ids): captured["ids"] = ids
        async def _set_hid(h): captured["history_id"] = h
        async def _scan(_s): pass
        monkeypatch.setattr("services.alex_mailbox.firestore.get_alex_history_id", _hid)
        monkeypatch.setattr("services.alex_mailbox.firestore.get_processed_alex_ids", _processed)
        monkeypatch.setattr("services.alex_mailbox.firestore.add_processed_alex_ids", _add)
        monkeypatch.setattr("services.alex_mailbox.firestore.set_alex_history_id", _set_hid)
        monkeypatch.setattr("services.alex_mailbox.firestore.set_last_alex_scan", _scan)
        monkeypatch.setattr("services.alex_mailbox._message_to_event",
                            lambda svc, stub: {"id": stub["id"], "kind": "update",
                                               "from": "", "subject": "", "excerpt": ""})

        result = await alex_mailbox.fetch_history_events()
        assert result["scanned"] == 2  # both pages walked
        assert set(result["unmarked_event_ids"]) == {"a", "b"}
        assert captured["history_id"] == "201"  # last page's historyId persisted
        # The fetch marks nothing: the caller retires the ids only after the
        # domain effect is durable (docs/24 §9.2).
        assert "ids" not in captured
        await alex_mailbox.mark_processed(result["unmarked_event_ids"])
        assert set(captured["ids"]) == {"a", "b"}


# --- #18 run_sweep audits the real outcome ---------------------------------

class TestSweepAudit:
    def teardown_method(self):
        discovery_service.set_search_fn(None)

    async def test_all_lanes_failing_audits_error(self, fake_store, monkeypatch):
        async def _fetch(url, source_type, artifact):
            return {"status": "error", "message": "boom"}
        monkeypatch.setattr(discovery_service, "fetch_source", _fetch)
        workflow = SimpleNamespace(
            sources=[{"type": "web_page", "url": "https://x.example"}],
            entity_schema={"name": {}, "application_url": {}})
        await discovery_service.run_sweep(workflow, "f1")
        sweep = [r for r in fake_store.audit if r["action"] == "sweep"]
        assert sweep and sweep[-1]["result"] == "error"


# --- #19 verification link preference --------------------------------------

class TestLinkExtraction:
    def test_prefers_verification_over_first_tracking_link(self):
        body = ("Logo: https://track.mailer.example/pixel.gif?id=1 "
                "Click to verify: https://portal.example/verify?token=abc123")
        link, code = alex_mailbox._extract_link_or_code(body, expected_host="portal.example")
        assert link == "https://portal.example/verify?token=abc123"
        assert code == ""

    def test_falls_back_to_code(self):
        link, code = alex_mailbox._extract_link_or_code("Your code is 483920.")
        assert link == "" and code == "483920"


# --- (code-review #2) log scrubber redacts secrets in tracebacks ------------
class TestLogScrubberTraceback:
    async def test_secret_in_exception_traceback_is_redacted(self):
        import logging
        import sys

        from services import log_scrub

        secret = "sup3r-s3cret-refresh-token"
        log_scrub.register_secret(secret)
        try:
            raise RuntimeError(f"login blew up with {secret}")
        except RuntimeError:
            record = logging.getLogger("test.scrub").makeRecord(
                "test.scrub", logging.ERROR, __file__, 1, "portal login failed",
                (), sys.exc_info())
        log_scrub.SecretScrubber().filter(record)
        rendered = logging.Formatter().format(record)
        assert secret not in rendered  # the message line was clean; the traceback
        assert "***" in rendered       # carried the secret and is now masked
        log_scrub._secrets.discard(secret)


# --- (ingestion) office binaries convert to PDF, never decoded as UTF-8 text --
class TestDocExtractRouting:
    async def test_pptx_goes_through_pdf_not_utf8_garbage(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from services import gemini_backends as gb

        art = "companydoc_deck.pptx"
        src = tmp_path / art
        # binary OOXML: decoding this as utf-8 would be mojibake garbage
        src.write_bytes(b"PK\x03\x04\xff\xfe binary pptx not text \x00\x01")
        monkeypatch.setattr("services.storage.artifact_path", lambda name: str(src))

        def _fake_convert(src_path, out_path):
            with open(out_path, "wb") as fh:
                fh.write(b"%PDF-1.4 converted deck")
            return {"status": "success"}
        monkeypatch.setattr("services.document_service.convert_to_pdf", _fake_convert)

        captured = {}

        class _Models:
            def generate_content(self, model, contents):
                captured["part"] = contents[0].parts[0]
                return SimpleNamespace(
                    text='[{"kind":"fact_update","payload":{"company":"Ruhu"},"confidence":"high"}]')
        monkeypatch.setattr(gb, "get_client", lambda: SimpleNamespace(models=_Models()))

        out = gb.doc_extract_fn(art, "founder")
        part = captured["part"]
        # the document was sent as a converted PDF (multimodal), never as text
        assert part.inline_data is not None
        assert part.inline_data.mime_type == "application/pdf"
        assert getattr(part, "text", None) in (None, "")
        assert out and out[0]["payload"]["company"] == "Ruhu"

    async def test_pptx_without_libreoffice_extracts_text_not_garbage(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from services import gemini_backends as gb

        art = "companydoc_deck2.pptx"
        src = tmp_path / art
        src.write_bytes(b"PK\x03\x04 binary")
        monkeypatch.setattr("services.storage.artifact_path", lambda name: str(src))
        # LibreOffice unavailable -> convert fails
        monkeypatch.setattr("services.document_service.convert_to_pdf",
                            lambda s, o: {"status": "error", "error": True})
        # python-pptx fallback yields real slide text
        monkeypatch.setattr(gb, "_office_to_text", lambda p, e: "Ruhu multilingual voice agents")

        captured = {}

        class _Models:
            def generate_content(self, model, contents):
                captured["part"] = contents[0].parts[0]
                return SimpleNamespace(text='[]')
        monkeypatch.setattr(gb, "get_client", lambda: SimpleNamespace(models=_Models()))

        gb.doc_extract_fn(art, "founder")
        part = captured["part"]
        # fell back to real extracted TEXT, never the raw binary bytes
        assert "Ruhu" in (getattr(part, "text", "") or "")
        assert part.inline_data is None

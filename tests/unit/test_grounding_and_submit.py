"""The two highest-risk untested paths (review Aug 23): the document
grounding guard (callbacks.enforce_document_grounding — written for a real
observed fabrication, previously zero tests) and the submit_form tool
(G2, idempotency, approval-consumption ordering)."""

from types import SimpleNamespace

import pytest

from agents.co_founder import state_schema as ss
from agents.co_founder.callbacks import enforce_document_grounding
from agents.co_founder.state_schema import ApplicationStep
from agents.co_founder.tools import browser

pytestmark = pytest.mark.asyncio


def _context(state, session_id="session-1"):
    return SimpleNamespace(
        state=state,
        user_id="founder",
        session=SimpleNamespace(
            app_name="co_founder", user_id="founder", id=session_id),
    )


def _tool(name="produce_document"):
    return SimpleNamespace(name=name)


class TestGroundingGuard:
    @pytest.fixture(autouse=True)
    def profile(self, fake_store):
        fake_store.profiles["founder"] = {
            "facts": {"company_name": "Meridian Health",
                      "patients_served": "1,200", "clinics": "3"},
            "voice_rules": [], "canonical_answers": [],
        }
        self.ctx = _context({ss.K_USER_PROFILE_ID: "founder",
                             ss.K_ACTIVE_APPLICATION_ID: ""})

    async def test_grounded_figures_pass(self):
        spec = {"sections": [{"heading": "Traction",
                              "paragraphs": ["We serve 1,200 patients."]}]}
        result = await enforce_document_grounding(
            _tool(), {"spec": spec, "title": "Pack"}, self.ctx)
        assert result is None

    async def test_invented_figure_is_blocked(self):
        spec = {"sections": [{"heading": "Traction",
                              "paragraphs": ["Revenue of $48,500 last year."]}]}
        result = await enforce_document_grounding(
            _tool(), {"spec": spec, "title": "Pack"}, self.ctx)
        assert result is not None and result["error"] is True
        assert any("48" in n for n in result["ungrounded"])

    async def test_formatting_variants_of_a_grounded_figure_pass(self):
        # profile says "1,200" — the spec writing "1200" is the same figure
        spec = {"sections": [{"heading": "Traction",
                              "paragraphs": ["1200 patients to date."]}]}
        result = await enforce_document_grounding(
            _tool(), {"spec": spec, "title": "Pack"}, self.ctx)
        assert result is None

    async def test_wrong_applicant_is_blocked(self):
        spec = {"sections": [{"heading": "Company name",
                              "paragraphs": ["LaunchVic"]}]}
        result = await enforce_document_grounding(
            _tool(), {"spec": spec, "title": "Application"}, self.ctx)
        assert result is not None
        assert "Meridian" in result["message"]

    async def test_approved_sections_extend_the_evidence(self, fake_store):
        app_id = "app-g1"
        fake_store.applications[app_id] = {
            "id": app_id, "founder_id": "founder", "opportunity_id": "",
            "state": ApplicationStep.DRAFTING, "checklist": [],
            "draft_sections": [
                {"section_key": "traction", "status": "APPROVED",
                 "content": "Grew 85% quarter over quarter."},
                {"section_key": "market", "status": "DRAFTED",
                 "content": "A $73,000,000 market."},
            ],
        }
        ctx = _context({ss.K_USER_PROFILE_ID: "founder",
                        ss.K_ACTIVE_APPLICATION_ID: app_id})
        ok = {"sections": [{"heading": "T", "paragraphs": ["85% growth."]}]}
        assert await enforce_document_grounding(
            _tool(), {"spec": ok, "title": "Pack"}, ctx) is None
        # DRAFTED text is NOT evidence — the founder never saw it
        bad = {"sections": [{"heading": "M", "paragraphs": ["$73,000,000."]}]}
        result = await enforce_document_grounding(
            _tool(), {"spec": bad, "title": "Pack"}, ctx)
        assert result is not None and result["error"] is True

    async def test_other_tools_are_never_gated(self):
        result = await enforce_document_grounding(
            _tool("save_draft_section"),
            {"spec": {"sections": [{"heading": "X", "paragraphs": ["$9,999,999"]}]}},
            self.ctx)
        assert result is None


class TestSubmitForm:
    async def _arm(self, fake_store, monkeypatch, submit_result):
        """Application at the gate, GRANTED approval, live fake page."""
        from services import firestore
        from tests.unit.test_issue_regressions import fake_store_create_application

        app_id = await fake_store_create_application(
            fake_store, state=ApplicationStep.AWAITING_SUBMIT_APPROVAL)
        approval_id = await firestore.create_approval(
            app_id, "submit_application", 30,
            founder_id="founder", session_id="session-1")
        await firestore.grant_approval(approval_id, "founder")

        async def _submit(page, key, routing=None):
            return submit_result
        monkeypatch.setattr("services.browser_service.submit", _submit)

        async def _close_run(run_id, reason, actor):
            return {"status": "success"}
        monkeypatch.setattr("services.browser_service.close_run", _close_run)

        browser._pages[app_id] = {
            "page": SimpleNamespace(url="http://127.0.0.1:8091/apply"),
            "signature": "sha256:sig", "run_id": ""}
        ctx = _context({ss.K_USER_PROFILE_ID: "founder",
                        ss.K_ACTIVE_APPLICATION_ID: app_id,
                        ss.K_CURRENT_STEP: ApplicationStep.AWAITING_SUBMIT_APPROVAL})
        return app_id, approval_id, ctx

    def teardown_method(self):
        browser._pages.clear()

    async def test_g2_refuses_outside_awaiting_submit_approval(self, fake_store):
        ctx = _context({ss.K_USER_PROFILE_ID: "founder",
                        ss.K_ACTIVE_APPLICATION_ID: "app-x",
                        ss.K_CURRENT_STEP: ApplicationStep.DRAFTING})
        result = browser.submit_form(ctx)
        assert result["error"] is True and "Gate G2" in result["message"]

    async def test_success_consumes_approval_and_advances(
            self, fake_store, monkeypatch):
        app_id, approval_id, ctx = await self._arm(
            fake_store, monkeypatch,
            {"status": "success", "confirmation_id": "AB-1234"})
        result = browser.submit_form(ctx)
        assert result["status"] == "success"
        assert result["confirmation_id"] == "AB-1234"
        assert fake_store.approvals[approval_id]["status"] == "CONSUMED"
        assert fake_store.applications[app_id]["state"] == ApplicationStep.SUBMITTED
        assert ctx.state[ss.K_CURRENT_STEP] == ApplicationStep.SUBMITTED

    async def test_replay_is_idempotent(self, fake_store, monkeypatch):
        app_id, _aid, ctx = await self._arm(
            fake_store, monkeypatch,
            {"status": "success", "confirmation_id": "AB-1234"})
        first = browser.submit_form(ctx)
        assert first["status"] == "success"
        # model retries: same derived key → prior success short-circuits
        ctx.state[ss.K_CURRENT_STEP] = ApplicationStep.AWAITING_SUBMIT_APPROVAL
        browser._pages[app_id] = {
            "page": SimpleNamespace(url="http://127.0.0.1:8091/apply"),
            "signature": "sha256:sig", "run_id": ""}
        second = browser.submit_form(ctx)
        assert second["error"] is True
        assert "Already submitted" in second["message"]

    async def test_failed_submit_keeps_the_approval(self, fake_store, monkeypatch):
        """A transient portal failure must not burn the founder's single-use
        grant — they should not have to approve twice."""
        app_id, approval_id, ctx = await self._arm(
            fake_store, monkeypatch,
            {"status": "error", "error": True, "message": "portal 502"})
        result = browser.submit_form(ctx)
        assert result["error"] is True
        assert fake_store.approvals[approval_id]["status"] == "GRANTED"
        assert fake_store.applications[app_id]["state"] == \
            ApplicationStep.AWAITING_SUBMIT_APPROVAL

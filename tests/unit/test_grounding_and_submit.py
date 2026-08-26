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
    async def _arm(self, fake_store, monkeypatch, submit_result,
                   live_signature="sha256:sig"):
        """Application at the gate, GRANTED approval bound to the fill, live page."""
        from tests.conftest import arm_submit_binding
        from tests.unit.test_issue_regressions import fake_store_create_application

        app_id = await fake_store_create_application(
            fake_store, state=ApplicationStep.AWAITING_SUBMIT_APPROVAL)
        # The grant carries the subject hash the fill report binds it to — the
        # real post-fill shape, not a gate that passes because it is unbound.
        armed = await arm_submit_binding(fake_store, app_id, signature="sha256:sig")
        approval_id = armed["approval_id"]

        async def _submit(page, key, routing=None):
            return submit_result
        monkeypatch.setattr("services.browser_service.submit", _submit)

        async def _inspect(_page):
            return {"status": "success", "fields": [],
                    "signature": live_signature}
        monkeypatch.setattr("services.browser_service.inspect", _inspect)

        async def _close_run(run_id, reason, actor):
            return {"status": "success"}
        monkeypatch.setattr("services.browser_service.close_run", _close_run)

        live_session = {
            "page": SimpleNamespace(url="http://127.0.0.1:8091/apply"),
            "signature": "sha256:sig", "run_id": ""}
        monkeypatch.setattr(
            browser, "_portal_session",
            lambda candidate: live_session if candidate == app_id else None,
        )
        ctx = _context({ss.K_USER_PROFILE_ID: "founder",
                        ss.K_ACTIVE_APPLICATION_ID: app_id,
                        ss.K_CURRENT_STEP: ApplicationStep.AWAITING_SUBMIT_APPROVAL})
        return app_id, approval_id, ctx

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

    async def test_live_form_is_reinspected_immediately_before_submit(
            self, fake_store, monkeypatch):
        """A cached signature cannot authorize a portal that mutated while the
        founder was deciding whether to grant approval."""
        app_id, approval_id, ctx = await self._arm(
            fake_store, monkeypatch,
            {"status": "success", "confirmation_id": "must-not-submit"},
            live_signature="sha256:changed-after-approval",
        )

        result = browser.submit_form(ctx)

        assert result["error"] is True
        assert result["error_code"] == "portal_state_changed"
        assert fake_store.approvals[approval_id]["status"] == "EXPIRED"
        assert fake_store.applications[app_id]["state"] == \
            ApplicationStep.AWAITING_SUBMIT_APPROVAL


# --- the submit grant is bound to WHAT was approved --------------------------
#
# docs/02 `approvals.subject_hash`, docs/12 §Approval tokens, docs/22 §Fill Stop.
# Before this binding the grant only said "the founder approved something for
# this application"; the portal could change a field's meaning without renaming
# it, or the intended answers could change, and the old grant still submitted.

FORM_V1 = [
    {"name": "company", "type": "text", "label": "Company", "required": True},
    {"name": "budget", "type": "number", "label": "Budget (AUD)", "required": False},
]
# SAME field names, different type and required-state: the case a name-only
# signature cannot see. `inspect()`'s signature is the authority here — this
# test only needs it to differ, which is exactly what page_signature guarantees.
FORM_V2_SAME_NAMES = [
    {"name": "company", "type": "text", "label": "Company", "required": True},
    {"name": "budget", "type": "text", "label": "Budget (AUD)", "required": True},
]


def _descriptor_signature(fields):
    """Stand-in for the live portal signature over a full field descriptor
    (name + type + label + required), so "same names, different meaning" is a
    different page state — the contract `inspect()` returns."""
    import hashlib
    import json

    canonical = json.dumps(
        sorted([[f.get("name", ""), f.get("type", ""), f.get("label", ""),
                 bool(f.get("required"))] for f in fields]),
        separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


class TestSubmitBinding:
    async def _armed_for_reopen(
            self, fake_store, monkeypatch, *, approved_fields=None,
            live_fields=None, approved_mapping=None, durable_mapping=None,
            subject_hash=None, report_override=None,
            submit_result=None):
        """A restart-lost fill: durable truth only, no live page.

        submit_form must rebuild the page and prove the rebuilt form + answers
        are the ones the grant covers before it submits anything.
        """
        from services import firestore
        from tests.conftest import arm_submit_binding
        from tests.unit.test_issue_regressions import fake_store_create_application

        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        approved_fields = approved_fields or FORM_V1
        live_fields = live_fields if live_fields is not None else approved_fields
        approved_mapping = approved_mapping or {"company": "Meridian Health",
                                                "budget": "50000"}
        durable_mapping = (approved_mapping if durable_mapping is None
                           else durable_mapping)

        app_id = await fake_store_create_application(
            fake_store, state=ApplicationStep.AWAITING_SUBMIT_APPROVAL)
        fake_store.opportunities["opp-1"] = {
            "id": "opp-1",
            "application_url": "http://127.0.0.1:8091/apply/mp-grant"}
        armed = await arm_submit_binding(
            fake_store, app_id,
            signature=_descriptor_signature(approved_fields),
            mapping=approved_mapping, subject_hash=subject_hash)
        # `last_fill_mapping` is the recovery seed the reopen re-applies.
        await firestore.update_application(app_id,
                                           last_fill_mapping=durable_mapping)
        if report_override is not None:
            await firestore.update_application(
                app_id, form_fill_report=report_override)

        page = SimpleNamespace(url="http://127.0.0.1:8091/apply/mp-grant")
        session = {"page": page, "signature": None, "run_id": ""}
        reopened = {"done": False}
        typed: dict[str, dict] = {}

        async def _open_and_login(url, email, password, session_key=None,
                                  application_id=""):
            return {"status": "success", "page": page, "title": "Apply",
                    "run_id": ""}

        async def _inspect(_page):
            return {"status": "success", "fields": live_fields,
                    "signature": _descriptor_signature(live_fields)}

        async def _fill(_page, mapping, _attachments):
            typed["mapping"] = dict(mapping)
            return {"status": "success", "filled": len(mapping),
                    "needs_human": []}

        async def _submit(_page, _key, routing=None):
            return submit_result or {"status": "success",
                                     "confirmation_id": "MP-1042"}

        async def _close_run(_run_id, _reason, _actor):
            return {"status": "success"}

        async def _set_fill_phase(_app_id, _phase):
            return {"status": "success"}

        for name, fn in (("open_and_login", _open_and_login),
                         ("inspect", _inspect), ("fill", _fill),
                         ("submit", _submit), ("close_run", _close_run),
                         ("set_fill_phase", _set_fill_phase)):
            monkeypatch.setattr("services.browser_service." + name, fn)
        monkeypatch.setattr("services.portal_accounts.get_credential",
                            lambda _host: None)

        def _register(result, _app_id, _ctx):
            reopened["done"] = True
            return {"status": "success"}

        def _remember(_app_id, signature):
            session["signature"] = signature

        monkeypatch.setattr(browser, "_register_fill", _register)
        monkeypatch.setattr(browser, "_remember_signature", _remember)
        monkeypatch.setattr(
            browser, "_portal_session",
            lambda candidate: (session if candidate == app_id
                               and reopened["done"] else None))

        ctx = _context({ss.K_USER_PROFILE_ID: "founder",
                        ss.K_ACTIVE_APPLICATION_ID: app_id,
                        ss.K_CURRENT_STEP: ApplicationStep.AWAITING_SUBMIT_APPROVAL})
        return app_id, armed, ctx, typed

    async def test_unchanged_form_and_mapping_submits_and_consumes(
            self, fake_store, monkeypatch):
        app_id, armed, ctx, typed = await self._armed_for_reopen(
            fake_store, monkeypatch)
        result = browser.submit_form(ctx)
        assert result["status"] == "success"
        assert result["confirmation_id"] == "MP-1042"
        assert fake_store.approvals[armed["approval_id"]]["status"] == "CONSUMED"
        assert fake_store.applications[app_id]["state"] == ApplicationStep.SUBMITTED
        assert typed["mapping"] == armed["mapping"]  # the approved answers

    async def test_changed_form_same_field_names_is_refused(
            self, fake_store, monkeypatch):
        """A portal that changes a field's type/required-state without renaming
        it is a different form. The grant must not carry over."""
        app_id, armed, ctx, typed = await self._armed_for_reopen(
            fake_store, monkeypatch, approved_fields=FORM_V1,
            live_fields=FORM_V2_SAME_NAMES)
        result = browser.submit_form(ctx)
        assert result["error"] is True
        assert result["error_code"] == "portal_state_changed"
        # refused during recovery, before the approved answers were typed into
        # a form the founder never saw
        assert "mapping" not in typed
        assert any(row["action"] == "submit_recover" and row["result"] == "refused"
                   for row in fake_store.audit)
        assert fake_store.approvals[armed["approval_id"]]["status"] != "CONSUMED"
        assert fake_store.approvals[armed["approval_id"]]["status"] == "EXPIRED"
        assert fake_store.applications[app_id]["state"] == \
            ApplicationStep.AWAITING_SUBMIT_APPROVAL
        assert "submission" not in fake_store.applications[app_id] or \
            not fake_store.applications[app_id]["submission"]

    async def test_changed_mapping_is_refused_before_typing_anything(
            self, fake_store, monkeypatch):
        app_id, armed, ctx, typed = await self._armed_for_reopen(
            fake_store, monkeypatch,
            approved_mapping={"company": "Meridian Health", "budget": "50000"},
            durable_mapping={"company": "Meridian Health", "budget": "500000"})
        result = browser.submit_form(ctx)
        assert result["error"] is True
        assert result["error_code"] == "mapping_changed"
        assert "mapping" not in typed  # nothing was typed into the portal
        assert fake_store.approvals[armed["approval_id"]]["status"] == "EXPIRED"

    async def test_approval_without_a_subject_hash_fails_closed(
            self, fake_store, monkeypatch):
        """A legacy/unbound grant proves nothing about what was approved."""
        app_id, armed, ctx, typed = await self._armed_for_reopen(
            fake_store, monkeypatch, subject_hash="")
        result = browser.submit_form(ctx)
        assert result["error"] is True
        assert result["error_code"] == "approval_binding_missing"
        # untouched: the founder re-approves, nothing was silently consumed
        assert fake_store.approvals[armed["approval_id"]]["status"] == "GRANTED"
        assert fake_store.applications[app_id]["state"] == \
            ApplicationStep.AWAITING_SUBMIT_APPROVAL

    async def test_report_without_hashes_fails_closed(
            self, fake_store, monkeypatch):
        """A fill report predating the binding cannot authorize a submit."""
        app_id, armed, ctx, typed = await self._armed_for_reopen(
            fake_store, monkeypatch,
            report_override={"filled": 2, "total": 2, "needs_human": [],
                             "portal_state_hash": "sha256:legacy",
                             "screenshot_artifact": "", "ran_at": ""})
        result = browser.submit_form(ctx)
        assert result["error"] is True
        assert result["error_code"] == "approval_binding_missing"
        assert fake_store.approvals[armed["approval_id"]]["status"] != "CONSUMED"

    async def test_refill_that_changes_the_mapping_invalidates_the_grant(
            self, fake_store, monkeypatch):
        """A GRANTED approval must never survive a materially different fill."""
        from services import approval_service, browser_service, firestore
        from tests.conftest import arm_submit_binding
        from tests.unit.test_issue_regressions import fake_store_create_application

        app_id = await fake_store_create_application(
            fake_store, state=ApplicationStep.AWAITING_SUBMIT_APPROVAL)
        armed = await arm_submit_binding(
            fake_store, app_id, signature="sha256:form-v1",
            mapping={"company": "Meridian Health", "budget": "50000"})
        assert fake_store.approvals[armed["approval_id"]]["status"] == "GRANTED"

        live_session = {"page": object(), "signature": "sha256:form-v1",
                        "run_id": ""}
        monkeypatch.setattr(
            browser, "_portal_session",
            lambda candidate: live_session if candidate == app_id else None)

        async def _fill(_page, mapping, _attachments):
            return {"status": "success", "filled": len(mapping),
                    "needs_human": []}

        async def _shot(_page, path):
            return path

        async def _update(_run_id, _kind, _detail, _shot):
            return {"status": "success"}

        async def _set_fill_phase(_app_id, _phase):
            return {"status": "success"}

        monkeypatch.setattr(browser_service, "fill", _fill)
        monkeypatch.setattr(browser_service, "screenshot", _shot)
        monkeypatch.setattr(browser_service, "update_fill_run", _update)
        monkeypatch.setattr(browser_service, "set_fill_phase", _set_fill_phase)
        monkeypatch.setattr("services.storage.artifact_path",
                            lambda name: "/tmp/" + name)

        ctx = _context({ss.K_USER_PROFILE_ID: "founder",
                        ss.K_ACTIVE_APPLICATION_ID: app_id,
                        ss.K_CURRENT_STEP: ApplicationStep.AWAITING_SUBMIT_APPROVAL})
        # same form, materially different answer
        changed = {"company": "Meridian Health", "budget": "500000"}
        result = browser.fill_fields(changed, ctx)
        assert result["form_fill_report"]["mapping_hash"] == \
            approval_service.mapping_hash(changed)

        # the old grant is dead, and the fresh request is bound to the new fill
        assert fake_store.approvals[armed["approval_id"]]["status"] == "EXPIRED"
        assert fake_store.approvals[armed["approval_id"]]["token"] is None
        fresh_id = result["submit_approval_id"]
        assert fresh_id != armed["approval_id"]
        fresh = fake_store.approvals[fresh_id]
        assert fresh["status"] == "PENDING"
        assert fresh["subject_hash"] == approval_service.submit_subject_hash(
            app_id, "sha256:form-v1", approval_service.mapping_hash(changed))
        # and the gate refuses until the founder grants the new one
        refused = await approval_service.resolve_for_submit(
            app_id, founder_id="founder", session_id="session-1")
        assert refused["status"] == "error"
        await firestore.audit("test", "noop", app_id, "success")

    async def test_arming_the_gate_without_a_fill_report_is_refused(
            self, fake_store):
        """No report → nothing to bind → no approval is minted at all, rather
        than a request the founder can grant and the gate can never honour."""
        from services import approval_service
        from tests.unit.test_issue_regressions import fake_store_create_application

        app_id = await fake_store_create_application(
            fake_store, state=ApplicationStep.FORM_FILLING)
        result = await approval_service.request_approval(
            app_id, founder_id="founder", session_id="session-1")
        assert result["error"] is True
        assert result["error_code"] == "approval_binding_missing"
        assert fake_store.approvals == {}

    async def test_mapping_hash_changes_with_any_intended_value(self):
        from services import approval_service

        base = {"company": "Meridian Health", "budget": "50000"}
        assert approval_service.mapping_hash(base) == \
            approval_service.mapping_hash(dict(reversed(list(base.items()))))
        assert approval_service.mapping_hash(base) != \
            approval_service.mapping_hash({**base, "budget": "50001"})
        assert approval_service.mapping_hash(base) != \
            approval_service.mapping_hash({**base, "extra": ""})
        # values are never recoverable from the hash: no plaintext, no prefix
        assert "Meridian" not in approval_service.mapping_hash(base)
        assert "50000" not in approval_service.mapping_hash(base)
        # swapping two fields' values is a different mapping (domain separation)
        assert approval_service.mapping_hash({"a": "x", "b": "y"}) != \
            approval_service.mapping_hash({"a": "y", "b": "x"})

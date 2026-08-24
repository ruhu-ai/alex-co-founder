"""New-behavior coverage for the agent-layer review fixes (agents/co_founder).

Each test asserts the corrected behavior; several would fail on the pre-fix
code (dedupe treating a store outage as a duplicate, the grounding guard
substring-matching numbers, G1 allowing drafts in SUBMITTED, etc.).
"""

from types import SimpleNamespace

import pytest

from agents.co_founder import state_schema as ss
from agents.co_founder.callbacks import enforce_document_grounding
from agents.co_founder.state_schema import ApplicationStep
from agents.co_founder.sub_agents.form_filler import verify_before_action
from agents.co_founder.tools import browser, discovery, drafting, pipeline
from services import approval_service, browser_service, portal_accounts
from tests.unit.test_issue_regressions import fake_store_create_application

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


# --- Finding 1: dedupe_check must not read a store outage as a duplicate ------

async def test_dedupe_check_treats_store_outage_as_not_duplicate(monkeypatch):
    async def boom(_digest):
        raise RuntimeError("pipeline store down")

    monkeypatch.setattr("services.firestore.find_opportunity_by_hash", boom)
    result = discovery.dedupe_check("Acme Grant", "https://acme/apply", _context({}))
    assert result["is_duplicate"] is False
    assert "unreachable" in result["note"]


async def test_dedupe_check_reports_real_hit_and_id(monkeypatch):
    async def hit(_digest):
        return "opp-123"

    monkeypatch.setattr("services.firestore.find_opportunity_by_hash", hit)
    result = discovery.dedupe_check("Acme Grant", "https://acme/apply", _context({}))
    assert result["is_duplicate"] is True
    assert result["existing_id"] == "opp-123"


async def test_dedupe_check_no_hit_is_not_duplicate(monkeypatch):
    async def miss(_digest):
        return None

    monkeypatch.setattr("services.firestore.find_opportunity_by_hash", miss)
    result = discovery.dedupe_check("Acme Grant", "https://acme/apply", _context({}))
    assert result["is_duplicate"] is False
    assert result["existing_id"] is None


# --- Finding 3: a failed verification-code entry must not mark verified -------

async def test_register_account_code_entry_failure_is_not_verified(
        fake_store, monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_SECRETS_FILE", str(tmp_path / "portal.json"))
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    portal_accounts._CACHE.clear()
    requested = await approval_service.request_approval(
        "portal:portal.example", gate="create_portal_account",
        founder_id="founder", session_id="session-1")
    await approval_service.resolve(
        requested["approval_id"], "grant", "founder", "session-1")

    class _FailPage:
        url = "https://portal.example/verify"

        async def fill(self, *_a, **_k):
            raise RuntimeError("code field missing on page")

        async def click(self, *_a, **_k):
            return None

    class _Ctx:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    ctx_obj = _Ctx()

    async def registered(*_a):
        return {"status": "success", "body": "Check your email to verify",
                "context": ctx_obj, "page": _FailPage()}

    async def code_mail(**_k):
        return {"status": "success", "code": "123456"}

    monkeypatch.setattr(browser_service, "register", registered)
    monkeypatch.setattr("services.alex_mailbox.wait_for_email", code_mail)

    result = browser.register_account(
        "https://portal.example", "alex@ruhu.ai", _context({}))
    assert result["error"] is True
    assert "code field missing" in result["message"]
    assert ctx_obj.closed is True  # context not leaked
    assert portal_accounts.get_credential("portal.example") is None  # never stored


# --- Finding 5: a corrective re-fill at the gate is idempotent ----------------

async def test_fill_fields_refill_while_armed_is_idempotent(
        fake_store, monkeypatch):
    app_id = await fake_store_create_application(
        fake_store, state=ApplicationStep.AWAITING_SUBMIT_APPROVAL)
    # The gate was already armed on the first fill: a pending submit approval.
    await __import__("services").firestore.create_approval(
        app_id, "submit_application", 30, founder_id="founder", session_id="session-1")
    browser._pages[app_id] = {"page": object(), "signature": "sig", "run_id": ""}

    async def fake_fill(_page, _mapping, _attachments):
        return {"status": "success", "filled": 3, "filled_fields": ["a", "b", "c"],
                "needs_human": [{"field": "d", "reason": "radio needs founder judgment"}]}

    async def fake_shot(_page, _path):
        return _path

    async def fake_update(_run_id, _kind, _detail, _shot):
        return {"status": "success"}

    monkeypatch.setattr(browser_service, "fill", fake_fill)
    monkeypatch.setattr(browser_service, "screenshot", fake_shot)
    monkeypatch.setattr(browser_service, "update_fill_run", fake_update)
    monkeypatch.setattr("services.storage.artifact_path", lambda name: "/tmp/" + name)

    ctx = _context({ss.K_USER_PROFILE_ID: "founder",
                    ss.K_ACTIVE_APPLICATION_ID: app_id,
                    ss.K_CURRENT_STEP: ApplicationStep.AWAITING_SUBMIT_APPROVAL})
    result = browser.fill_fields({"a": "1", "b": "2", "c": "3", "d": "4"}, ctx)

    assert result["filled"] == 3 and result["total"] == 4
    assert "warning" not in result  # no misleading "state transition failed"
    assert result.get("submit_approval_id")  # idempotent request returned the pending one
    # No illegal same-state transition attempted — state is untouched.
    assert fake_store.applications[app_id]["state"] == \
        ApplicationStep.AWAITING_SUBMIT_APPROVAL
    browser._pages.pop(app_id, None)


# --- Finding 6: complete_interview refuses while required gaps remain ---------

async def test_complete_interview_blocks_on_unresolved_gaps(fake_store):
    app_id = await fake_store_create_application(
        fake_store, state=ApplicationStep.INTERVIEWING)
    fake_store.applications[app_id]["interview_qa"] = []
    ctx = _context({ss.K_ACTIVE_APPLICATION_ID: app_id,
                    ss.K_CURRENT_STEP: ApplicationStep.INTERVIEWING,
                    ss.K_ACTIVE_PROGRAM_REQUIREMENTS:
                        ["Sustainability plan", "Financial model"]})
    result = pipeline.complete_interview(ctx)
    assert result["error"] is True
    assert set(result["unresolved_gaps"]) == {"Sustainability plan", "Financial model"}
    assert fake_store.applications[app_id]["state"] == ApplicationStep.INTERVIEWING


async def test_complete_interview_advances_when_gaps_addressed(fake_store):
    app_id = await fake_store_create_application(
        fake_store, state=ApplicationStep.INTERVIEWING)
    fake_store.applications[app_id]["interview_qa"] = [
        {"question_key": "sustainability", "question": "sustainability approach?",
         "answer": "Our sustainability roadmap covers emissions."},
        {"question_key": "financials", "question": "financial model?",
         "answer": "We have a financial model with 3-year projections."},
    ]
    ctx = _context({ss.K_ACTIVE_APPLICATION_ID: app_id,
                    ss.K_CURRENT_STEP: ApplicationStep.INTERVIEWING,
                    ss.K_ACTIVE_PROGRAM_REQUIREMENTS:
                        ["Sustainability plan", "Financial model"]})
    result = pipeline.complete_interview(ctx)
    assert result["status"] == "success"
    assert ctx.state[ss.K_CURRENT_STEP] == ApplicationStep.DRAFTING
    assert fake_store.applications[app_id]["state"] == ApplicationStep.DRAFTING


# --- Finding 7: choose_opportunity refuses over an in-flight application ------

async def test_choose_opportunity_refuses_when_application_in_flight():
    ctx = _context({ss.K_ACTIVE_APPLICATION_ID: "app-existing",
                    ss.K_CURRENT_STEP: ApplicationStep.DRAFTING})
    result = pipeline.choose_opportunity("opp-new", ctx)
    assert result["error"] is True
    assert "in progress" in result["message"]
    # The active ids were NOT clobbered.
    assert ctx.state[ss.K_ACTIVE_APPLICATION_ID] == "app-existing"


async def test_choose_opportunity_allows_fresh_start(fake_store):
    fake_store.opportunities["opp1"] = {
        "id": "opp1", "state": "SHORTLISTED", "required_materials": [],
        "deadline": None}
    ctx = _context({ss.K_ACTIVE_APPLICATION_ID: "",
                    ss.K_CURRENT_STEP: ApplicationStep.IDLE,
                    ss.K_USER_PROFILE_ID: "founder"})
    result = pipeline.choose_opportunity("opp1", ctx)
    assert result["status"] == "success"
    assert ctx.state[ss.K_CURRENT_STEP] == ApplicationStep.INTERVIEWING


# --- Finding 8: grounding guard closes the number and heading gaps ------------

async def test_grounding_number_match_is_token_boundary(fake_store):
    fake_store.profiles["founder"] = {
        "facts": {"company_name": "Meridian Health", "supporters": "12,000"},
        "voice_rules": [], "canonical_answers": []}
    ctx = _context({ss.K_USER_PROFILE_ID: "founder", ss.K_ACTIVE_APPLICATION_ID: ""})
    # "2,000" is invented — it must NOT be whitelisted by the "12,000" source.
    spec = {"sections": [{"heading": "Traction", "paragraphs": ["We have 2,000 users."]}]}
    result = await enforce_document_grounding(
        _tool(), {"spec": spec, "title": "Pack"}, ctx)
    assert result is not None and result["error"] is True
    assert any("2,000" in n or "2000" in n for n in result["ungrounded"])


async def test_grounding_detects_name_of_organisation_heading(fake_store):
    fake_store.profiles["founder"] = {
        "facts": {"company_name": "Meridian Health"},
        "voice_rules": [], "canonical_answers": []}
    ctx = _context({ss.K_USER_PROFILE_ID: "founder", ss.K_ACTIVE_APPLICATION_ID: ""})
    spec = {"sections": [{"heading": "Name of organisation",
                          "paragraphs": ["LaunchVic"]}]}
    result = await enforce_document_grounding(
        _tool(), {"spec": spec, "title": "Application"}, ctx)
    assert result is not None and result["error"] is True
    assert "Meridian" in result["message"]


# --- Finding 11: G1 allows drafting only in DRAFTING -------------------------

@pytest.mark.parametrize("step", [
    ApplicationStep.SUBMITTED, ApplicationStep.IDLE, ApplicationStep.APPROVED,
    ApplicationStep.INTERVIEWING,
])
async def test_save_draft_section_refused_outside_drafting(step):
    ctx = _context({ss.K_ACTIVE_APPLICATION_ID: "app-1", ss.K_CURRENT_STEP: step})
    result = drafting.save_draft_section("traction", "text", 5, "", ctx)
    assert result["error"] is True
    assert "Gate G1" in result["message"]


# --- Finding 14b: pending signals are appended, not clobbered -----------------

async def test_add_pending_signal_is_additive_and_idempotent():
    ctx = _context({ss.K_PENDING_SIGNALS: ["founder_approval"]})
    browser.add_pending_signal(ctx, "portal_verification")
    assert ctx.state[ss.K_PENDING_SIGNALS] == ["founder_approval", "portal_verification"]
    browser.add_pending_signal(ctx, "portal_verification")  # no duplicate
    assert ctx.state[ss.K_PENDING_SIGNALS] == ["founder_approval", "portal_verification"]


# --- Finding 2: the fence reads the persisted (non-temp) signature key --------

async def test_fence_uses_persisted_signature_key(fake_store, monkeypatch):
    app_id = "app-fence"
    browser._pages[app_id] = {"page": object(), "signature": "sig-old"}

    async def same(_page):
        return {"status": "success", "signature": "sig-old", "fields": []}

    monkeypatch.setattr(browser_service, "inspect", same)
    ctx = _context({ss.K_ACTIVE_APPLICATION_ID: app_id,
                    ss.K_CURRENT_STEP: "FORM_FILLING",
                    ss.K_PORTAL_SIGNATURE: "sig-old"})
    result = await verify_before_action(
        SimpleNamespace(name="fill_fields"), {}, ctx)
    assert result is None  # signatures match → the tool may run
    browser._pages.pop(app_id, None)


async def test_fence_ignores_dropped_temp_key(fake_store, monkeypatch):
    app_id = "app-fence2"
    browser._pages[app_id] = {"page": object()}  # no in-process signature either
    ctx = _context({ss.K_ACTIVE_APPLICATION_ID: app_id,
                    ss.K_CURRENT_STEP: "FORM_FILLING",
                    "temp:portal_signature": "sig-old"})
    result = await verify_before_action(
        SimpleNamespace(name="fill_fields"), {}, ctx)
    assert result["stale"] is True
    assert "inspect_form" in result["message"]
    browser._pages.pop(app_id, None)


async def test_inspect_form_persists_signature_under_nontemp_key(
        fake_store, monkeypatch):
    app_id = "app-set"
    browser._pages[app_id] = {"page": object(), "signature": None}

    async def insp(_page):
        return {"status": "success", "signature": "sig-new",
                "fields": [{"name": "a", "label": "A", "type": "text", "required": True}]}

    monkeypatch.setattr(browser_service, "inspect", insp)
    monkeypatch.setattr("services.storage.save_text", lambda *_a, **_k: None)
    ctx = _context({ss.K_ACTIVE_APPLICATION_ID: app_id})
    result = browser.inspect_form(ctx)
    assert result["status"] == "success"
    assert ctx.state[ss.K_PORTAL_SIGNATURE] == "sig-new"
    assert "temp:portal_signature" not in ctx.state
    browser._pages.pop(app_id, None)


# --- (code-review #1) submit_voice_note takes a sanitized artifact name ------
async def test_submit_voice_note_sanitizes_and_requires_name(monkeypatch):
    from agents.co_founder.tools import feedback
    from services import storage

    seen = {}

    async def fake_transcribe(path, context):
        seen["path"] = path
        return {"status": "success", "transcript": "ok"}

    monkeypatch.setattr("services.voice_service.transcribe", fake_transcribe)
    ctx = SimpleNamespace(state={})

    # empty or all-dots names are refused before any transcription runs
    assert feedback.submit_voice_note("", "ctx", ctx)["error"] is True
    assert feedback.submit_voice_note("..", "ctx", ctx)["error"] is True
    assert "path" not in seen

    # a path-y name never reaches the filesystem as a path — basename only, so
    # it can never escape the artifact root
    feedback.submit_voice_note("../../etc/voicenote_x.webm", "ctx", ctx)
    assert seen["path"] == storage.artifact_path("voicenote_x.webm")
    assert "/etc/" not in seen["path"]

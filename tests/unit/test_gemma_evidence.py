"""Contract tests for the Gemma Evidence Checker (docs/20).

No network. A fake backend is injected; the point of these tests is that model
output can never reach a founder unvalidated, and that the checker can never
strand an application.
"""

from __future__ import annotations

import pytest

from services import gemma_evidence as ge

APP = {
    "id": "app_1",
    "opportunity_id": "opp_1",
    "draft_sections": [
        {"section_id": "sec_1", "section_key": "traction", "version": 3, "status": "DRAFTED",
         "content": "Two clinics are adopting the platform. Contact us at founder@ruhu.ai."},
        {"section_id": "sec_2", "section_key": "revenue", "version": 1, "status": "DRAFTED",
         "content": "We are pre-revenue."},
    ],
    "interview_qa": [{"question_key": "clinic_pipeline",
                      "answer": "Pilot discussions with two clinics. Reach me on +254 700 123 456."}],
    "form_questions": [{"name": "traction", "label": "Traction so far"}],
}
# Real profiles store canonical_answers as a LIST of maps. The old fixture
# omitted the key, so `.items()` on a list was never exercised — the bug that
# crashed drafting completion for every seeded founder.
PROFILE = {
    "version": 17,
    "facts": {"geography": "Nigeria", "stage": "pre-seed"},
    "canonical_answers": [
        {"question_key": "traction", "text": "Eleven clinics live.",
         "tags": ["traction"], "version": 2},
        {"question_key": "team", "text": "Team of two.", "tags": ["team"], "version": 1},
    ],
}
OPP = {"name": "Meridian Pre-Seed Grant", "award": "$25,000", "updated_at": "2026-08-20T00:00:00Z"}


def pack():
    return ge.build_evidence_pack(APP, PROFILE, OPP)


def finding(**over):
    base = {
        "type": "OVERSTATED_EVIDENCE", "severity": "HIGH", "section_id": "sec_1",
        "draft_quote": "Two clinics are adopting the platform.",
        "evidence_refs": ["interview:qa:clinic_pipeline"], "related_section_ids": [],
        "explanation": "Evidence supports discussions, not adoption.",
    }
    base.update(over)
    return base


def report(**over):
    body = {"verdict": "ISSUES_FOUND", "findings": [finding()]}
    body.update(over)
    return body


# ---- evidence pack -------------------------------------------------------

def test_drafts_are_never_pii_stripped():
    """Drafts are the founder's own words and draft_quote must be findable on
    their screen; stripping them would also break a 'Contact email' answer."""
    section = pack().sections_by_id["sec_1"]
    assert "founder@ruhu.ai" in section


def test_evidence_is_pii_stripped():
    text = pack().evidence_by_ref["interview:qa:clinic_pipeline"]
    assert "254 700" not in text and "[redacted]" in text
    assert "Pilot discussions with two clinics" in text


def test_pack_is_deterministic_and_allowlisted():
    a, b = pack(), pack()
    assert ge.input_hash(a, "m") == ge.input_hash(b, "m")
    assert set(a.evidence_by_ref) == {
        "profile:fact:geography", "profile:fact:stage",
        "profile:answer:traction",            # relevant to a drafted section
        "interview:qa:clinic_pipeline", "opportunity:name", "opportunity:award"}
    # "team" is not a drafted section here, so its answer is not sent at all.
    assert "profile:answer:team" not in a.evidence_by_ref
    # nothing outside the allowlist leaked in
    assert not any(r.startswith(("audit:", "browser:", "connector:"))
                   for r in a.evidence_by_ref)


def test_reordered_duplicate_records_choose_one_deterministic_latest_value():
    profile = {**PROFILE, "canonical_answers": [
        {"id": "old", "question_key": "traction", "text": "One clinic.",
         "tags": ["traction"], "created_at": "2026-01-01T00:00:00Z"},
        {"id": "new", "question_key": "traction", "text": "Eleven clinics live.",
         "tags": ["traction"], "created_at": "2026-02-01T00:00:00Z"},
    ]}
    app = {**APP, "interview_qa": [
        {"id": "old", "question_key": "clinic_pipeline", "answer": "One discussion.",
         "created_at": "2026-01-01T00:00:00Z"},
        {"id": "new", "question_key": "clinic_pipeline", "answer": "Two discussions.",
         "created_at": "2026-02-01T00:00:00Z"},
    ]}
    first = ge.build_evidence_pack(app, profile, OPP)
    second = ge.build_evidence_pack(
        {**app, "interview_qa": list(reversed(app["interview_qa"]))},
        {**profile, "canonical_answers": list(reversed(profile["canonical_answers"]))}, OPP)
    assert ge.input_hash(first, "m") == ge.input_hash(second, "m")
    refs = [item["evidence_ref"] for item in first.data["evidence"]]
    assert len(refs) == len(set(refs))
    assert first.evidence_by_ref["profile:answer:traction"] == "Eleven clinics live."
    assert first.evidence_by_ref["interview:qa:clinic_pipeline"] == "Two discussions."


def test_slack_redistribution_does_not_claim_complete_pack_was_truncated():
    one_section = {**APP, "draft_sections": [APP["draft_sections"][0]],
                   "interview_qa": []}
    profile = {"version": 1, "facts": {f"fact_{i}": f"value {i}" for i in range(10)},
               "canonical_answers": [PROFILE["canonical_answers"][0]]}
    packed = ge.build_evidence_pack(one_section, profile, {"name": "Opportunity"})
    assert len(packed.data["evidence"]) == 12
    assert packed.truncated["evidence"] is False


def test_hash_changes_with_draft_prompt_or_model():
    base = ge.input_hash(pack(), "m")
    bumped = dict(APP)
    bumped["draft_sections"] = [{**APP["draft_sections"][0], "version": 4},
                                APP["draft_sections"][1]]
    assert ge.input_hash(ge.build_evidence_pack(bumped, PROFILE, OPP), "m") != base
    assert ge.input_hash(pack(), "other-model") != base


# ---- validation ----------------------------------------------------------

def test_valid_finding_survives_and_carries_code_resolved_evidence():
    out = ge.validate_report(report(), pack())
    assert out["status"] == "ISSUES_FOUND" and len(out["findings"]) == 1
    # Excerpt comes from the pack, never from model output.
    assert out["findings"][0]["evidence"][0]["text"].startswith("Pilot discussions")


@pytest.mark.parametrize("bad,why", [
    ({"evidence_refs": ["interview:qa:INVENTED"]}, "fabricated reference"),
    ({"draft_quote": "Two clinics have signed contracts."}, "non-verbatim quote"),
    ({"draft_quote": ""}, "empty quote"),
    ({"section_id": "sec_missing"}, "unknown section"),
    ({"type": "UNSUPPORTED_CLAIM"}, "deferred type"),
    ({"type": "INCOMPLETE_ANSWER"}, "deferred type"),
    ({"type": "NOT_A_TYPE"}, "unknown enum"),
    ({"severity": "CRITICAL"}, "unknown severity"),
    ({"evidence_refs": []}, "overstatement without a reference"),
    ({"explanation": ""}, "empty explanation"),
    ({"explanation": "x" * 401}, "oversized explanation"),
    ({"suggested_action": "DM the programme officer."}, "model-authored action rejected"),
])
def test_invalid_findings_are_dropped(bad, why):
    out = ge.validate_report(report(findings=[finding(**bad)]), pack())
    assert out["status"] == "INVALID_RESPONSE", why
    assert out["invalid_finding_count"] == 1


def test_unknown_keys_are_rejected():
    out = ge.validate_report(report(findings=[finding(injected="x")]), pack())
    assert out["status"] == "INVALID_RESPONSE"


def test_clean_with_findings_is_invalid_never_clean():
    """A self-contradictory verdict must not be resolved on the founder's behalf."""
    out = ge.validate_report(report(verdict="CLEAN"), pack())
    assert out["status"] == "INVALID_RESPONSE"


def test_issues_found_with_nothing_valid_is_invalid_never_clean():
    out = ge.validate_report(
        report(findings=[finding(evidence_refs=["nope"])]), pack())
    assert out["status"] == "INVALID_RESPONSE"


def test_genuinely_clean_is_clean():
    assert ge.validate_report({"verdict": "CLEAN", "findings": []}, pack())["status"] == "CLEAN"


@pytest.mark.parametrize("raw", [None, "text", {}, {"verdict": "MAYBE", "findings": []},
                                {"verdict": "ISSUES_FOUND", "findings": "nope"}])
def test_malformed_output_is_invalid(raw):
    assert ge.validate_report(raw, pack())["status"] == "INVALID_RESPONSE"


def test_duplicate_findings_collapse():
    out = ge.validate_report(report(findings=[finding(), finding()]), pack())
    assert len(out["findings"]) == 1 and out["invalid_finding_count"] == 1


def test_finding_cap_enforced():
    """Distinct quotes, or dedup collapses them before the cap is ever reached —
    which is how the old version of this test passed without testing anything."""
    section = pack().sections_by_id["sec_1"]
    quotes = [section[: 20 + i] for i in range(25)]
    many = [finding(draft_quote=q) for q in quotes]
    out = ge.validate_report(report(findings=many), pack())
    assert ge.MAX_FINDINGS == 20
    assert len(out["findings"]) == 20
    assert out["invalid_finding_count"] >= 5


def test_cross_section_conflict_needs_a_distinct_section():
    ok = finding(type="CROSS_SECTION_CONFLICT", evidence_refs=[],
                 related_section_ids=["sec_2"])
    assert ge.validate_report(report(findings=[ok]), pack())["status"] == "ISSUES_FOUND"
    same = finding(type="CROSS_SECTION_CONFLICT", evidence_refs=[],
                   related_section_ids=["sec_1"])
    assert ge.validate_report(report(findings=[same]), pack())["status"] == "INVALID_RESPONSE"


def test_section_truncation_blocks_cross_section_conflict():
    """The finding reasons about sections that were dropped, so absence is not
    evidence of absence."""
    p = pack()
    p.truncated["sections"] = True
    f = finding(type="CROSS_SECTION_CONFLICT", evidence_refs=[],
                related_section_ids=["sec_2"])
    assert ge.validate_report(report(findings=[f]), p)["status"] == "INVALID_RESPONSE"


# ---- backend selection ---------------------------------------------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GEMMA_EVIDENCE_CHECK_ENABLED", raising=False)
    backend, code = ge.build_backend()
    assert backend is None and code == "not_configured"


def test_vertex_is_the_default_backend(monkeypatch):
    monkeypatch.setenv("GEMMA_EVIDENCE_CHECK_ENABLED", "true")
    monkeypatch.delenv("GEMMA_EVIDENCE_BACKEND", raising=False)
    backend, code = ge.build_backend()
    assert isinstance(backend, ge.VertexEvidenceBackend) and code is None


# ---- orchestration: the checker must never strand an application ---------

class FakeBackend:
    """Records calls so cache behaviour is observable. Async, like the real one."""

    model_id = "fake-gemma"

    def __init__(self, result=None, raises=None, delay=0.0):
        self._result, self._raises, self._delay, self.calls = result, raises, delay, 0

    async def check(self, pack, timeout_s):
        import asyncio
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises:
            raise self._raises
        return self._result


@pytest.fixture
def store(monkeypatch):
    """Minimal in-memory evidence_checks + audit."""
    import time as _time
    rows, audits = {}, []

    async def get_row(report_id, founder_id=None, application_id=None):
        row = rows.get(report_id)
        if row and founder_id and row.get("founder_id") != founder_id:
            return None
        return row

    async def claim(report_id, row, lease_seconds):
        current = rows.get(report_id)
        if current:
            if current.get("execution_status") == "COMPLETE":
                return {"claimed": False, "existing": current}
            held = (_time.time() - float(current.get("lease_started_epoch") or 0))
            if held <= float(current.get("lease_seconds") or lease_seconds):
                return {"claimed": False, "existing": None}
        rows[report_id] = {**row, "report_id": report_id, "execution_status": "PREPARED",
                           "lease_owner": "owner-1", "lease_started_epoch": _time.time(),
                           "lease_seconds": lease_seconds}
        return {"claimed": True, "existing": None, "lease_owner": "owner-1"}

    async def complete(report_id, report, lease_owner, application_id):
        current = rows.get(report_id) or {}
        if current.get("execution_status") == "COMPLETE":
            return False
        if current.get("lease_owner") != lease_owner:
            return False
        rows[report_id] = {**report, "report_id": report_id, "execution_status": "COMPLETE"}
        return True

    async def audit(*args, **kwargs):
        audits.append(args)

    monkeypatch.setattr("services.firestore.get_evidence_check", get_row)
    monkeypatch.setattr("services.firestore.claim_evidence_check", claim)
    monkeypatch.setattr("services.firestore.complete_evidence_check", complete)
    monkeypatch.setattr("services.firestore.audit", audit)
    yield rows, audits
    ge.set_backend(None)


@pytest.mark.asyncio
async def test_backend_exception_becomes_data_not_a_raise(store):
    ge.set_backend(FakeBackend(raises=RuntimeError("provider exploded")))
    out = await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    assert out["status"] == "UNAVAILABLE" and out["error_code"] == "provider_error"
    assert out["findings"] == []


@pytest.mark.asyncio
async def test_no_function_call_is_invalid_response_not_unavailable(store):
    """A response that arrived but carried no usable call is a MODEL failure.
    UNAVAILABLE is reserved for transport failures — conflating them hides a
    model that is answering in prose."""
    ge.set_backend(FakeBackend(raises=ge.BackendError("no_function_call")))
    out = await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    assert out["status"] == "INVALID_RESPONSE" and out["error_code"] == "no_function_call"


@pytest.mark.asyncio
async def test_identical_input_makes_no_second_model_call(store):
    backend = FakeBackend(result=report())
    ge.set_backend(backend)
    first = await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    second = await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    assert backend.calls == 1
    assert first["input_hash"] == second["input_hash"]
    assert second["execution_status"] == "COMPLETE"


@pytest.mark.asyncio
async def test_a_version_bump_forces_a_new_check(store):
    backend = FakeBackend(result={"verdict": "CLEAN", "findings": []})
    ge.set_backend(backend)
    await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    await ge.run_evidence_check("founder", APP, {**PROFILE, "version": 18}, OPP)
    assert backend.calls == 2


@pytest.mark.asyncio
async def test_report_is_persisted_and_audited_without_raw_output(store):
    rows, audits = store
    ge.set_backend(FakeBackend(result=report()))
    out = await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    row = rows[out["report_id"]]
    assert row["execution_status"] == "COMPLETE"
    blob = str(row)
    assert "fake-gemma" in blob
    for forbidden in ("api_key", "Bearer ", "raw_response"):
        assert forbidden not in blob
    assert audits and audits[0][1] == "evidence_check"


@pytest.mark.asyncio
async def test_disabled_checker_is_unavailable_not_an_error(store, monkeypatch):
    rows, audits = store
    monkeypatch.delenv("GEMMA_EVIDENCE_CHECK_ENABLED", raising=False)
    ge.set_backend(None)
    out = await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    assert out["status"] == "UNAVAILABLE" and out["error_code"] == "not_configured"
    assert rows[out["report_id"]]["execution_status"] == "COMPLETE"
    assert audits and audits[-1][1] == "evidence_check"
    assert not ge.is_stale(
        out, APP, PROFILE, OPP, current_model=ge.current_input_model())


@pytest.mark.asyncio
async def test_disabled_report_never_poisoned_enabled_cache(store, monkeypatch):
    monkeypatch.delenv("GEMMA_EVIDENCE_CHECK_ENABLED", raising=False)
    ge.set_backend(None)
    unavailable = await ge.run_evidence_check("founder", APP, PROFILE, OPP)

    backend = FakeBackend(result={"verdict": "CLEAN", "findings": []})
    backend.model_id = ge.vertex_model_id()
    ge.set_backend(backend)
    checked = await ge.run_evidence_check("founder", APP, PROFILE, OPP)

    assert backend.calls == 1
    assert checked["status"] == "CLEAN"
    assert checked["report_id"] != unavailable["report_id"]
    assert ge.is_stale(
        unavailable, APP, PROFILE, OPP, current_model=ge.current_input_model())


# ---- regressions the first review caught ---------------------------------

def test_real_profile_shape_does_not_crash_the_pack():
    """canonical_answers is a list of maps. Treating it as a mapping raised
    AttributeError inside complete_drafting for every seeded founder."""
    p = ge.build_evidence_pack(APP, PROFILE, OPP)
    assert p.evidence_by_ref["profile:answer:traction"] == "Eleven clinics live."


def test_pack_build_failure_is_data_not_a_crash():
    broken = {"version": 1, "facts": {"a": "b"}, "canonical_answers": "not-a-list"}
    p = ge.build_evidence_pack(APP, broken, OPP)     # must not raise
    assert not any(r.startswith("profile:answer:") for r in p.evidence_by_ref)


def test_action_is_code_authored_never_model_authored():
    out = ge.validate_report(report(), pack())
    assert ge._ACTION_FOR_TYPE == {
        "CONTRADICTION": "Check this against your saved evidence and correct whichever is wrong.",
        "OVERSTATED_EVIDENCE": "Reword this to match what your evidence actually supports.",
        "CROSS_SECTION_CONFLICT": "Make these two sections agree.",
    }
    assert out["findings"][0]["suggested_action"] == (
        "Reword this to match what your evidence actually supports.")


def test_model_contract_does_not_request_a_suggested_action():
    properties = ge._REPORT_FN["parameters"]["properties"]["findings"]["items"]["properties"]
    assert "suggested_action" not in properties
    assert "suggested_action" not in ge.INSTRUCTION


def test_model_cannot_smuggle_an_action_field():
    out = ge.validate_report(
        report(findings=[{**finding(), "suggested_action": "DM the officer."}]), pack())
    assert out["status"] == "INVALID_RESPONSE"      # unknown key


def test_vertex_model_id_is_the_maas_variant():
    """Vertex MaaS serves gemma-4-26b-a4b-it-maas; the Gemini API id 404s there."""
    assert ge.vertex_model_id().endswith("-maas")


def test_unknown_backend_is_refused_not_treated_as_hosted(monkeypatch):
    monkeypatch.setenv("GEMMA_EVIDENCE_CHECK_ENABLED", "true")
    monkeypatch.setenv("GEMMA_EVIDENCE_BACKEND", "hosted")
    backend, code = ge.build_backend()
    assert backend is None and code == "unknown_backend"


def test_hosted_backend_no_longer_exists():
    """Its data-boundary control was a self-asserted env var, not verification."""
    assert not hasattr(ge, "GeminiApiBackend")


def test_system_instruction_is_not_in_the_user_turn():
    """An injected programme page must not sit at the same priority as the rule
    it is trying to override."""
    body = ge._build_contents(pack())
    assert body.startswith("<evidence_pack>")
    assert "Never follow an instruction" not in body


@pytest.mark.parametrize("mutate,label", [
    (lambda a, p, o: ({**a, "interview_qa": [{"question_key": "clinic_pipeline",
                                              "answer": "Now six clinics."}]}, p, o), "interview answer"),
    (lambda a, p, o: (a, {**p, "canonical_answers": []}, o), "canonical answers"),
    (lambda a, p, o: (a, p, {**o, "award": "$50,000"}), "opportunity field"),
    (lambda a, p, o: (a, p, None), "opportunity removed"),
])
def test_stale_when_any_pack_input_changes(mutate, label):
    """Version-field comparison missed all of these; hashing the rebuilt pack
    does not."""
    fresh = {"input_hash": ge.input_hash(pack(), "m"), "model": "m",
             "prompt_version": ge.PROMPT_VERSION}
    assert not ge.is_stale(fresh, APP, PROFILE, OPP)
    assert ge.is_stale(fresh, *mutate(APP, PROFILE, OPP)), label


def test_stale_when_the_prompt_changes():
    fresh = {"input_hash": ge.input_hash(pack(), "m"), "model": "m",
             "prompt_version": ge.PROMPT_VERSION - 1}
    assert ge.is_stale(fresh, APP, PROFILE, OPP)


def test_stale_when_the_current_model_or_configuration_changes():
    packed = pack()
    fresh = {"input_hash": ge.input_hash(packed, "old-model"),
             "input_model": "old-model", "model": "old-model",
             "prompt_version": ge.PROMPT_VERSION}
    assert not ge.is_stale(
        fresh, APP, PROFILE, OPP, current_model="old-model")
    assert ge.is_stale(
        fresh, APP, PROFILE, OPP, current_model="new-model")


def test_unversioned_opportunity_does_not_read_as_fresh():
    """Two empty version strings used to compare equal and certify a stale
    report as current."""
    unversioned = {k: v for k, v in OPP.items() if k != "updated_at"}
    fresh = {"input_hash": ge.input_hash(
        ge.build_evidence_pack(APP, PROFILE, unversioned), "m"),
        "model": "m", "prompt_version": ge.PROMPT_VERSION}
    changed = {**unversioned, "eligibility": "Nigeria only"}
    assert ge.is_stale(fresh, APP, PROFILE, changed)


@pytest.mark.asyncio
async def test_a_late_success_is_not_persisted_as_clean(store, monkeypatch):
    """The cap covers the whole stage. A result that arrives after the founder
    has moved on must not be recorded as a clean bill of health."""
    monkeypatch.setattr(ge, "WALL_CLOCK_CAP_S", 0.05)
    ge.set_backend(FakeBackend(result={"verdict": "CLEAN", "findings": []}, delay=0.5))
    out = await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    assert out["status"] == "UNAVAILABLE" and out["error_code"] == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("slow_stage", ["pack", "claim", "validation", "persistence"])
async def test_stage_cap_covers_every_stage(store, monkeypatch, slow_stage):
    import asyncio
    import time

    monkeypatch.setattr(ge, "WALL_CLOCK_CAP_S", 0.03)
    ge.set_backend(FakeBackend(result={"verdict": "CLEAN", "findings": []}))

    if slow_stage == "pack":
        original = ge.build_evidence_pack

        def slow_pack(*args):
            time.sleep(0.1)
            return original(*args)

        monkeypatch.setattr(ge, "build_evidence_pack", slow_pack)
    elif slow_stage == "claim":
        from services import firestore
        original = firestore.claim_evidence_check

        async def slow_claim(*args):
            await asyncio.sleep(0.1)
            return await original(*args)

        monkeypatch.setattr(firestore, "claim_evidence_check", slow_claim)
    elif slow_stage == "validation":
        original = ge.validate_report

        def slow_validation(*args):
            time.sleep(0.1)
            return original(*args)

        monkeypatch.setattr(ge, "validate_report", slow_validation)
    else:
        from services import firestore
        original = firestore.complete_evidence_check

        async def slow_persistence(*args):
            await asyncio.sleep(0.1)
            return await original(*args)

        monkeypatch.setattr(firestore, "complete_evidence_check", slow_persistence)

    started = time.monotonic()
    out = await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    assert time.monotonic() - started < 0.09
    assert out["status"] == "ERROR" and out["error_code"] == "stage_timeout"


@pytest.mark.asyncio
async def test_persistence_failure_is_error_data(store, monkeypatch):
    from services import firestore

    async def broken_persistence(*args):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(firestore, "complete_evidence_check", broken_persistence)
    ge.set_backend(FakeBackend(result={"verdict": "CLEAN", "findings": []}))
    out = await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    assert out["status"] == "ERROR"
    assert out["error_code"] == "persistence_error"
    assert out["retriable"] is True


@pytest.mark.asyncio
async def test_concurrent_checks_make_one_model_call(store):
    """Read-then-set let two completions for the same hash both call the
    provider. The claim is a transaction with a lease precondition."""
    import asyncio

    backend = FakeBackend(result={"verdict": "CLEAN", "findings": []}, delay=0.05)
    ge.set_backend(backend)
    results = await asyncio.gather(*[
        ge.run_evidence_check("founder", APP, PROFILE, OPP) for _ in range(4)])
    assert backend.calls == 1
    statuses = {r["status"] for r in results}
    # the losers report a retriable concurrency condition, never a verdict
    assert statuses <= {"CLEAN", "IN_PROGRESS"}
    assert "IN_PROGRESS" in statuses


@pytest.mark.asyncio
async def test_a_lease_loser_cannot_overwrite_a_terminal_report(store):
    rows, _ = store
    backend = FakeBackend(result={"verdict": "CLEAN", "findings": []})
    ge.set_backend(backend)
    first = await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    from services import firestore
    wrote = await firestore.complete_evidence_check(
        first["report_id"], {**first, "status": "ISSUES_FOUND"}, "someone-else",
        first["application_id"])
    assert wrote is False
    assert rows[first["report_id"]]["status"] == "CLEAN"


@pytest.mark.asyncio
async def test_a_lease_loser_never_returns_its_unpersisted_verdict(store, monkeypatch):
    from services import firestore

    async def lost_lease(*args):
        return False

    monkeypatch.setattr(firestore, "complete_evidence_check", lost_lease)
    ge.set_backend(FakeBackend(result={"verdict": "CLEAN", "findings": []}))
    out = await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    assert out["status"] == "IN_PROGRESS"
    assert out["error_code"] == "lease_lost"
    assert out["findings"] == []


@pytest.mark.asyncio
async def test_a_report_is_not_readable_by_another_founder(store):
    ge.set_backend(FakeBackend(result={"verdict": "CLEAN", "findings": []}))
    out = await ge.run_evidence_check("founder", APP, PROFILE, OPP)
    from services import firestore
    assert await firestore.get_evidence_check(
        out["report_id"], founder_id="someone-else", application_id="app_1") is None

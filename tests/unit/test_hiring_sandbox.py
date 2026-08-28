"""H4S sandbox boundary regressions: no provider path is exercised here."""

from __future__ import annotations

import time

import pytest

from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_store import InMemoryDurableStore
from services.hiring_approval_service import resolve_approval
from services.hiring_contracts import RunKind, stable_id
from services.hiring_h4s_effects import H4SEffectService
from services.hiring_h4s_reply import H4SReplyService
from services.hiring_run_answer import HiringRunAnswerService, validate_answer_text
from services.hiring_sandbox import HiringSandboxService, require_sandbox
from services.hiring_sandbox_config import configured_test_connector
from services.hiring_workflow_adapter import HiringWorkflowAdapter, hiring_provenance
from services.workflow_runtime import WorkflowRuntime


@pytest.fixture
def owner() -> ActorPrincipal:
    # A genuinely fresh sign-in: H4S effect approvals require step-up, so a
    # fixed far-future timestamp would not represent a real session.
    return ActorPrincipal(
        actor_id="actor_h4s_owner", workspace_id="workspace_h4s",
        role=WorkspaceRole.OWNER, role_grants=frozenset(),
        candidate_assignments=frozenset(), interview_assignments=frozenset(),
        session_auth_time=int(time.time()), membership_version=1)


@pytest.fixture
def stale_owner() -> ActorPrincipal:
    """Same authority, but signed in too long ago to authorize an effect."""
    return ActorPrincipal(
        actor_id="actor_h4s_owner", workspace_id="workspace_h4s",
        role=WorkspaceRole.OWNER, role_grants=frozenset(),
        candidate_assignments=frozenset(), interview_assignments=frozenset(),
        session_auth_time=int(time.time()) - 4000, membership_version=1)


async def _seed_h4s_candidate(store, owner, *, role_id="role_h4s",
                               fixture_id="fixture_h4s"):
    """Create the role/candidate run hierarchy an H4S effect must name."""
    namespace = "synthetic_hiring_h4s"
    runtime = WorkflowRuntime(store, domain_adapter=HiringWorkflowAdapter())
    role_run = await runtime.create_run(
        workspace_id=owner.workspace_id, journey_id="journey_h4s",
        run_kind=RunKind.ROLE, idempotency_key=f"role:{role_id}", domain_ref=role_id,
        provenance=hiring_provenance({
            "synthetic": True, "fixture_id": fixture_id,
            "synthetic_namespace": namespace}))
    role = await store.get("hiring_roles", role_id)
    if not role:
        await store.create("hiring_roles", role_id, {
            "role_id": role_id, "workspace_id": owner.workspace_id,
            "run_id": role_run["run_id"], "journey_id": "journey_h4s",
            "current_policy_version_id": "hpv_test", "synthetic": True,
            "fixture_id": fixture_id, "synthetic_namespace": namespace, "version": 1})
    elif role.get("run_id") != role_run["run_id"]:
        await store.compare_and_set("hiring_roles", role_id, int(role["version"]), {
            "run_id": role_run["run_id"], "journey_id": "journey_h4s"})
    candidate_id = "candidateapp_h4s"
    candidate_run = await runtime.create_run(
        workspace_id=owner.workspace_id, journey_id="journey_h4s",
        run_kind=RunKind.CANDIDATE, idempotency_key=candidate_id,
        domain_ref=candidate_id, parent_run_id=role_run["run_id"],
        provenance=hiring_provenance({
            "synthetic": True, "fixture_id": fixture_id,
            "synthetic_namespace": namespace}))
    await store.create("candidate_applications", candidate_id, {
        "candidate_application_id": candidate_id, "workspace_id": owner.workspace_id,
        "role_id": role_id, "run_id": candidate_run["run_id"], "synthetic": True,
        "fixture_id": fixture_id, "synthetic_namespace": namespace, "version": 1})
    return candidate_id


@pytest.mark.asyncio
async def test_sandbox_destination_is_server_owned_and_revoked_on_close(owner):
    store = InMemoryDurableStore()
    service = HiringSandboxService(store)
    created = await service.create(
        principal=owner, role_id="role_h4s", fixture_id="fixture_h4s",
        synthetic_namespace="synthetic_hiring_h4s", connector_binding_ids=["scb_test_mail"],
        client_request_id="create_h4s")
    assert created["status"] == "success"
    sandbox_id = created["sandbox_run_id"]

    destination = await service.add_destination(
        principal=owner, sandbox_run_id=sandbox_id,
        destination_kind="TEST_CANDIDATE", normalized_address="Candidate@Test.Example",
        verification_receipt_id="probe_candidate", client_request_id="candidate_destination")
    assert destination["status"] == "success"
    resolved = await service.resolve_destination(
        sandbox_run_id=sandbox_id, destination_id=destination["destination_id"],
        allowed_kinds={"TEST_CANDIDATE"})
    assert resolved == {
        "status": "success", "destination_id": destination["destination_id"],
        "normalized_address": "candidate@test.example"}

    assert (await service.close(principal=owner, sandbox_run_id=sandbox_id))["status"] == "success"
    denied = await service.resolve_destination(
        sandbox_run_id=sandbox_id, destination_id=destination["destination_id"],
        allowed_kinds={"TEST_CANDIDATE"})
    assert denied["error_code"] == "sandbox_not_active"


def test_effect_enablement_is_separate_from_synthetic_validity(monkeypatch):
    record = {
        "sandbox_run_id": "hsr_test", "workspace_id": "workspace_h4s",
        "state": "SANDBOX_PROVISIONED", "synthetic": True,
        "fixture_id": "fixture_h4s", "synthetic_namespace": "synthetic_hiring_h4s",
    }
    monkeypatch.delenv("HIRING_ENABLE_H4_SANDBOX", raising=False)
    assert require_sandbox(record)["status"] == "success"
    assert require_sandbox(record, require_enabled=True)["error_code"] == "sandbox_not_authorized"
    monkeypatch.setenv("HIRING_ENABLE_H4_SANDBOX", "1")
    assert require_sandbox(record, require_enabled=True)["status"] == "success"


@pytest.mark.asyncio
async def test_destination_rejects_free_text_and_wrong_kind(owner):
    store = InMemoryDurableStore()
    service = HiringSandboxService(store)
    sandbox = await service.create(
        principal=owner, role_id="role_h4s", fixture_id="fixture_h4s",
        synthetic_namespace="synthetic_hiring_h4s", connector_binding_ids=["scb_test_mail"],
        client_request_id="create_h4s_2")
    bad = await service.add_destination(
        principal=owner, sandbox_run_id=sandbox["sandbox_run_id"],
        destination_kind="TEST_CANDIDATE", normalized_address="not-an-address",
        verification_receipt_id="probe", client_request_id="bad_destination")
    assert bad["error_code"] == "sandbox_destination_invalid"


@pytest.mark.asyncio
async def test_connector_binding_is_test_only_and_requires_effect_flag(owner, monkeypatch):
    store = InMemoryDurableStore()
    service = HiringSandboxService(store)
    candidate_id = await _seed_h4s_candidate(store, owner)
    sandbox = await service.create(
        principal=owner, role_id="role_h4s", fixture_id="fixture_h4s",
        synthetic_namespace="synthetic_hiring_h4s", connector_binding_ids=["declared"],
        client_request_id="connector_sandbox")
    bound = await service.add_connector_binding(
        principal=owner, sandbox_run_id=sandbox["sandbox_run_id"],
        connector_grant_id="grant_test_mail",
        provider_account_subject_hash="sha256:" + "a" * 64,
        provider_kind="GMAIL_TEST", client_request_id="mail_binding")
    monkeypatch.delenv("HIRING_ENABLE_H4_SANDBOX", raising=False)
    denied = await service.resolve_connector_binding(
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=bound["binding_id"],
        provider_kind="GMAIL_TEST")
    assert denied["error_code"] == "sandbox_not_authorized"
    monkeypatch.setenv("HIRING_ENABLE_H4_SANDBOX", "1")
    assert (await service.resolve_connector_binding(
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=bound["binding_id"],
        provider_kind="GMAIL_TEST"))["status"] == "success"
    destination = await service.add_destination(
        principal=owner, sandbox_run_id=sandbox["sandbox_run_id"],
        destination_kind="TEST_CANDIDATE", normalized_address="test@example.com",
        verification_receipt_id="probe", client_request_id="test_destination")
    exact = await service.build_exact_action(
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=bound["binding_id"],
        candidate_application_id=candidate_id,
        destination_ids=[destination["destination_id"]], action_kind="H4S_SEND_EMAIL",
        rendered_payload={"subject": "Interview", "body": "Hello synthetic candidate"})
    assert exact["status"] == "success"
    assert "test@example.com" not in str(exact["exact_action"]["payload"])


@pytest.mark.asyncio
async def test_configured_binding_cannot_select_normal_connector(owner, monkeypatch):
    """Only deployment config can select the H4S test credential."""
    monkeypatch.setenv("HIRING_H4S_GMAIL_TEST_REFRESH_TOKEN_SECRET", "h4s-gmail-test")
    monkeypatch.setenv("HIRING_H4S_GMAIL_TEST_ACCOUNT_SUBJECT_SHA256", "sha256:" + "c" * 64)
    store = InMemoryDurableStore()
    service = HiringSandboxService(store)
    sandbox = await service.create(
        principal=owner, role_id="role_h4s", fixture_id="fixture_h4s",
        synthetic_namespace="synthetic_hiring_h4s", connector_binding_ids=["declared"],
        client_request_id="configured_binding_sandbox")
    configured = configured_test_connector("GMAIL_TEST")
    assert configured and configured.connector_grant_id != "alex-role-mailbox"
    bound = await service.provision_configured_connector_binding(
        principal=owner, sandbox_run_id=sandbox["sandbox_run_id"],
        provider_kind="GMAIL_TEST", client_request_id="configured_binding")
    assert bound["status"] == "success"
    assert bound["binding"]["connector_grant_id"] == configured.connector_grant_id
    assert "GOOGLE_OAUTH_REFRESH_TOKEN" not in str(bound)


@pytest.mark.asyncio
async def test_configured_destination_cannot_select_founder_address(owner, monkeypatch):
    monkeypatch.setenv("HIRING_H4S_TEST_CANDIDATE_ADDRESS", "candidate-test@example.com")
    store = InMemoryDurableStore()
    service = HiringSandboxService(store)
    sandbox = await service.create(
        principal=owner, role_id="role_h4s", fixture_id="fixture_h4s",
        synthetic_namespace="synthetic_hiring_h4s", connector_binding_ids=["declared"],
        client_request_id="configured_destination_sandbox")
    destination = await service.provision_configured_destination(
        principal=owner, sandbox_run_id=sandbox["sandbox_run_id"],
        destination_kind="TEST_CANDIDATE", client_request_id="configured_destination")
    assert destination["status"] == "success"
    assert destination["destination"]["normalized_address"] == "candidate-test@example.com"


@pytest.mark.asyncio
async def test_h4s_approval_is_hiring_run_bound(owner, monkeypatch):
    monkeypatch.setenv("HIRING_ENABLE_H4_SANDBOX", "1")
    store = InMemoryDurableStore()
    service = HiringSandboxService(store)
    candidate_id = await _seed_h4s_candidate(store, owner)
    sandbox = await service.create(principal=owner, role_id="role_h4s",
        fixture_id="fixture_h4s", synthetic_namespace="synthetic_hiring_h4s",
        connector_binding_ids=["declared"], client_request_id="approval_sandbox")
    binding = await service.add_connector_binding(principal=owner,
        sandbox_run_id=sandbox["sandbox_run_id"], connector_grant_id="grant_test",
        provider_account_subject_hash="sha256:" + "b" * 64,
        provider_kind="GMAIL_TEST", client_request_id="approval_binding")
    destination = await service.add_destination(principal=owner,
        sandbox_run_id=sandbox["sandbox_run_id"], destination_kind="TEST_CANDIDATE",
        normalized_address="candidate@example.com", verification_receipt_id="probe",
        client_request_id="approval_destination")
    result = await service.request_effect_approval(principal=owner,
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=binding["binding_id"],
        candidate_application_id=candidate_id,
        destination_ids=[destination["destination_id"]], action_kind="H4S_SEND_EMAIL",
        rendered_payload={"subject": "Interview", "body": "Hello"},
        client_request_id="approval_request")
    assert result["status"] == "success"
    assert result["approval_id"].startswith("happroval_")
    assert result["exact_action"]["sandbox_run_id"] == sandbox["sandbox_run_id"]
    claimed = await service.claim_effect_approval(principal=owner,
        approval_id=result["approval_id"], sandbox_run_id=sandbox["sandbox_run_id"],
        binding_id=binding["binding_id"], destination_ids=[destination["destination_id"]],
        candidate_application_id=candidate_id,
        action_kind="H4S_SEND_EMAIL",
        rendered_payload={"subject": "Interview", "body": "Hello"})
    assert claimed["error_code"] == "claim_requires_action_prepare"
    assert (await resolve_approval(principal=owner, approval_id=result["approval_id"],
                                   decision="GRANT", store=store))["status"] == "success"
    granted = await service.claim_effect_approval(principal=owner,
        approval_id=result["approval_id"], sandbox_run_id=sandbox["sandbox_run_id"],
        binding_id=binding["binding_id"], destination_ids=[destination["destination_id"]],
        candidate_application_id=candidate_id,
        action_kind="H4S_SEND_EMAIL",
        rendered_payload={"subject": "Interview", "body": "Hello"})
    assert granted["error_code"] == "claim_requires_action_prepare"


@pytest.mark.asyncio
async def test_only_owner_can_provision(owner):
    store = InMemoryDurableStore()
    service = HiringSandboxService(store)
    manager = ActorPrincipal(
        actor_id="actor_manager", workspace_id=owner.workspace_id,
        role=WorkspaceRole.HIRING_MANAGER, role_grants=frozenset({"role_h4s"}),
        candidate_assignments=frozenset(), interview_assignments=frozenset(),
        session_auth_time=2_000_000_000, membership_version=1)
    result = await service.create(
        principal=manager, role_id="role_h4s", fixture_id="fixture_h4s",
        synthetic_namespace="synthetic_hiring_h4s", connector_binding_ids=["scb_test_mail"],
        client_request_id="manager_create")
    assert result["error_code"] == "operation_forbidden"


@pytest.mark.asyncio
async def test_conversation_is_signed_run_scoped_and_not_generic_session(owner, monkeypatch):
    monkeypatch.setenv("APP_SESSION_SECRET", "h4s-test-secret")
    store = InMemoryDurableStore()
    sandbox_service = HiringSandboxService(store)
    sandbox = await sandbox_service.create(
        principal=owner, role_id="role_h4s", fixture_id="fixture_h4s",
        synthetic_namespace="synthetic_hiring_h4s", connector_binding_ids=["scb_test_mail"],
        client_request_id="conversation_sandbox")
    await store.create("hiring_roles", "role_h4s", {
        "role_id": "role_h4s", "workspace_id": owner.workspace_id,
        "synthetic": True, "fixture_id": "fixture_h4s",
        "synthetic_namespace": "synthetic_hiring_h4s", "version": 1})
    answers = HiringRunAnswerService(store)
    begun = await answers.begin(principal=owner, sandbox_run_id=sandbox["sandbox_run_id"])
    response = await answers.answer(
        principal=owner, conversation_token=begun["conversation_token"],
        question="What can you do for this hiring process?")
    assert response["status"] == "success"
    assert response["intent"] == "EXPLAIN"
    assert "send without approval" in response["answer"]
    assert store.records.get("hiring_conversation_turns")
    assert not store.records.get("sessions")


def test_answer_validator_rejects_hiring_judgment_language():
    assert validate_answer_text("The candidate has one cited unknown.") is True
    assert validate_answer_text("I recommend we hire this candidate.") is False


class _FakeH4SProvider:
    def __init__(self, outcome: dict):
        self.outcome = outcome
        self.calls = 0

    async def execute(self, **kwargs):
        self.calls += 1
        assert kwargs["normalized_destinations"] == ["candidate@example.com"]
        assert kwargs["causal_token"].startswith("h4scausal_")
        return self.outcome

    async def reconcile(self, **_kwargs):
        return self.outcome


class _NotReadyH4SProvider(_FakeH4SProvider):
    async def preflight(self, **_kwargs):
        return {"status": "failed", "error": True,
                "error_code": "sandbox_provider_not_configured"}


class _CausalReplyProvider:
    def __init__(self):
        self.calls = 0

    async def execute(self, **kwargs):
        self.calls += 1
        action_id = kwargs["action_id"]
        from hashlib import sha256
        return {"status": "success", "provider_effect_id": "gmail_reply_1",
                "result_ref": {
                    "thread_id": "thread_reply_1",
                    "causal_message_id_hash": "sha256:" + sha256(
                        f"<{action_id}@h4s.invalid>".encode()).hexdigest(),
                }}

    async def reconcile(self, **_kwargs):
        return {"status": "uncertain", "uncertainty_reason": "not_called"}


async def _effect_fixture(owner, store, service):
    await store.create("hiring_roles", "role_h4s", {
        "role_id": "role_h4s", "workspace_id": owner.workspace_id,
        "current_policy_version_id": "hpv_test", "synthetic": True,
        "fixture_id": "fixture_h4s", "synthetic_namespace": "synthetic_hiring_h4s", "version": 1})
    candidate_id = await _seed_h4s_candidate(store, owner)
    sandbox = await service.create(principal=owner, role_id="role_h4s",
        fixture_id="fixture_h4s", synthetic_namespace="synthetic_hiring_h4s",
        connector_binding_ids=["declared"], client_request_id="effect_sandbox")
    binding = await service.add_connector_binding(principal=owner,
        sandbox_run_id=sandbox["sandbox_run_id"], connector_grant_id="grant_test",
        provider_account_subject_hash="sha256:" + "d" * 64,
        provider_kind="GMAIL_TEST", client_request_id="effect_binding")
    destination = await service.add_destination(principal=owner,
        sandbox_run_id=sandbox["sandbox_run_id"], destination_kind="TEST_CANDIDATE",
        normalized_address="candidate@example.com", verification_receipt_id="probe",
        client_request_id="effect_destination")
    approval = await service.request_effect_approval(principal=owner,
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=binding["binding_id"],
        candidate_application_id=candidate_id,
        destination_ids=[destination["destination_id"]], action_kind="H4S_SEND_EMAIL",
        rendered_payload={"subject": "Interview", "body": "Hello"},
        client_request_id="effect_approval")
    assert (await resolve_approval(principal=owner, approval_id=approval["approval_id"],
                                   decision="GRANT", store=store))["status"] == "success"
    return sandbox, binding, destination, approval, candidate_id


@pytest.mark.asyncio
async def test_h4s_effect_executes_once_with_exact_approval(owner, monkeypatch):
    monkeypatch.setenv("HIRING_ENABLE_H4_SANDBOX", "1")
    store = InMemoryDurableStore()
    sandbox_service = HiringSandboxService(store)
    sandbox, binding, destination, approval, candidate_id = await _effect_fixture(owner, store, sandbox_service)
    provider = _FakeH4SProvider({"status": "success", "provider_effect_id": "gmail_1",
                                 "result_ref": {"thread_id": "thread_1"}})
    effects = H4SEffectService(sandbox=sandbox_service, adapter=provider, store=store)
    args = dict(principal=owner, approval_id=approval["approval_id"],
                sandbox_run_id=sandbox["sandbox_run_id"], binding_id=binding["binding_id"],
                candidate_application_id=candidate_id,
                destination_ids=[destination["destination_id"]], action_kind="H4S_SEND_EMAIL",
                rendered_payload={"subject": "Interview", "body": "Hello"})
    first = await effects.execute(**args)
    assert first["status"] == "success" and first["receipt_status"] == "SUCCEEDED"
    second = await effects.execute(**args)
    assert second["status"] == "success" and second["duplicate"] is True
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_h4s_uncertain_effect_never_retries_without_reconciliation(owner, monkeypatch):
    monkeypatch.setenv("HIRING_ENABLE_H4_SANDBOX", "1")
    store = InMemoryDurableStore()
    sandbox_service = HiringSandboxService(store)
    sandbox, binding, destination, approval, candidate_id = await _effect_fixture(owner, store, sandbox_service)
    provider = _FakeH4SProvider({"status": "uncertain", "uncertainty_reason": "timeout"})
    effects = H4SEffectService(sandbox=sandbox_service, adapter=provider, store=store)
    args = dict(principal=owner, approval_id=approval["approval_id"],
                sandbox_run_id=sandbox["sandbox_run_id"], binding_id=binding["binding_id"],
                candidate_application_id=candidate_id,
                destination_ids=[destination["destination_id"]], action_kind="H4S_SEND_EMAIL",
                rendered_payload={"subject": "Interview", "body": "Hello"})
    first = await effects.execute(**args)
    assert first["error_code"] == "reconciliation_required"
    retry = await effects.execute(**args)
    assert retry["error_code"] == "reconciliation_required"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_h4s_provider_preflight_does_not_consume_approval(owner, monkeypatch):
    """Missing deployment configuration is refused before the approval CAS."""
    monkeypatch.setenv("HIRING_ENABLE_H4_SANDBOX", "1")
    store = InMemoryDurableStore()
    sandbox_service = HiringSandboxService(store)
    sandbox, binding, destination, approval, candidate_id = await _effect_fixture(owner, store, sandbox_service)
    provider = _NotReadyH4SProvider({"status": "success"})
    effects = H4SEffectService(sandbox=sandbox_service, adapter=provider, store=store)
    result = await effects.execute(
        principal=owner, approval_id=approval["approval_id"],
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=binding["binding_id"],
        candidate_application_id=candidate_id,
        destination_ids=[destination["destination_id"]], action_kind="H4S_SEND_EMAIL",
        rendered_payload={"subject": "Interview", "body": "Hello"})
    assert result["error_code"] == "sandbox_provider_not_configured"
    assert (await store.get("approvals", approval["approval_id"]))["status"] == "GRANTED"
    assert not store.records.get("external_actions")
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_h4s_recovers_after_approval_claim_crash_without_resend(owner, monkeypatch):
    """A T1 CLAIMED+PREPARED crash safely resumes the same action at T2."""
    monkeypatch.setenv("HIRING_ENABLE_H4_SANDBOX", "1")
    store = InMemoryDurableStore()
    sandbox_service = HiringSandboxService(store)
    sandbox, binding, destination, approval, candidate_id = await _effect_fixture(owner, store, sandbox_service)
    payload = {"subject": "Interview", "body": "Hello"}
    built = await sandbox_service.build_exact_action(
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=binding["binding_id"],
        candidate_application_id=candidate_id,
        destination_ids=[destination["destination_id"]], action_kind="H4S_SEND_EMAIL",
        rendered_payload=payload)
    action_id = H4SEffectService._action_id(owner.workspace_id, built["exact_action"])
    approval_row = await store.get("approvals", approval["approval_id"])
    claim_id = stable_id("claim", approval["approval_id"], action_id)
    claimed = await store.compare_and_set(
        "approvals", approval["approval_id"], approval_row["version"], {
            "status": "CLAIMED", "claim_id": claim_id,
            "claimed_action_id": action_id})
    assert claimed["status"] == "CLAIMED"
    # This is the precise durable state left by a crash after T1 and before T2.
    await store.create("external_actions", action_id, {
        "schema_version": 2, "action_id": action_id,
        "workspace_id": owner.workspace_id,
        "approval_id": approval["approval_id"], "request_hash": built["subject_hash"],
        "exact_action": built["exact_action"], "status": "PREPARED",
        "claim_id": claim_id, "approval_consumed": False,
        "provider_started_at": None, "version": 1,
    })
    provider = _FakeH4SProvider({"status": "success", "provider_effect_id": "gmail_crash"})
    effects = H4SEffectService(sandbox=sandbox_service, adapter=provider, store=store)
    result = await effects.execute(
        principal=owner, approval_id=approval["approval_id"],
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=binding["binding_id"],
        candidate_application_id=candidate_id,
        destination_ids=[destination["destination_id"]], action_kind="H4S_SEND_EMAIL",
        rendered_payload=payload)
    assert result["receipt_status"] == "SUCCEEDED"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_h4s_verified_reply_resumes_only_the_causal_candidate_run(owner, monkeypatch):
    monkeypatch.setenv("HIRING_ENABLE_H4_SANDBOX", "1")
    store = InMemoryDurableStore()
    sandbox_service = HiringSandboxService(store)
    sandbox, binding, destination, approval, candidate_id = await _effect_fixture(
        owner, store, sandbox_service)
    provider = _CausalReplyProvider()
    effects = H4SEffectService(sandbox=sandbox_service, adapter=provider, store=store)
    sent = await effects.execute(
        principal=owner, approval_id=approval["approval_id"],
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=binding["binding_id"],
        candidate_application_id=candidate_id,
        destination_ids=[destination["destination_id"]], action_kind="H4S_SEND_EMAIL",
        rendered_payload={"subject": "Interview", "body": "Hello"})
    assert sent["receipt_status"] == "SUCCEEDED"
    reply = H4SReplyService(sandbox=sandbox_service, store=store)
    correlated = await reply.ingest_verified_reply(
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=binding["binding_id"],
        provider_message_id="gmail_reply_message_1", provider_thread_id="thread_reply_1",
        in_reply_to_message_id=f"<{sent['action_id']}@h4s.invalid>",
        causal_token=stable_id("h4scausal", sent["action_id"]),
        sender_destination_id=destination["destination_id"], message_kind="INBOX")
    assert correlated["status"] == "success"
    candidate = await store.get("candidate_applications", candidate_id)
    waits = await store.list("waits", filters={"run_id": candidate["run_id"]})
    assert len(waits) == 1 and waits[0]["status"] == "RESOLVED"
    forged = await reply.ingest_verified_reply(
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=binding["binding_id"],
        provider_message_id="gmail_reply_message_2", provider_thread_id="thread_reply_1",
        in_reply_to_message_id=f"<{sent['action_id']}@h4s.invalid>",
        causal_token="h4scausal_forged", sender_destination_id=destination["destination_id"],
        message_kind="INBOX")
    assert forged["error_code"] == "reply_quarantined"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_h4s_calendar_change_is_bound_to_prior_sandbox_event(owner, monkeypatch):
    monkeypatch.setenv("HIRING_ENABLE_H4_SANDBOX", "1")
    store = InMemoryDurableStore()
    sandbox_service = HiringSandboxService(store)
    sandbox, _mail, destination, _approval, candidate_id = await _effect_fixture(
        owner, store, sandbox_service)
    calendar = await sandbox_service.add_connector_binding(
        principal=owner, sandbox_run_id=sandbox["sandbox_run_id"],
        connector_grant_id="grant_calendar", provider_account_subject_hash="sha256:" + "e" * 64,
        provider_kind="CALENDAR_TEST", client_request_id="calendar_binding")
    target_id = "h4saction_prior_calendar"
    await store.create("external_actions", target_id, {
        "action_id": target_id, "workspace_id": owner.workspace_id,
        "action_kind": "H4S_CREATE_CALENDAR_EVENT", "status": "SUCCEEDED",
        "provider_effect_id": "calendar_event_1",
        "sandbox_context": {"sandbox_run_id": sandbox["sandbox_run_id"],
                            "connector_binding_id": calendar["binding_id"],
                            "destination_ids": [destination["destination_id"]],
                            "fixture_id": "fixture_h4s"},
        "exact_action": {"candidate_application_id": candidate_id,
                         "destination_ids": [destination["destination_id"]]}, "version": 1})
    payload = {"target_action_id": target_id, "summary": "Interview", "description": "Brief",
               "start": "2026-09-01T09:00:00+00:00", "end": "2026-09-01T09:30:00+00:00",
               "timezone": "UTC", "conference": "false"}
    changed = await sandbox_service.build_exact_action(
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=calendar["binding_id"],
        candidate_application_id=candidate_id, destination_ids=[destination["destination_id"]],
        action_kind="H4S_UPDATE_CALENDAR_EVENT", rendered_payload=payload)
    assert changed["status"] == "success"
    assert changed["exact_action"]["payload"]["target_event_id"] == "calendar_event_1"
    payload["target_action_id"] = "h4saction_other"
    refused = await sandbox_service.build_exact_action(
        sandbox_run_id=sandbox["sandbox_run_id"], binding_id=calendar["binding_id"],
        candidate_application_id=candidate_id, destination_ids=[destination["destination_id"]],
        action_kind="H4S_UPDATE_CALENDAR_EVENT", rendered_payload=payload)
    assert refused["error_code"] == "sandbox_calendar_target_mismatch"

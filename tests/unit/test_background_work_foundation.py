from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.background_work import (
    FOUNDATION_NEGATIVE_CONSTRAINTS,
    FOUNDATION_TEMPLATE,
    BackgroundCommandDispatcher,
    BackgroundInputRef,
    BackgroundJobRequest,
    BackgroundRetryPolicy,
    BackgroundTemplate,
    BackgroundWorkService,
    enabled_foundation_templates,
    normalize_background_projection,
)
from services.durable_store import InMemoryDurableStore
from services.workflow_contracts import RunKind, run_visible_to_actor, stable_id
from services.workflow_runtime import WorkflowRuntime

pytestmark = pytest.mark.asyncio
REPO = Path(__file__).resolve().parents[2]


def _principal(actor: str = "actor_alex", workspace: str = "workspace_alpha"
               ) -> ActorPrincipal:
    return ActorPrincipal(
        actor_id=actor, workspace_id=workspace, role=WorkspaceRole.FOUNDER,
        role_grants=frozenset(), candidate_assignments=frozenset(),
        interview_assignments=frozenset(),
        session_auth_time=1, membership_version=1)


def _request(*, client_id: str = "background-request-0001",
             objective: str = "Prepare a bounded internal research outline."
             ) -> BackgroundJobRequest:
    return BackgroundJobRequest(
        client_request_id=client_id,
        template_id=FOUNDATION_TEMPLATE.template_id,
        template_version=FOUNDATION_TEMPLATE.version,
        objective_kind=FOUNDATION_TEMPLATE.objective_kind,
        objective_summary=objective,
        origin_message_id="message-origin-001",
        origin_session_id="session-origin-001",
        input_refs=(BackgroundInputRef(
            kind="WORKSPACE_ARTIFACT", ref_id="artifact-source-001",
            version="version-001", content_hash="sha256:" + "a" * 64),),
        confirmed_negative_constraints=("CITATIONS_REQUIRED",),
    )


def _service(store: InMemoryDurableStore) -> BackgroundWorkService:
    return BackgroundWorkService(
        store, templates=enabled_foundation_templates(),
        admission_enabled=True)


async def test_production_default_and_noninteractive_identity_fail_closed():
    store = InMemoryDurableStore()
    disabled = await BackgroundWorkService(store).accept(
        principal=_principal(), request=_request())
    seeded = replace(_principal(), principal_kind="SEEDED")
    noninteractive = await _service(store).accept(
        principal=seeded, request=_request(client_id="background-request-0002"))

    assert disabled["error_code"] == "background_admission_disabled"
    assert noninteractive["error_code"] == "interactive_founder_required"
    assert await store.list("workflow_runs", filters={}) == []


async def test_foundation_has_no_live_route_agent_or_dispatcher_wiring():
    app_source = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    agent_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO / "agents").rglob("*.py"))

    assert "/api/v1/jobs" not in app_source
    assert "BackgroundWorkService" not in app_source
    assert "BackgroundCommandDispatcher" not in app_source
    assert "ConversationDeliveryService" not in app_source
    assert "BACKGROUND_JOB_ADMISSION_ENABLED" not in app_source
    assert "services.background_work" not in agent_source
    assert "services.conversation_serialization" not in agent_source
    assert run_visible_to_actor(
        {"workspace_id": "workspace_alpha", "visibility_scope": "UNKNOWN"},
        workspace_id="workspace_alpha", actor_id="actor_alex") is False


async def test_accept_is_atomic_private_bounded_and_idempotent():
    store = InMemoryDurableStore()
    service = _service(store)

    first = await service.accept(principal=_principal(), request=_request())
    duplicate = await service.accept(principal=_principal(), request=_request())

    assert first["status"] == "accepted" and duplicate["duplicate"] is True
    assert first["job_id"] == first["run_id"]
    run = await store.get("workflow_runs", first["run_id"])
    assert run is not None
    assert run["execution_mode"] == "BACKGROUND"
    assert run["visibility_scope"] == "ACTOR_PRIVATE"
    assert run["subject_id"] == _principal().actor_id
    assert run["background_gate_ceiling"] == "GATE_C_FOUNDER_PILOT"
    assert run["approval_authority"] == run["effect_authority"] == "NONE"
    assert run["memory_write_authority"] == "NONE"
    assert run["external_read_authority"] == "NONE"
    assert run["specialist_execution_enabled"] is True
    assert run["skill_bindings"] == []
    assert set(FOUNDATION_NEGATIVE_CONSTRAINTS) <= set(
        run["negative_constraints"])
    assert run["budgets"] == {
        "max_steps": 1, "max_model_calls": 0,
        "max_provider_calls": 0, "max_tokens": 0,
        "max_active_seconds": 30, "max_wall_seconds": 120,
        "max_artifact_bytes": 5_242_880, "max_artifact_chunks": 100,
        "max_output_bytes": 65_536, "max_retries": 2,
        "max_concurrent": 1}
    assert len(await store.list("workflow_runs", filters={})) == 1
    assert len(await store.list("workflow_plans", filters={})) == 1
    assert len(await store.list("run_events", filters={})) == 1
    assert len(await store.list("artifacts", filters={})) == 1
    assert len(await store.list("command_receipts", filters={})) == 1
    assert len(await store.list("command_outbox", filters={})) == 1
    assert await store.list("workflow_steps", filters={}) == []
    assert await store.list("projection_events", filters={}) == []


async def test_same_client_identity_cannot_name_changed_work():
    store = InMemoryDurableStore()
    service = _service(store)
    await service.accept(principal=_principal(), request=_request())
    conflict = await service.accept(
        principal=_principal(),
        request=_request(objective="Prepare different bounded work."))

    assert conflict["error_code"] == "idempotency_conflict"
    assert len(await store.list("workflow_runs", filters={})) == 1


async def test_input_constraints_and_effect_capability_are_rejected():
    store = InMemoryDurableStore()
    unsafe_input = replace(
        _request(client_id="background-request-unsafe-input"),
        input_refs=(BackgroundInputRef(
            kind="URL", ref_id="https:example.invalid", version="version-001",
            content_hash="sha256:" + "b" * 64),))
    unknown_constraint = replace(
        _request(client_id="background-request-unsafe-constraint"),
        confirmed_negative_constraints=("MODEL_CHOOSES_AUTHORITY",))
    effect_template: BackgroundTemplate = replace(
        FOUNDATION_TEMPLATE, admission_enabled=True,
        capability_ids=("external.send_email",))
    effect_service = BackgroundWorkService(
        store,
        templates={(effect_template.template_id, effect_template.version):
                   effect_template},
        admission_enabled=True)

    bad_input = await _service(store).accept(
        principal=_principal(), request=unsafe_input)
    bad_constraint = await _service(store).accept(
        principal=_principal(), request=unknown_constraint)
    effect = await effect_service.accept(
        principal=_principal(),
        request=_request(client_id="background-request-effect"))

    assert bad_input["error_code"] == "background_input_invalid"
    assert bad_constraint["error_code"] == "negative_constraint_invalid"
    assert effect["error_code"] == "background_authority_forbidden"
    assert await store.list("workflow_runs", filters={}) == []


async def test_dispatch_materializes_only_no_effect_validation_and_replays():
    store = InMemoryDurableStore()
    accepted = await _service(store).accept(
        principal=_principal(), request=_request())
    outbox_id = stable_id("cmdoutbox", accepted["command_id"], "dispatch")

    first = await BackgroundCommandDispatcher(store).dispatch(outbox_id)
    duplicate = await BackgroundCommandDispatcher(store).dispatch(outbox_id)
    steps = await store.list(
        "workflow_steps", filters={"run_id": accepted["run_id"]})

    assert first["command"]["status"] == "DISPATCHED"
    assert duplicate["duplicate"] is True
    assert len(steps) == 1
    assert steps[0]["step_key"] == "analyze_artifact"
    assert steps[0]["capability_id"] == "background.artifact.inspect"
    assert steps[0]["status"] == "READY"
    assert steps[0]["visibility_scope"] == "ACTOR_PRIVATE"
    assert await store.list("approvals", filters={}) == []
    assert await store.list("external_actions", filters={}) == []
    assert await store.list("wake_deliveries", filters={}) == []


async def test_dispatch_refuses_widened_authority_before_creating_a_step():
    store = InMemoryDurableStore()
    accepted = await _service(store).accept(
        principal=_principal(), request=_request())
    run = await store.get("workflow_runs", accepted["run_id"])
    await store.compare_and_set(
        "workflow_runs", accepted["run_id"], run["version"],
        {"effect_authority": "MODEL_SELECTED"})
    outbox_id = stable_id("cmdoutbox", accepted["command_id"], "dispatch")

    refused = await BackgroundCommandDispatcher(store).dispatch(outbox_id)

    assert refused["error_code"] == "background_dispatch_authority_invalid"
    assert await store.list("workflow_steps", filters={}) == []


async def test_actor_private_reads_cancel_and_stale_worker_are_fenced():
    store = InMemoryDurableStore()
    founder = _principal()
    other = _principal(actor="actor_other")
    service = _service(store)
    accepted = await service.accept(principal=founder, request=_request())
    outbox_id = stable_id("cmdoutbox", accepted["command_id"], "dispatch")
    dispatched = await BackgroundCommandDispatcher(store).dispatch(outbox_id)
    step = dispatched["step"]
    claimed = await WorkflowRuntime(store).claim_step(
        step["step_id"], lease_owner="foundation-worker")

    hidden = await service.get_job(principal=other, run_id=accepted["run_id"])
    hidden_list = await service.list_jobs(principal=other)
    forbidden_cancel = await service.cancel(
        principal=other, run_id=accepted["run_id"], reason="stop")
    cancelled = await service.cancel(
        principal=founder, run_id=accepted["run_id"], reason="Stop this work")
    stale = await WorkflowRuntime(store).complete_step(
        step["step_id"], lease_owner="foundation-worker",
        generation=claimed["attempt_generation"])

    assert hidden["error_code"] == "background_job_not_found"
    assert hidden_list["jobs"] == []
    assert forbidden_cancel["error_code"] == "background_job_not_found"
    assert cancelled["runtime_status"] == "CANCELLED"
    assert stale["error_code"] == "run_fenced"


async def test_retry_and_projection_contracts_are_closed():
    retry = BackgroundRetryPolicy.classify(
        FOUNDATION_TEMPLATE, error_code="lease_lost", attempts=1)
    exhausted = BackgroundRetryPolicy.classify(
        FOUNDATION_TEMPLATE, error_code="lease_lost", attempts=3)
    auth = BackgroundRetryPolicy.classify(
        FOUNDATION_TEMPLATE, error_code="authorization_failed", attempts=0)
    milestone = normalize_background_projection(
        event_kind="MILESTONE_REACHED", run_id="run-background-001",
        event_id="event-background-001", event_sequence=2)
    raw = normalize_background_projection(
        event_kind="MODEL_STATUS_TEXT", run_id="run-background-001",
        event_id="event-background-002", event_sequence=3)
    missing_approval = normalize_background_projection(
        event_kind="APPROVAL_REQUIRED", run_id="run-background-001",
        event_id="event-background-003", event_sequence=4)

    assert retry["retryable"] is True
    assert exhausted["retryable"] is False
    assert auth["retryable"] is False
    assert milestone["projection"]["authoritative"] is False
    assert milestone["projection"]["caption_code"] == "BACKGROUND_MILESTONE"
    assert raw["error_code"] == "background_projection_invalid"
    assert missing_approval["error_code"] == "background_projection_invalid"


async def test_runtime_cannot_bypass_foundation_profile():
    result = await WorkflowRuntime(InMemoryDurableStore()).create_run(
        workspace_id="workspace_alpha", journey_id="journey-background-raw",
        run_kind=RunKind.BACKGROUND, workflow_kind="alex_background_job:v1",
        idempotency_key="background-raw-request",
        domain_ref="artifact-background-raw",
        originating_actor_id="actor_alex",
        origin_session_id="session-origin-001",
        budgets={"max_steps": 1, "max_model_calls": 0,
                 "max_provider_calls": 0, "max_tokens": 0})

    assert result["error_code"] == "background_profile_invalid"

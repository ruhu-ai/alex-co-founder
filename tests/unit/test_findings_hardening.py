"""Regression coverage for the detailed production-readiness review."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from starlette.datastructures import Headers, UploadFile

from agents.co_founder.state_schema import ApplicationStep, SectionStatus
from services import (
    alex_mailbox,
    approval_service,
    feedback_service,
    firestore,
    google_oauth,
)

ROOT = Path(__file__).parents[2]


@pytest.mark.asyncio
async def test_losing_approval_claim_never_calls_email_provider(
        monkeypatch, fake_store):
    sends = []

    class Service:
        def users(self):
            return self

        def messages(self):
            return self

        def send(self, **kwargs):
            sends.append(kwargs)
            raise AssertionError("provider must not be called")

    alex_mailbox.set_service_factory(Service)

    details = {"to": "program@example.org", "subject": "Question", "body": "Body"}
    subject_hash = approval_service.action_subject_hash(
        "send_email", "email:general", details)
    approval_id = await firestore.create_approval(
        "email:general", "send_email", 30, details=details,
        founder_id="founder", session_id="session", subject_hash=subject_hash)
    await firestore.grant_approval(approval_id, "founder")

    async def _lost(*_args, **_kwargs):
        return {"status": "error", "error": True,
                "error_code": "concurrency_conflict",
                "message": "Approval or action changed concurrently."}

    async def _audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "services.alex_mailbox.external_action_service.prepare", _lost)
    monkeypatch.setattr("services.alex_mailbox.firestore.audit", _audit)
    result = await alex_mailbox.send_email(
        "program@example.org", "Question", "Body",
        founder_id="founder", session_id="session")
    alex_mailbox.set_service_factory(None)
    assert result["status"] == "error"
    assert result["error_code"] == "concurrency_conflict"
    assert sends == []


@pytest.mark.asyncio
async def test_feedback_rejects_unknown_section_and_wrong_founder(fake_store, monkeypatch):
    app_id = await firestore.create_application("founder", "opp", [])
    fake_store.applications[app_id].update(
        state=ApplicationStep.AWAITING_REVIEW,
        draft_sections=[{"section_id": "known", "section_key": "problem",
                         "content": "answer", "status": SectionStatus.DRAFTED}])

    called = False

    async def _distill(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"status": "success"}

    monkeypatch.setattr("services.feedback_service.distill_service.run_distillation", _distill)
    missing = await feedback_service.record_feedback(
        "founder", app_id, "missing", "approve")
    foreign = await feedback_service.record_feedback(
        "other-founder", app_id, "known", "approve")
    assert missing["status"] == "error" and "not found" in missing["message"]
    assert foreign["status"] == "error" and "belong" in foreign["message"]
    assert called is False
    assert fake_store.feedback == {}


@pytest.mark.asyncio
async def test_bounded_upload_rejects_oversize_and_bad_type():
    from app.main import _read_upload

    oversized = UploadFile(
        io.BytesIO(b"12345"), filename="note.webm",
        headers=Headers({"content-type": "audio/webm"}))
    with pytest.raises(Exception) as size_error:
        await _read_upload(oversized, max_bytes=4, allowed_types={"audio/webm"})
    assert getattr(size_error.value, "status_code", None) == 413

    bad_type = UploadFile(
        io.BytesIO(b"hello"), filename="payload.exe",
        headers=Headers({"content-type": "application/x-msdownload"}))
    with pytest.raises(Exception) as type_error:
        await _read_upload(bad_type, max_bytes=100,
                           allowed_types={"application/pdf"})
    assert getattr(type_error.value, "status_code", None) == 415


def test_failed_secret_persistence_does_not_change_live_process(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "co-founder")
    monkeypatch.setenv("TEST_RUNTIME_SECRET", "previous")

    def _fail(_key, _value):
        raise RuntimeError("secret manager unavailable")

    monkeypatch.setattr("services.secrets.put", _fail)
    result = google_oauth.save_env_var("TEST_RUNTIME_SECRET", "replacement")
    assert result["status"] == "error"
    assert os.environ["TEST_RUNTIME_SECRET"] == "previous"


def test_browser_and_mobile_runtime_invariants_are_encoded():
    browser_source = (ROOT / "services/browser_service.py").read_text()
    runtime_source = (ROOT / "services/browser_runtime.py").read_text()
    env_example = (ROOT / ".env.example").read_text()
    ui = (ROOT / "app/static/index.html").read_text()
    assert "launch(headless=True)" in runtime_source
    assert "launch(headless=True)" not in browser_source
    assert "HEADLESS=false" not in runtime_source
    assert "HEADLESS=" not in env_example
    assert 'body[data-pane="board"] #leftCell' in ui
    assert 'body[data-pane="browser"] #leftCell' in ui
    assert "setInterval(refresh" not in ui


def test_background_work_and_voice_lifecycle_are_request_bound():
    main_source = (ROOT / "app/main.py").read_text()
    live_source = (ROOT / "app/live.py").read_text()
    assert "BackgroundTasks" not in main_source
    assert 'task_queue.enqueue, "/tasks/wake_delivery"' in main_source
    assert live_source.index("runner = Runner(") < live_source.index(
        '@app.websocket("/live/{session_id}")')
    assert "CanonicalLiveSessionService" in live_source
    assert "LiveTranscriptCommitter" in live_source
    assert "turns_to_append" not in live_source


def test_cloud_build_and_explicit_eval_gate_are_reproducible():
    docker = (ROOT / "Dockerfile").read_text()
    requirements = (ROOT / "requirements.txt").read_text()
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    deploy = (ROOT / "scripts/deploy.sh").read_text()
    assert "v1.62.0-jammy" in docker
    assert "playwright install chromium" not in docker
    assert "pytest==" not in requirements and "ruff==" not in requirements
    assert "run_model_evals" in ci
    assert "github.event_name == 'workflow_dispatch'" in ci
    assert "cancel-in-progress: true" in ci
    assert "ADK eval gate blocked" in ci and "exit 1" in ci
    assert "co-founder-browser-worker" in deploy
    assert "--no-allow-unauthenticated --min-instances 0 --max-instances 1" in deploy
    assert "--concurrency=1" in deploy
    assert "--allow-unauthenticated --min-instances 0 --max-instances 10" in deploy
    assert "BROWSER_WORKER_URL" in deploy
    assert "--cpu-throttling" in deploy
    assert "--no-cpu-throttling" not in deploy


def test_discovery_is_manual_only_and_deadline_monitoring_stays_scheduled():
    deploy = (ROOT / "scripts/deploy.sh").read_text()
    main_source = (ROOT / "app/main.py").read_text()

    assert "discovery-daily" not in deploy
    assert "discovery-tick-push" not in deploy
    assert "discovery-tick" not in deploy
    assert "deadline-scan-6h" in deploy
    assert "deadline-tick-push" in deploy
    assert "--ack-deadline=600" in deploy
    # Behavioral, not whitespace-exact (docs/23 WI-3 refactored the dispatch
    # call site): discovery reaches the worker through a durable Cloud Task,
    # and the route is never a Scheduler/Pub-Sub target.
    assert '"/tasks/discover"' in main_source
    assert "task_queue.enqueue" in main_source
    assert "this route is not scheduled" in main_source


def test_agent_engine_deploy_reuses_one_scale_to_zero_resource():
    deploy = (ROOT / "scripts/deploy_agent_engine.sh").read_text()
    config = json.loads(
        (ROOT / "agents/co_founder/.agent_engine_config.json").read_text())

    assert "--agent_engine_id" in deploy
    assert "multiple co-founder Agent Engines found" in deploy
    assert config["min_instances"] == 0
    assert config["max_instances"] == 1


def test_mock_portal_cloud_state_and_public_urls_are_durable():
    source = (ROOT / "mock_portal/main.py").read_text()
    deploy = (ROOT / "scripts/deploy.sh").read_text()
    assert 'collection("mock_portal_submissions")' in source
    assert 'collection("mock_portal_accounts")' in source
    assert "_reserve_submission" in source
    assert "MOCK_PORTAL_PUBLIC_URL=$MOCK_URL" in deploy
    assert 'verify_url = f"{_AGENT_URL.rstrip' in source

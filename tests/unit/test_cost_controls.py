from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from google.api_core import exceptions as google_exceptions

from app.live import _pcm16_has_activity
from services import projection_stream, retry_policy, task_queue

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_projection_waiter_wakes_without_reconciliation_timeout():
    revision = projection_stream.workspace_revision("workspace-cost")
    waiter = asyncio.create_task(projection_stream.wait_for_workspace_event(
        "workspace-cost", after_revision=revision, timeout=1))
    await asyncio.sleep(0)

    projection_stream.notify_workspace("workspace-cost")

    assert await waiter is True


@pytest.mark.asyncio
async def test_projection_wait_closes_replay_subscribe_race():
    revision = projection_stream.workspace_revision("workspace-race")
    projection_stream.notify_workspace("workspace-race")

    assert await projection_stream.wait_for_workspace_event(
        "workspace-race", after_revision=revision, timeout=0.01) is True


def test_live_idle_energy_fence_ignores_silence_and_detects_speech():
    assert _pcm16_has_activity(b"\x00\x00" * 160) is False
    assert _pcm16_has_activity((800).to_bytes(2, "little", signed=True) * 160) is True


def test_retry_policy_is_explicit_and_transient_only():
    options = retry_policy.gemini_retry_options()

    assert options.attempts == 3
    assert options.http_status_codes == [408, 429, 500, 502, 503, 504]
    assert retry_policy.is_transient_exception(TimeoutError()) is True
    assert retry_policy.is_transient_exception(
        google_exceptions.ServiceUnavailable("later")) is True
    assert retry_policy.is_transient_exception(ValueError("bad model JSON")) is False


def test_model_backed_task_deadline_is_shorter_than_durable_lease(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

    class Session:
        def __init__(self, _credentials):
            pass

        def post(self, _url, *, timeout, json):
            assert timeout == 20
            captured.update(json["task"])
            return Response()

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project")
    monkeypatch.setenv("GOOGLE_CLOUD_REGION", "us-central1")
    monkeypatch.setenv("AGENT_BASE_URL", "https://app.example")
    monkeypatch.setenv("TASKS_DISCOVERY_INGESTION_SA", "worker@example.iam")
    monkeypatch.setattr("google.auth.default", lambda **_kwargs: (object(), "project"))
    monkeypatch.setattr(
        "google.auth.transport.requests.AuthorizedSession", Session)

    result = task_queue.enqueue(
        "/tasks/discover", {"discovery_request_id": "request"}, "dedupe",
        queue_name="co-founder-discovery-ingestion")

    assert result["status"] == "success"
    assert captured["dispatchDeadline"] == "840s"


def test_frontend_streams_and_ci_are_demand_and_credit_gated():
    html = (ROOT / "app/static/index.html").read_text()
    main = (ROOT / "app/main.py").read_text()
    ci = (ROOT / ".github/workflows/ci.yml").read_text()

    boot = html[html.index("// ---------- boot ----------"):]
    assert "connectPlatformEvents(); refresh(); refreshWaiting();" not in boot
    assert "armPlatformEvents();" in html
    assert "platformDemandUntil" in html
    assert "if (voice) closeLiveConnection();" in html
    assert "await asyncio.sleep(2)" not in main
    assert "wait_for_workspace_event" in main
    assert "run_model_evals" in ci and "cancel-in-progress: true" in ci


def test_deploy_reconciles_bounded_retries_and_low_frequency_recovery():
    deploy = (ROOT / "scripts/deploy.sh").read_text()
    firestore = (ROOT / "services/firestore.py").read_text()

    assert '--schedule="*/15 * * * *"' in deploy
    assert "co-founder-discovery-ingestion --location=\"$REGION\"" in deploy
    assert "--max-concurrent-dispatches=4 --max-attempts=3" in deploy
    assert '"max_attempts": 3' in firestore

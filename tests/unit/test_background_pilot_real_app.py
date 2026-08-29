"""Safety envelope for the private exact real-artifact pilot host."""

import asyncio
from types import SimpleNamespace

from services import background_pilot_real_app as real_pilot


def _exact_env(monkeypatch):
    values = {
        "K_SERVICE": "co-founder-spec40-real-pilot",
        real_pilot.WORKSPACE_ENV: "founder",
        real_pilot.ACTOR_ENV: "founder_actor",
        real_pilot.SESSION_ENV: "session_pitch_deck",
        real_pilot.ARTIFACT_ENV: "ab" * 16,
        "BACKGROUND_JOB_ADMISSION_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PILOT_ENABLED": "true",
        "BACKGROUND_SPECIALIST_EXECUTION_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PILOT_EXECUTION_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH": "false",
        "BACKGROUND_ARTIFACT_PILOT_WORKSPACES": "founder",
        "BACKGROUND_CONVERSATION_DELIVERY_ENABLED": "false",
        "BACKGROUND_PILOT_ALLOW_TEST_DISPATCH": "0",
        "BACKGROUND_ARTIFACT_PILOT_QUEUE":
            "co-founder-background-pilot-real",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_real_pilot_exposes_no_seed_controls_docs_or_generic_routes():
    paths = {route.path for route in real_pilot.app.routes}
    assert paths == {
        "/health",
        "/api/v1/background-pilot/artifact-analysis",
        "/api/v1/background-pilot/artifact-grounded-brief",
        "/api/v1/background-pilot/jobs",
        "/api/v1/background-pilot/jobs/{run_id}",
        "/api/v1/background-pilot/jobs/{run_id}/timeline",
        "/api/v1/background-pilot/jobs/{run_id}:cancel",
        "/tasks/background-artifact-pilot",
        "/tasks/background-artifact-grounded-brief",
    }
    assert not any(path.startswith("/canary") for path in paths)
    assert not {"/docs", "/redoc", "/openapi.json"} & paths


def test_real_pilot_requires_exact_complete_binding(monkeypatch):
    _exact_env(monkeypatch)
    assert all(real_pilot._exact_configuration().values())
    monkeypatch.setenv(real_pilot.ARTIFACT_ENV, "https://example.com/deck.pdf")
    assert real_pilot._binding() == {}
    assert real_pilot._exact_configuration()["binding_complete"] is False


def test_artifact_authorizer_is_exact(monkeypatch):
    _exact_env(monkeypatch)
    principal = SimpleNamespace(workspace_id="founder", actor_id="founder_actor")
    assert asyncio.run(real_pilot._artifact(
        principal, "session_pitch_deck", "ab" * 16)) is True
    assert asyncio.run(real_pilot._artifact(
        principal, "session_pitch_deck", "cd" * 16)) is False
    assert asyncio.run(real_pilot._artifact(
        principal, "another_session", "ab" * 16)) is False

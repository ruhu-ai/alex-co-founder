"""Static safety envelope for the private synthetic Spec 40 canary host."""

from services import background_pilot_canary_app as canary


def test_canary_host_exposes_only_fixed_route_families():
    paths = {route.path for route in canary.app.routes}
    assert paths == {
        "/health",
        "/canary/seed",
        "/canary/arm/{mode}",
        "/canary/failure-probe",
        "/canary/state",
        "/canary/cleanup",
        "/api/v1/background-pilot/artifact-analysis",
        "/api/v1/background-pilot/artifact-grounded-brief",
        "/api/v1/background-pilot/jobs",
        "/api/v1/background-pilot/jobs/{run_id}",
        "/api/v1/background-pilot/jobs/{run_id}/timeline",
        "/api/v1/background-pilot/jobs/{run_id}:cancel",
        "/tasks/background-artifact-pilot",
        "/tasks/background-artifact-grounded-brief",
    }
    assert not {"/docs", "/redoc", "/openapi.json"} & paths
    assert not any("runs" in path or "model" in path for path in paths)


def test_canary_configuration_is_fail_closed_by_default(monkeypatch):
    for name in (
        "K_SERVICE", "SPEC40_CANARY_TEMPLATE",
        "BACKGROUND_JOB_ADMISSION_ENABLED", "BACKGROUND_SKILLS_ENABLED",
        "BACKGROUND_ARTIFACT_PREPARATION_ENABLED",
        "BACKGROUND_SPECIALIST_EXECUTION_ENABLED",
        "BACKGROUND_ARTIFACT_PREPARATION_EXECUTION_ENABLED",
        "BACKGROUND_ARTIFACT_PREPARATION_KILL_SWITCH",
        "BACKGROUND_ARTIFACT_PREPARATION_WORKSPACES",
        "BACKGROUND_ARTIFACT_PREPARATION_QUEUE",
        "BACKGROUND_ARTIFACT_PILOT_ENABLED",
        "BACKGROUND_ARTIFACT_PILOT_EXECUTION_ENABLED",
        "BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH",
        "BACKGROUND_CONVERSATION_DELIVERY_ENABLED",
        "BACKGROUND_PILOT_ALLOW_TEST_DISPATCH",
    ):
        monkeypatch.delenv(name, raising=False)
    flags = canary._exact_configuration()
    assert flags["cloud_run"] is False
    assert flags["gate_f_selected"] is False
    assert flags["admission_enabled"] is False
    assert flags["execution_enabled"] is False
    assert flags["kill_switch_clear"] is False
    assert flags["single_workspace"] is False
    assert flags["conversation_delivery_off"] is True
    assert flags["local_dispatch_off"] is True


def test_canary_configuration_accepts_only_exact_private_envelope(monkeypatch):
    values = {
        "K_SERVICE": "co-founder-spec40-canary",
        "SPEC40_CANARY_TEMPLATE": "pilot.artifact_grounded_brief@1",
        "BACKGROUND_JOB_ADMISSION_ENABLED": "true",
        "BACKGROUND_SKILLS_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PREPARATION_ENABLED": "true",
        "BACKGROUND_SPECIALIST_EXECUTION_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PREPARATION_EXECUTION_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PREPARATION_KILL_SWITCH": "false",
        "BACKGROUND_ARTIFACT_PREPARATION_WORKSPACES": canary.WORKSPACE_ID,
        "BACKGROUND_ARTIFACT_PREPARATION_QUEUE":
            "co-founder-background-skill-gate-f",
        "BACKGROUND_ARTIFACT_PILOT_ENABLED": "false",
        "BACKGROUND_ARTIFACT_PILOT_EXECUTION_ENABLED": "false",
        "BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH": "true",
        "BACKGROUND_CONVERSATION_DELIVERY_ENABLED": "false",
        "BACKGROUND_PILOT_ALLOW_TEST_DISPATCH": "0",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    assert all(canary._exact_configuration().values())
    monkeypatch.setenv(
        "BACKGROUND_ARTIFACT_PREPARATION_WORKSPACES",
        f"{canary.WORKSPACE_ID},other")
    assert canary._exact_configuration()["single_workspace"] is False
    monkeypatch.setenv("BACKGROUND_ARTIFACT_PILOT_ENABLED", "true")
    assert canary._exact_configuration()["inventory_pilot_disabled"] is False

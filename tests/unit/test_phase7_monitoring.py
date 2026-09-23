from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "configure_phase7_monitoring", ROOT / "scripts" / "configure_phase7_monitoring.py"
)
assert SPEC and SPEC.loader
monitoring = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = monitoring
SPEC.loader.exec_module(monitoring)


class FakeRunner:
    def __init__(self, *, uptime: list[dict] | None = None,
                 policies: list[dict] | None = None,
                 log_metrics: list[dict] | None = None):
        self.commands: list[tuple[str, ...]] = []
        self.uptime = list(uptime or [])
        self.policies = list(policies or [])
        self.log_metrics = list(log_metrics or [])

    def __call__(self, command: tuple[str, ...]):
        self.commands.append(command)
        text = " ".join(command)
        if "logging metrics list" in text:
            value = self.log_metrics
        elif "logging metrics create" in text or "logging metrics update" in text:
            value = {}
        elif "monitoring channels list" in text:
            value = [
                {
                    "name": "projects/project-a/notificationChannels/channel-1",
                    "displayName": "Primary on-call",
                    "enabled": True,
                    "verificationStatus": "VERIFIED",
                }
            ]
        elif "monitoring uptime list-configs" in text:
            value = self.uptime
        elif "monitoring uptime create" in text:
            value = {"name": "projects/project-a/uptimeCheckConfigs/check-1"}
        elif "monitoring policies list" in text:
            value = self.policies
        elif "monitoring policies create" in text:
            value = {"name": "projects/project-a/alertPolicies/policy-1"}
        elif "monitoring policies update" in text:
            value = {"name": command[4]}
        else:
            raise AssertionError(command)
        return monitoring.CommandResult(0, json.dumps(value), "")


def test_dry_run_requires_a_real_verified_channel_and_mutates_nothing():
    runner = FakeRunner()
    result = monitoring.configure(
        project="project-a",
        region="us-central1",
        app_url="https://app.example.com",
        requested_channels=["channel-1"],
        apply=False,
        confirmed_project="",
        runner=runner,
    )

    assert result["mode"] == "dry-run"
    assert result["policy_count"] == 13
    assert result["uptime_check_id"] == "DRY_RUN_UPTIME_CHECK_ID"
    assert len(result["policies_created"]) == 13
    assert len(result["log_metrics_created"]) == 2
    assert not any(" create " in f" {' '.join(command)} " for command in runner.commands)


def test_apply_creates_uptime_and_every_policy_with_notification_channel():
    runner = FakeRunner()
    result = monitoring.configure(
        project="project-a",
        region="us-central1",
        app_url="https://app.example.com",
        requested_channels=["channel-1"],
        apply=True,
        confirmed_project="project-a",
        runner=runner,
    )

    assert result["uptime_check_created"] is True
    assert result["uptime_check_id"] == "check-1"
    policy_commands = [
        command for command in runner.commands if "monitoring policies create" in " ".join(command)
    ]
    assert len(policy_commands) == 13
    for command in policy_commands:
        argument = next(item for item in command if item.startswith("--policy="))
        policy = json.loads(argument.removeprefix("--policy="))
        assert policy["notificationChannels"] == [
            "projects/project-a/notificationChannels/channel-1"
        ]
        assert policy["userLabels"]["managed_by"] == monitoring.MANAGED_BY


def test_apply_refuses_wrong_project_and_unverified_channel():
    with pytest.raises(monitoring.ConfigurationError, match="confirm-project"):
        monitoring.configure(
            project="project-a",
            region="us-central1",
            app_url="https://app.example.com",
            requested_channels=["channel-1"],
            apply=True,
            confirmed_project="project-b",
            runner=FakeRunner(),
        )

    runner = FakeRunner()

    def unverified(command):
        if "monitoring channels list" in " ".join(command):
            return monitoring.CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "name": "projects/project-a/notificationChannels/channel-1",
                            "enabled": True,
                            "verificationStatus": "UNVERIFIED",
                        }
                    ]
                ),
                "",
            )
        return runner(command)

    with pytest.raises(monitoring.ConfigurationError, match="not verified"):
        monitoring.configure(
            project="project-a",
            region="us-central1",
            app_url="https://app.example.com",
            requested_channels=["channel-1"],
            apply=False,
            confirmed_project="",
            runner=unverified,
        )


def test_existing_unmanaged_name_is_never_adopted():
    runner = FakeRunner(
        uptime=[
            {
                "name": "projects/project-a/uptimeCheckConfigs/check-1",
                "displayName": monitoring.UPTIME_DISPLAY_NAME,
                "userLabels": {"managed_by": "someone_else"},
            }
        ]
    )
    with pytest.raises(monitoring.ConfigurationError, match="unmanaged uptime"):
        monitoring.configure(
            project="project-a",
            region="us-central1",
            app_url="https://app.example.com",
            requested_channels=["channel-1"],
            apply=False,
            confirmed_project="",
            runner=runner,
        )


def test_policy_filters_are_closed_to_project_region_service_and_queue():
    policies = monitoring.desired_policies(
        project="project-a",
        region="us-central1",
        uptime_check_id="check-1",
        channels=["projects/project-a/notificationChannels/channel-1"],
    )
    serialized = json.dumps(policies, sort_keys=True)
    assert "co-founder-browser-worker" in serialized
    assert "project-a" in serialized
    assert "us-central1" in serialized
    assert all(name in serialized for name in monitoring.QUEUE_DEPTH_LIMITS)
    assert all(policy["notificationChannels"] for policy in policies)
    assert "Co-Founder service errors" not in {
        policy["displayName"] for policy in policies}
    route_policies = [
        policy for policy in policies
        if policy["displayName"].endswith("failures")]
    assert len(route_policies) == 2
    assert all(
        policy["conditions"][0]["conditionThreshold"]["thresholdValue"] == 1
        for policy in route_policies)


def test_owned_single_error_policies_are_disabled_not_deleted():
    policies = [
        {
            "name": "projects/project-a/alertPolicies/old-gate-e",
            "displayName": "Spec 40 Gate E service 5xx",
            "enabled": True,
            "documentation": {"content": "stale", "mimeType": "text/markdown"},
            "userLabels": {"managed_by": "cofounder_spec40_gate_e"},
            "conditions": [], "combiner": "OR", "notificationChannels": [],
        },
        {
            "name": "projects/project-a/alertPolicies/old-phase7",
            "displayName": "Co-Founder service errors",
            "enabled": True,
            "documentation": {"content": "stale", "mimeType": "text/markdown"},
            "userLabels": {"managed_by": monitoring.MANAGED_BY},
            "conditions": [], "combiner": "OR", "notificationChannels": [],
        },
    ]
    runner = FakeRunner(policies=policies)
    result = monitoring.configure(
        project="project-a", region="us-central1",
        app_url="https://app.example.com", requested_channels=["channel-1"],
        apply=True, confirmed_project="project-a", runner=runner)
    assert result["policies_retired"] == [
        "Spec 40 Gate E service 5xx", "Co-Founder service errors"]
    updates = [
        command for command in runner.commands
        if "monitoring policies update" in " ".join(command)]
    retired = []
    for command in updates:
        payload = json.loads(next(
            item.removeprefix("--policy=") for item in command
            if item.startswith("--policy=")))
        if payload["displayName"] in monitoring.RETIRED_POLICIES:
            retired.append(payload)
    assert len(retired) == 2
    assert all(policy["enabled"] is False for policy in retired)


def test_unmanaged_log_metric_is_never_adopted():
    runner = FakeRunner(log_metrics=[{
        "name": "cofounder_command_outbox_5xx",
        "description": "created elsewhere", "filter": "anything"}])
    with pytest.raises(monitoring.ConfigurationError, match="unmanaged log metric"):
        monitoring.configure(
            project="project-a", region="us-central1",
            app_url="https://app.example.com", requested_channels=["channel-1"],
            apply=False, confirmed_project="", runner=runner)

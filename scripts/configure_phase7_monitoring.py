#!/usr/bin/env python3
"""Install the content-free Cloud Monitoring controls required by Phase 7.

The installer is dry-run by default. Applying requires both an exact project
confirmation and at least one existing, enabled notification channel. It owns
only resources labelled ``managed_by=cofounder_phase7`` and refuses to adopt a
same-named resource created by another owner.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

MANAGED_BY = "cofounder_phase7"
UPTIME_DISPLAY_NAME = "Co-Founder API health"
POLICY_PREFIX = "Co-Founder"

QUEUE_DEPTH_LIMITS = {
    "co-founder-events": 10_000,
    "co-founder-browser-expiry": 500,
    "co-founder-timers": 10_000,
    "co-founder-provider-events": 10_000,
    "co-founder-discovery-ingestion": 5_000,
    "co-founder-reconciliation": 2_000,
    "co-founder-interactive": 2_000,
    "co-founder-background-pilot": 100,
}


class ConfigurationError(RuntimeError):
    """A fail-closed monitoring configuration error."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[tuple[str, ...]], CommandResult]


def _runner(command: tuple[str, ...]) -> CommandResult:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _json_command(runner: Runner, *args: str) -> Any:
    command = ("gcloud", *args, "--format=json")
    completed = runner(command)
    if completed.returncode:
        message = completed.stderr.strip()[-800:] or "gcloud command failed"
        raise ConfigurationError(message)
    text = completed.stdout.strip()
    return json.loads(text) if text else {}


def _channel_names(channels: list[dict[str, Any]], requested: list[str]) -> list[str]:
    by_name = {str(item.get("name") or ""): item for item in channels}
    selected: list[str] = []
    for value in requested:
        matches = [
            item
            for name, item in by_name.items()
            if name == value or name.rsplit("/", 1)[-1] == value
        ]
        if len(matches) != 1:
            raise ConfigurationError(f"notification channel not found exactly once: {value}")
        channel = matches[0]
        verification = str(channel.get("verificationStatus") or "")
        if not channel.get("enabled", True):
            raise ConfigurationError(f"notification channel is disabled: {value}")
        if verification and verification != "VERIFIED":
            raise ConfigurationError(f"notification channel is not verified: {value}")
        selected.append(str(channel["name"]))
    return sorted(set(selected))


def _threshold_condition(
    *,
    display_name: str,
    metric_filter: str,
    comparison: str,
    threshold: float,
    duration: str,
    aligner: str,
) -> dict[str, Any]:
    return {
        "displayName": display_name,
        "conditionThreshold": {
            "filter": metric_filter,
            "comparison": comparison,
            "thresholdValue": threshold,
            "duration": duration,
            "aggregations": [
                {
                    "alignmentPeriod": "300s",
                    "perSeriesAligner": aligner,
                }
            ],
            "trigger": {"count": 1},
        },
    }


def _policy(
    *,
    display_name: str,
    conditions: list[dict[str, Any]],
    channels: list[str],
    severity: str,
    documentation: str,
) -> dict[str, Any]:
    return {
        "displayName": display_name,
        "documentation": {
            "content": documentation,
            "mimeType": "text/markdown",
        },
        "userLabels": {"managed_by": MANAGED_BY, "phase": "7"},
        "conditions": conditions,
        "combiner": "OR",
        "enabled": True,
        "notificationChannels": channels,
        "alertStrategy": {
            "autoClose": "604800s",
            "notificationPrompts": ["OPENED", "CLOSED"],
        },
        "severity": severity,
    }


def desired_policies(
    *, project: str, region: str, uptime_check_id: str, channels: list[str]
) -> list[dict[str, Any]]:
    check_filter = (
        'resource.type="uptime_url" AND '
        'metric.type="monitoring.googleapis.com/uptime_check/check_passed" '
        f'AND metric.label.check_id="{uptime_check_id}"'
    )
    policies = [
        _policy(
            display_name=f"{POLICY_PREFIX} API unavailable",
            severity="CRITICAL",
            documentation=(
                "The public health check failed or stopped reporting. Pause new "
                "admission, inspect the active Cloud Run revision, then follow "
                "docs/35-platform-operations-and-recovery.md. Do not replay "
                "provider effects."
            ),
            channels=channels,
            conditions=[
                _threshold_condition(
                    display_name="Health check pass fraction below 100%",
                    metric_filter=check_filter,
                    comparison="COMPARISON_LT",
                    threshold=1,
                    duration="120s",
                    aligner="ALIGN_FRACTION_TRUE",
                ),
                {
                    "displayName": "Health check has no data",
                    "conditionAbsent": {
                        "filter": check_filter,
                        "duration": "300s",
                        "trigger": {"count": 1},
                    },
                },
            ],
        ),
    ]

    error_conditions = []
    for service_name in ("co-founder", "co-founder-browser-worker"):
        error_conditions.append(
            _threshold_condition(
                display_name=f"{service_name} returned 5xx",
                metric_filter=(
                    'resource.type="cloud_run_revision" AND '
                    'metric.type="run.googleapis.com/request_count" AND '
                    f'resource.label.service_name="{service_name}" AND '
                    'metric.label.response_code_class="5xx"'
                ),
                comparison="COMPARISON_GT",
                threshold=0,
                duration="0s",
                aligner="ALIGN_RATE",
            )
        )
    policies.append(
        _policy(
            display_name=f"{POLICY_PREFIX} service errors",
            severity="ERROR",
            documentation=(
                "Cloud Run is returning server errors. Correlate the revision and "
                "queue attempt, preserve durable receipts, and use the rollback "
                "procedure in docs/35-platform-operations-and-recovery.md."
            ),
            channels=channels,
            conditions=error_conditions,
        )
    )

    for queue_name, limit in QUEUE_DEPTH_LIMITS.items():
        policies.append(
            _policy(
                display_name=f"{POLICY_PREFIX} queue backlog: {queue_name}",
                severity="WARNING",
                documentation=(
                    f"Queue `{queue_name}` reached its closed depth limit ({limit}). "
                    "Shed new low-priority work; do not delete tasks or bypass "
                    "idempotency. Follow docs/35-platform-operations-and-recovery.md."
                ),
                channels=channels,
                conditions=[
                    _threshold_condition(
                        display_name=f"{queue_name} depth at or above {limit}",
                        metric_filter=(
                            'resource.type="cloud_tasks_queue" AND '
                            'metric.type="cloudtasks.googleapis.com/queue/depth" AND '
                            f'resource.label.queue_id="{queue_name}" AND '
                            f'resource.label.location="{region}" AND '
                            f'resource.label.project_id="{project}"'
                        ),
                        comparison="COMPARISON_GE",
                        threshold=limit,
                        duration="300s",
                        aligner="ALIGN_MAX",
                    )
                ],
            )
        )
    return policies


def _managed(value: dict[str, Any]) -> bool:
    return (value.get("userLabels") or {}).get("managed_by") == MANAGED_BY


def _without_server_fields(value: dict[str, Any]) -> dict[str, Any]:
    clean = {
        key: item
        for key, item in value.items()
        if key
        in {
            "displayName",
            "documentation",
            "userLabels",
            "conditions",
            "combiner",
            "enabled",
            "notificationChannels",
            "alertStrategy",
            "severity",
        }
    }
    clean["conditions"] = [
        {key: item for key, item in condition.items() if key != "name"}
        for condition in clean.get("conditions", [])
    ]
    return clean


def _uptime_host(app_url: str) -> str:
    parsed = urlparse(app_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path not in {"", "/"}:
        raise ConfigurationError("app URL must be an HTTPS origin without a path")
    return parsed.hostname


def configure(
    *,
    project: str,
    region: str,
    app_url: str,
    requested_channels: list[str],
    apply: bool,
    confirmed_project: str,
    runner: Runner = _runner,
) -> dict[str, Any]:
    if not project or not region:
        raise ConfigurationError("project and region are required")
    if apply and confirmed_project != project:
        raise ConfigurationError("apply requires --confirm-project to equal --project")
    host = _uptime_host(app_url)
    channels = _json_command(runner, "beta", "monitoring", "channels", "list", "--project", project)
    if not isinstance(channels, list):
        raise ConfigurationError("notification channel list is unreadable")
    channel_names = _channel_names(channels, requested_channels)
    if not channel_names:
        raise ConfigurationError("at least one notification channel is required")

    uptime_checks = _json_command(
        runner, "monitoring", "uptime", "list-configs", "--project", project)
    if not isinstance(uptime_checks, list):
        raise ConfigurationError("uptime check list is unreadable")
    matches = [item for item in uptime_checks if item.get("displayName") == UPTIME_DISPLAY_NAME]
    if len(matches) > 1:
        raise ConfigurationError("duplicate Co-Founder uptime checks exist")
    created_uptime = False
    if matches:
        uptime = matches[0]
        if not _managed(uptime):
            raise ConfigurationError("refusing to adopt unmanaged uptime check")
        uptime_id = str(uptime.get("name") or "").rsplit("/", 1)[-1]
        if not uptime_id:
            raise ConfigurationError("uptime check has no resource name")
    elif apply:
        uptime = _json_command(
            runner,
            "monitoring",
            "uptime",
            "create",
            UPTIME_DISPLAY_NAME,
            "--project",
            project,
            "--resource-type=uptime-url",
            f"--resource-labels=host={host},project_id={project}",
            "--protocol=https",
            "--path=/health",
            "--port=443",
            "--request-method=get",
            "--status-codes=200",
            '--matcher-content="status":"ok"',
            "--matcher-type=contains-string",
            "--period=60s",
            "--timeout=10s",
            "--validate-ssl=true",
            f"--user-labels=managed_by={MANAGED_BY},phase=7",
        )
        uptime_id = str(uptime.get("name") or "").rsplit("/", 1)[-1]
        if not uptime_id:
            raise ConfigurationError("created uptime check has no resource name")
        created_uptime = True
    else:
        uptime_id = "DRY_RUN_UPTIME_CHECK_ID"

    desired = desired_policies(
        project=project, region=region, uptime_check_id=uptime_id, channels=channel_names
    )
    existing = _json_command(runner, "monitoring", "policies", "list", "--project", project)
    if not isinstance(existing, list):
        raise ConfigurationError("alert policy list is unreadable")
    by_display: dict[str, list[dict[str, Any]]] = {}
    for policy in existing:
        by_display.setdefault(str(policy.get("displayName") or ""), []).append(policy)

    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    for policy in desired:
        display_name = str(policy["displayName"])
        found = by_display.get(display_name, [])
        if len(found) > 1:
            raise ConfigurationError(f"duplicate alert policies: {display_name}")
        if found and not _managed(found[0]):
            raise ConfigurationError(f"refusing to adopt unmanaged alert policy: {display_name}")
        if found and _without_server_fields(found[0]) == policy:
            unchanged.append(display_name)
            continue
        if not apply:
            (updated if found else created).append(display_name)
            continue
        if found:
            resource_name = str(found[0].get("name") or "")
            if not resource_name:
                raise ConfigurationError(f"alert policy has no resource name: {display_name}")
            replacement = {**policy, "name": resource_name}
            _json_command(
                runner,
                "monitoring",
                "policies",
                "update",
                resource_name,
                "--project",
                project,
                f"--policy={json.dumps(replacement, separators=(',', ':'))}",
            )
            updated.append(display_name)
        else:
            _json_command(
                runner,
                "monitoring",
                "policies",
                "create",
                "--project",
                project,
                f"--policy={json.dumps(policy, separators=(',', ':'))}",
            )
            created.append(display_name)
    return {
        "status": "success",
        "mode": "apply" if apply else "dry-run",
        "project": project,
        "region": region,
        "notification_channel_count": len(channel_names),
        "uptime_check_id": uptime_id,
        "uptime_check_created": created_uptime,
        "policies_created": created,
        "policies_updated": updated,
        "policies_unchanged": unchanged,
        "policy_count": len(desired),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument(
        "--notification-channel",
        action="append",
        default=[],
        help="Existing notification-channel resource name or final ID; repeatable",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-project", default="")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = configure(
            project=args.project,
            region=args.region,
            app_url=args.app_url,
            requested_channels=args.notification_channel,
            apply=args.apply,
            confirmed_project=args.confirm_project,
        )
    except (ConfigurationError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": True,
                    "error_code": "monitoring_configuration_invalid",
                    "message": str(exc),
                }
            )
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

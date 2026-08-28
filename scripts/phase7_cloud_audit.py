#!/usr/bin/env python3
"""Read-only audit of cloud controls required by docs/34 Phase 7."""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any


def _gcloud(*args: str) -> Any:
    completed = subprocess.run(
        ("gcloud", *args, "--format=json"), check=False,
        capture_output=True, text=True)
    if completed.returncode:
        return {"error": True, "message": completed.stderr.strip()[-500:]}
    text = completed.stdout.strip()
    return json.loads(text) if text else {}


def _path(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _auth_error() -> str | None:
    completed = subprocess.run(
        ("gcloud", "auth", "print-access-token"), check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    return None if completed.returncode == 0 else completed.stderr.strip()[-500:]


def audit(project: str, region: str) -> dict[str, Any]:
    auth_error = _auth_error()
    if auth_error:
        return {
            "schema_version": 1, "project": project, "region": region,
            "checks": {}, "ready": False, "resources": {},
            "errors": {"authentication": {
                "error": True, "message": auth_error}},
        }
    firestore = _gcloud(
        "firestore", "databases", "describe", "--project", project,
        "--database=(default)")
    sql = _gcloud(
        "sql", "instances", "describe", "co-founder-sessions",
        "--project", project)
    app = _gcloud(
        "run", "services", "describe", "co-founder", "--project", project,
        "--region", region)
    browser = _gcloud(
        "run", "services", "describe", "co-founder-browser-worker",
        "--project", project, "--region", region)
    bucket = _gcloud(
        "storage", "buckets", "describe", f"gs://{project}-artifacts",
        "--project", project)
    queue_names = (
        "co-founder-events", "co-founder-browser-expiry", "co-founder-timers",
        "co-founder-provider-events", "co-founder-discovery-ingestion",
        "co-founder-reconciliation", "co-founder-interactive",
    )
    queues = {name: _gcloud(
        "tasks", "queues", "describe", name, "--project", project,
        "--location", region) for name in queue_names}
    policies = _gcloud("monitoring", "policies", "list", "--project", project)
    app_max_scale = _path(
        app, "spec", "template", "metadata", "annotations",
        "autoscaling.knative.dev/maxScale")
    browser_max_scale = _path(
        browser, "spec", "template", "metadata", "annotations",
        "autoscaling.knative.dev/maxScale")
    browser_concurrency = _path(
        browser, "spec", "template", "spec", "containerConcurrency")
    browser_account = str(_path(
        browser, "spec", "template", "spec", "serviceAccountName") or "")
    queue_concurrency = {
        "co-founder-events": 1, "co-founder-browser-expiry": 4,
        "co-founder-timers": 8, "co-founder-provider-events": 8,
        "co-founder-discovery-ingestion": 4,
        "co-founder-reconciliation": 4, "co-founder-interactive": 4,
    }
    queues_correct = all(
        not value.get("error")
        and int(_path(value, "rateLimits", "maxConcurrentDispatches") or -1)
        == queue_concurrency[name]
        and int(_path(value, "retryConfig", "maxAttempts") or -1) == 8
        for name, value in queues.items())
    checks = {
        "firestore_pitr": _path(
            firestore, "pointInTimeRecoveryEnablement") == "POINT_IN_TIME_RECOVERY_ENABLED",
        "firestore_delete_protection": _path(
            firestore, "deleteProtectionState") == "DELETE_PROTECTION_ENABLED",
        "sql_backups": bool(_path(sql, "settings", "backupConfiguration", "enabled")),
        "sql_pitr": bool(_path(
            sql, "settings", "backupConfiguration", "pointInTimeRecoveryEnabled")),
        "sql_delete_protection": bool(_path(sql, "settings", "deletionProtectionEnabled")),
        "artifact_versioning": bool(_path(bucket, "versioning", "enabled")),
        "artifact_soft_delete": bool(_path(
            bucket, "softDeletePolicy", "retentionDurationSeconds")
            or _path(bucket, "softDeletePolicy", "retentionDuration")),
        "public_service_scaled": (
            _path(app, "metadata", "name") == "co-founder"
            and int(app_max_scale or 0) >= 2),
        "browser_isolated": (
            _path(browser, "metadata", "name") == "co-founder-browser-worker"
            and int(browser_max_scale or 0) == 1
            and int(browser_concurrency or 0) == 1
            and browser_account.startswith("browser-worker@")),
        "queue_lanes_bounded": queues_correct,
        "monitoring_policies_present": isinstance(policies, list) and bool(policies),
    }
    return {
        "schema_version": 1, "project": project, "region": region,
        "checks": checks, "ready": all(checks.values()),
        "resources": {
            "firestore_location": _path(firestore, "locationId"),
            "sql_region": _path(sql, "region"),
            "sql_availability": _path(sql, "settings", "availabilityType"),
            "artifact_location": _path(bucket, "location"),
            "queue_names": sorted(queues),
            "monitoring_policy_count": len(policies) if isinstance(policies, list) else 0,
        },
        "errors": {name: value for name, value in {
            "firestore": firestore, "sql": sql, "app": app,
            "browser": browser, "bucket": bucket, "policies": policies,
            **{f"queue:{name}": value for name, value in queues.items()},
        }.items() if isinstance(value, dict) and value.get("error")},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    args = parser.parse_args()
    result = audit(args.project, args.region)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

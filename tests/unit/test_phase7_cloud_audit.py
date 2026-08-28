from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "phase7_cloud_audit", ROOT / "scripts" / "phase7_cloud_audit.py")
assert SPEC and SPEC.loader
audit_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_module)


def test_expired_auth_fails_once_without_querying_resources(monkeypatch):
    monkeypatch.setattr(audit_module, "_auth_error", lambda: "reauth required")

    def forbidden(*_args):
        raise AssertionError("resource query should not run without auth")

    monkeypatch.setattr(audit_module, "_gcloud", forbidden)
    result = audit_module.audit("project-a", "us-central1")
    assert result["ready"] is False
    assert result["errors"]["authentication"]["message"] == "reauth required"


def test_cloud_audit_requires_recovery_and_isolation_controls(monkeypatch):
    monkeypatch.setattr(audit_module, "_auth_error", lambda: None)

    def fake_gcloud(*args):
        command = " ".join(args)
        if command.startswith("firestore databases describe"):
            return {"locationId": "nam5",
                    "pointInTimeRecoveryEnablement": "POINT_IN_TIME_RECOVERY_ENABLED",
                    "deleteProtectionState": "DELETE_PROTECTION_ENABLED"}
        if command.startswith("sql instances describe"):
            return {"region": "us-central1", "settings": {
                "availabilityType": "ZONAL", "deletionProtectionEnabled": True,
                "backupConfiguration": {"enabled": True,
                                        "pointInTimeRecoveryEnabled": True}}}
        if "run services describe co-founder-browser-worker" in command:
            return {"metadata": {"name": "co-founder-browser-worker"},
                    "spec": {"template": {
                        "metadata": {"annotations": {
                            "autoscaling.knative.dev/maxScale": "1"}},
                        "spec": {"containerConcurrency": 1,
                                 "serviceAccountName": "browser-worker@project-a"}}}}
        if "run services describe co-founder " in command:
            return {"metadata": {"name": "co-founder"},
                    "spec": {"template": {"metadata": {"annotations": {
                        "autoscaling.knative.dev/maxScale": "10"}}}}}
        if command.startswith("storage buckets describe"):
            return {"location": "US", "versioning_enabled": True,
                    "soft_delete_policy": {
                        "retentionDurationSeconds": "604800"}}
        if command.startswith("tasks queues describe"):
            queue = args[3]
            expected = {
                "co-founder-events": 1, "co-founder-browser-expiry": 4,
                "co-founder-timers": 8, "co-founder-provider-events": 8,
                "co-founder-discovery-ingestion": 4,
                "co-founder-reconciliation": 4, "co-founder-interactive": 4,
            }
            return {"rateLimits": {"maxConcurrentDispatches": expected[queue]},
                    "retryConfig": {"maxAttempts": 8}}
        if command.startswith("monitoring policies list"):
            return [{"displayName": "Co-Founder dead-letter wake"}]
        raise AssertionError(command)

    monkeypatch.setattr(audit_module, "_gcloud", fake_gcloud)
    result = audit_module.audit("project-a", "us-central1")
    assert result["ready"] is True
    assert all(result["checks"].values())

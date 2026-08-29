#!/usr/bin/env python3
"""Prepare and control the isolated, single-Founder Spec 39 local M2 pilot."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.actor_identity import ActorPrincipal, WorkspaceRole  # noqa: E402
from services.durable_memory import (  # noqa: E402
    DurableMemoryService,
    MemoryMode,
    PilotPolicy,
    StoreDeletionDenyLedger,
    UnavailableDeletionDenyLedger,
    founder_failure_message,
)
from services.local_pilot_store import (  # noqa: E402
    PILOT_ROOT,
    SQLiteDurableStore,
)
from services.memory_export import (  # noqa: E402
    LOCAL_EXPORT_KEY_PATH,
    LOCAL_EXPORT_ROOT,
    LocalEncryptedMemoryExportStore,
)
from services.workflow_contracts import stable_id  # noqa: E402

WORKSPACE_ID = "local_m2_pilot"
FOUNDER_ID = "local_founder"
PILOT_PORT = 8093
DATA_PATH = PILOT_ROOT / "pilot-data.sqlite3"
LEDGER_PATH = PILOT_ROOT / "deletion-ledger.sqlite3"
SESSION_PATH = PILOT_ROOT / "sessions.sqlite3"
VALIDATION_DATA_PATH = PILOT_ROOT / "validation-data.sqlite3"
VALIDATION_LEDGER_PATH = PILOT_ROOT / "validation-ledger.sqlite3"
RESTORE_PATH = PILOT_ROOT / "validation-restore.sqlite3"
VALIDATION_EXPORT_ROOT = PILOT_ROOT / "validation-exports"
VALIDATION_EXPORT_KEY_PATH = PILOT_ROOT / "validation-export-key.bin"
FOUNDER_VALIDATION_RESTORE_PATH = PILOT_ROOT / "founder-validation-restore.sqlite3"
ENV_PATH = PILOT_ROOT / "pilot.env"
EVIDENCE_PATH = PILOT_ROOT / "entry-evidence.json"
RUNBOOK_PATH = PILOT_ROOT / "README.md"


def _principal() -> ActorPrincipal:
    membership_id = stable_id("membership", WORKSPACE_ID, FOUNDER_ID)
    return ActorPrincipal(
        actor_id=FOUNDER_ID,
        workspace_id=WORKSPACE_ID,
        role=WorkspaceRole.FOUNDER,
        role_grants=frozenset({"workspace.*"}),
        candidate_assignments=frozenset(),
        interview_assignments=frozenset(),
        session_auth_time=int(datetime.now(timezone.utc).timestamp()),
        membership_version=1,
        principal_kind="LOCAL_AUTHENTICATED_FOUNDER",
        membership_id=membership_id,
    )


def _policy() -> PilotPolicy:
    return PilotPolicy(
        deployment_enabled=True,
        workspace_allowlist=frozenset(),
        backup_residual_days=30,
        backup_policy_attested=False,
        backup_policy_ref="local-pilot-restore-fence-evidence",
        local_pilot=True,
        local_entry_attested=True,
        local_workspace_id=WORKSPACE_ID,
        local_founder_id=FOUNDER_ID,
    )


async def _seed_founder(store: SQLiteDurableStore) -> None:
    principal = _principal()
    active = await store.list(
        "workspace_members",
        filters={"workspace_id": WORKSPACE_ID, "status": "ACTIVE"},
        limit=3,
    )
    expected = {
        "schema_version": 1,
        "membership_id": principal.membership_id,
        "workspace_id": WORKSPACE_ID,
        "actor_id": FOUNDER_ID,
        "product_role": "FOUNDER",
        "status": "ACTIVE",
        "authenticated": True,
        "synthetic": True,
        "local_only": True,
        "version": 1,
    }
    if active:
        if len(active) != 1 or any(active[0].get(key) != value for key, value in expected.items()):
            raise RuntimeError("pilot data has an ineligible or additional active identity")
        return
    if not await store.create("workspace_members", principal.membership_id, expected):
        raise RuntimeError("local Founder record could not be created")


def _unlink_sqlite(path: Path) -> None:
    for candidate in (path, Path(f"{path}-journal"), Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.unlink()


def _atomic_text(path: Path, value: str, *, mode: int = 0o600) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.chmod(mode)
    temporary.replace(path)


async def _exercise() -> dict[str, bool]:
    for path in (VALIDATION_DATA_PATH, VALIDATION_LEDGER_PATH, RESTORE_PATH):
        _unlink_sqlite(path)
    if VALIDATION_EXPORT_ROOT.exists():
        shutil.rmtree(VALIDATION_EXPORT_ROOT)
    if VALIDATION_EXPORT_KEY_PATH.exists():
        VALIDATION_EXPORT_KEY_PATH.unlink()
    data = SQLiteDurableStore(VALIDATION_DATA_PATH)
    ledger_data = SQLiteDurableStore(VALIDATION_LEDGER_PATH)
    await _seed_founder(data)
    principal = _principal()
    service = DurableMemoryService(
        data,
        ledger=StoreDeletionDenyLedger(ledger_data),
        policy=_policy(),
        export_store=LocalEncryptedMemoryExportStore(
            root=VALIDATION_EXPORT_ROOT, key_path=VALIDATION_EXPORT_KEY_PATH
        ),
    )
    checks: dict[str, bool] = {}

    enabled = await service.set_enabled(
        principal=principal, enabled=True, client_request_id="local-entry-enable"
    )
    checks["authenticated_founder_entry"] = enabled.get("status") == "success"

    remembered = await service.remember(
        principal=principal,
        session_id="entry-standard",
        session_mode=MemoryMode.STANDARD.value,
        kind="PREFERENCE",
        summary="Use concise pilot progress summaries.",
        tags=["length"],
        client_request_id="local-entry-remember",
    )
    checks["remember"] = remembered.get("status") == "success"
    memory_id = str(remembered.get("memory_id") or "")

    corrected = await service.correct(
        principal=principal,
        memory_id=memory_id,
        expected_version=1,
        summary="Use short, direct pilot progress summaries.",
        control_session_id="entry-standard",
        control_session_mode="STANDARD",
        client_request_id="local-entry-correct",
    )
    checks["correct"] = corrected.get("status") == "success"
    corrected_id = str(corrected.get("memory_id") or "")

    pinned = await service.pin(
        principal=principal,
        memory_id=corrected_id,
        expected_version=1,
        control_session_id="entry-standard",
        control_session_mode="STANDARD",
        client_request_id="local-entry-pin",
    )
    checks["pin"] = pinned.get("status") == "success" and bool(
        (pinned.get("memory") or {}).get("pinned")
    )

    private_write = await service.remember(
        principal=principal,
        session_id="entry-private",
        session_mode=MemoryMode.PRIVATE.value,
        kind="PREFERENCE",
        summary="This must not persist.",
        tags=[],
        client_request_id="local-entry-private",
    )
    private_recall = await service.recall(
        principal=principal,
        session_mode=MemoryMode.PRIVATE.value,
        query="pilot progress",
        purpose="PERSONALIZE_RESPONSE",
    )
    checks["private_session_isolation"] = (
        private_write.get("error_code") == "memory_disabled_for_session"
        and private_recall.get("error_code") == "memory_disabled_for_session"
    )

    expiring = await service.remember(
        principal=principal,
        session_id="entry-standard",
        session_mode="STANDARD",
        kind="REUSABLE_CONTEXT",
        summary="The local expiry simulation marker is amber.",
        tags=[],
        client_request_id="local-entry-expiry",
    )
    expiring_row = await data.get("memory_items", expiring["memory_id"])
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    await data.compare_and_set(
        "memory_items",
        expiring["memory_id"],
        int(expiring_row["version"]),
        {"expires_at": past, "expires_at_ts": past},
    )
    expired_recall = await service.recall(
        principal=principal,
        session_mode="STANDARD",
        query="amber marker",
        purpose="PERSONALIZE_RESPONSE",
    )
    checks["expiry_simulation"] = expired_recall.get("hits") == []

    restore_memory = await service.remember(
        principal=principal,
        session_id="entry-standard",
        session_mode="STANDARD",
        kind="REUSABLE_CONTEXT",
        summary="The restore fence validation marker is cobalt.",
        tags=[],
        client_request_id="local-entry-restore",
    )
    shutil.copy2(VALIDATION_DATA_PATH, RESTORE_PATH)
    forgotten = await service.forget(
        principal=principal,
        memory_id=restore_memory["memory_id"],
        expected_version=1,
        client_request_id="local-entry-forget",
    )
    checks["forget"] = forgotten.get("status") == "success"
    restored_service = DurableMemoryService(
        SQLiteDurableStore(RESTORE_PATH),
        ledger=StoreDeletionDenyLedger(ledger_data),
        policy=_policy(),
    )
    restored_recall = await restored_service.recall(
        principal=principal,
        session_mode="STANDARD",
        query="cobalt marker",
        purpose="PERSONALIZE_RESPONSE",
    )
    checks["rollback_restore_fence"] = restored_recall.get("hits") == []

    export_request = await service.request_export(
        principal=principal, session_mode="STANDARD", client_request_id="local-entry-export"
    )
    export_id = str(export_request.get("export_id") or "")
    export_processed = await service.process_export(export_id=export_id)
    export_download = await service.download_export(
        principal=principal, session_mode="STANDARD", export_id=export_id
    )
    encrypted_path = VALIDATION_EXPORT_ROOT / f"{export_id}.aesgcm"
    encrypted_bytes = encrypted_path.read_bytes() if encrypted_path.exists() else b""
    try:
        export_document = json.loads(bytes(export_download.get("payload") or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        export_document = {}
    checks["encrypted_async_export"] = all(
        (
            export_request.get("export_status") == "PENDING",
            export_processed.get("export_status") == "READY",
            export_document.get("export_kind") == "SPEC39_OPTIONAL_MEMORY",
            "omission_count" not in export_document,
            b"The restore fence validation marker is cobalt." not in encrypted_bytes,
            encrypted_path.exists(),
            VALIDATION_EXPORT_KEY_PATH.exists(),
        )
    )
    await service.export_store.delete(export_id=export_id)

    delete_plan = await service.delete_all_plan(principal=principal)
    checks["delete_all_plan"] = (
        delete_plan.get("status") == "success"
        and int(delete_plan.get("lineage_count") or 0) >= 1
        and delete_plan.get("memory_will_be_disabled") is True
    )
    deleted_all = await service.delete_all(
        principal=principal,
        expected_plan_hash=str(delete_plan.get("plan_hash") or ""),
        client_request_id="local-entry-delete-all",
    )
    remaining = await data.list(
        "memory_items",
        filters={"workspace_id": WORKSPACE_ID, "lifecycle_status": "ACTIVE"},
        limit=1000,
    )
    checks["delete_all"] = (
        deleted_all.get("status") == "success"
        and deleted_all.get("read_enabled") is False
        and not remaining
    )

    disabled = await service.set_enabled(
        principal=principal, enabled=False, client_request_id="local-entry-disable"
    )
    disabled_recall = await service.recall(
        principal=principal,
        session_mode="STANDARD",
        query="pilot progress",
        purpose="PERSONALIZE_RESPONSE",
    )
    checks["disable_kill_switch"] = (
        disabled.get("status") == "success"
        and disabled_recall.get("error_code") == "memory_disabled"
    )

    await service.set_enabled(
        principal=principal, enabled=True, client_request_id="local-entry-reenable-for-degradation"
    )
    degraded = DurableMemoryService(data, ledger=UnavailableDeletionDenyLedger(), policy=_policy())
    degradation = await degraded.recall(
        principal=principal,
        session_mode="STANDARD",
        query="pilot progress",
        purpose="PERSONALIZE_RESPONSE",
    )
    safe_copy = founder_failure_message(degradation) or ""
    checks["safe_failure_degradation"] = (
        degradation.get("error_code") == "memory_deny_ledger_unavailable"
        and "Current workspace facts and work are still up to date" in safe_copy
    )
    return checks


def _environment_text() -> str:
    values = {
        "DURABLE_MEMORY_M2_ENABLED": "true",
        "DURABLE_MEMORY_M2_WORKSPACE_ALLOWLIST": "",
        "DURABLE_MEMORY_BACKUP_POLICY_ATTESTED": "false",
        "DURABLE_MEMORY_BACKUP_POLICY_REF": "",
        "DURABLE_MEMORY_M2_LOCAL_PILOT": "1",
        "DURABLE_MEMORY_M2_LOCAL_ENTRY_ATTESTED": "1",
        "DURABLE_MEMORY_M2_LOCAL_WORKSPACE_ID": WORKSPACE_ID,
        "DURABLE_MEMORY_M2_LOCAL_FOUNDER_ID": FOUNDER_ID,
        "DURABLE_MEMORY_M2_LOCAL_DATA_PATH": str(DATA_PATH),
        "DURABLE_MEMORY_M2_LOCAL_LEDGER_PATH": str(LEDGER_PATH),
        "SESSION_SERVICE_URI": f"sqlite+aiosqlite:///{SESSION_PATH}",
        "ARTIFACT_SERVICE_URI": f"file://{PILOT_ROOT / 'artifacts'}",
        "PERSISTENT_MEMORY_BACKEND": "disabled",
        "SPEC39_M2_LOCAL_PILOT_PORT": str(PILOT_PORT),
    }
    return "".join(f"export {key}={value}\n" for key, value in values.items())


def _runbook_text() -> str:
    return f"""# Spec 39 local M2 pilot

This ignored directory is the only pilot namespace. It contains one synthetic,
authenticated `FOUNDER` identity. It has no OWNER/OPERATOR product role and no
dependency on the production-looking cloud project.

Enter: source `{ENV_PATH}`, then start the app from this worktree on dedicated
port `{PILOT_PORT}`. Open `http://127.0.0.1:{PILOT_PORT}`. The canonical normal
app port 8090 is never occupied by the pilot. In Settings → What Alex knows,
use the explicit enable control. The prepared app is already enabled after
entry checks pass; sessions remain STANDARD unless Private is chosen at session
creation.

Measured validation: with the pilot running on port `{PILOT_PORT}`, source the
normal local `.env` and then `pilot.env`, and run
`python scripts/validate_local_m2_pilot.py`. The validation uses only synthetic
data, writes content-free evidence to `founder-validation-evidence.json`,
forgets every item it creates, and leaves memory disabled.

Exit/kill switch: run `python scripts/local_m2_pilot.py disable`, then restart
the local app so the environment-level flag is off. To remove pilot state, stop
the app and run `python scripts/local_m2_pilot.py clear --confirm-clear LOCAL_M2_PILOT`.

Data: `{DATA_PATH}`. Separately administered deletion ledger:
`{LEDGER_PATH}`. Ordinary local application data is never migrated or read into
these files. Local chat sessions use `{SESSION_PATH}`. Restore tests copy only
the validation DB and retain the ledger.

Cloud TTL, backup-policy attestation, named security/privacy reviews, deployment
authority, and the production release entry gate remain pre-production gates.
"""


async def prepare() -> dict[str, Any]:
    if os.environ.get("K_SERVICE"):
        raise RuntimeError("the local M2 pilot refuses Cloud Run")
    PILOT_ROOT.mkdir(parents=True, exist_ok=True)
    checks = await _exercise()
    if not all(checks.values()):
        return {"status": "blocked", "checks": checks}
    live_data = SQLiteDurableStore(DATA_PATH)
    live_ledger = SQLiteDurableStore(LEDGER_PATH)
    # ``prepare`` is an explicit synthetic-lane reset boundary. Re-running the
    # measured pack must not inherit its own prior test writes, while runtime
    # requests after preparation still share the normal 100-write daily cap.
    for budget in await live_data.list(
        "workspace_budgets", filters={"workspace_id": WORKSPACE_ID}, limit=100
    ):
        await live_data.delete("workspace_budgets", str(budget["budget_id"]))
    if SESSION_PATH.exists():
        SESSION_PATH.chmod(0o600)
    await _seed_founder(live_data)
    live_service = DurableMemoryService(
        live_data, ledger=StoreDeletionDenyLedger(live_ledger), policy=_policy()
    )
    enabled = await live_service.set_enabled(
        principal=_principal(), enabled=True, client_request_id="local-pilot-live-enable"
    )
    if enabled.get("error"):
        return {
            "status": "blocked",
            "checks": checks,
            "enable_error_code": enabled.get("error_code"),
        }
    evidence = {
        "schema_version": 1,
        "pilot": "spec39-m2-local-single-founder",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "candidate_sha256": subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "check_durable_memory_m2_release.py"),
                "--print-candidate-hash",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "checks": checks,
        "passed": True,
        "identity": {
            "authenticated": True,
            "product_role": "FOUNDER",
            "synthetic": True,
            "count": 1,
        },
        "isolation": {
            "data_file_separate": DATA_PATH != LEDGER_PATH,
            "local_only": True,
            "setup_command_cloud_calls": 0,
            "final_runtime_cloud_dependency": False,
        },
        "external_effects_executed": 0,
        "automatic_extraction_calls": 0,
        "managed_memory_calls": 0,
        "preproduction_gates": {
            "cloud_ttl": "PENDING",
            "cloud_backup_policy": "PENDING",
            "qualified_cloud_export_delivery": "PENDING",
            "named_security_privacy_reviews": "PENDING",
            "deployment_authority": "PENDING",
        },
        "live_store_counts": await live_data.counts(),
        "ledger_store_counts": await live_ledger.counts(),
    }
    _atomic_text(ENV_PATH, _environment_text())
    _atomic_text(EVIDENCE_PATH, json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    _atomic_text(RUNBOOK_PATH, _runbook_text(), mode=0o600)
    return {
        "status": "ready",
        "checks": checks,
        "environment": str(ENV_PATH),
        "evidence": str(EVIDENCE_PATH),
        "runbook": str(RUNBOOK_PATH),
    }


async def disable() -> dict[str, Any]:
    if DATA_PATH.exists():
        data = SQLiteDurableStore(DATA_PATH)
        ledger = SQLiteDurableStore(LEDGER_PATH)
        service = DurableMemoryService(
            data, ledger=StoreDeletionDenyLedger(ledger), policy=_policy()
        )
        await service.set_enabled(
            principal=_principal(),
            enabled=False,
            client_request_id=(
                "local-pilot-disable-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            ),
        )
    if ENV_PATH.exists():
        text = ENV_PATH.read_text(encoding="utf-8")
        text = text.replace(
            "export DURABLE_MEMORY_M2_ENABLED=true", "export DURABLE_MEMORY_M2_ENABLED=false"
        )
        text = text.replace(
            "export DURABLE_MEMORY_M2_LOCAL_PILOT=1", "export DURABLE_MEMORY_M2_LOCAL_PILOT=0"
        )
        text = text.replace(
            "export DURABLE_MEMORY_M2_LOCAL_ENTRY_ATTESTED=1",
            "export DURABLE_MEMORY_M2_LOCAL_ENTRY_ATTESTED=0",
        )
        _atomic_text(ENV_PATH, text)
    return {
        "status": "disabled",
        "restart_required": True,
        "data_preserved": DATA_PATH.exists(),
        "ledger_preserved": LEDGER_PATH.exists(),
    }


def clear(confirm: str) -> dict[str, Any]:
    if confirm != "LOCAL_M2_PILOT":
        raise RuntimeError("clear requires --confirm-clear LOCAL_M2_PILOT")
    removed: list[str] = []
    for path in (
        DATA_PATH,
        LEDGER_PATH,
        SESSION_PATH,
        VALIDATION_DATA_PATH,
        VALIDATION_LEDGER_PATH,
        RESTORE_PATH,
        FOUNDER_VALIDATION_RESTORE_PATH,
        ENV_PATH,
        LOCAL_EXPORT_KEY_PATH,
        VALIDATION_EXPORT_KEY_PATH,
    ):
        for candidate in (path, Path(f"{path}-journal"), Path(f"{path}-wal"), Path(f"{path}-shm")):
            if candidate.exists():
                candidate.unlink()
                removed.append(candidate.name)
    for directory in (LOCAL_EXPORT_ROOT, VALIDATION_EXPORT_ROOT):
        if directory.exists():
            shutil.rmtree(directory)
            removed.append(directory.name)
    return {
        "status": "cleared",
        "removed": sorted(removed),
        "content_free_evidence_preserved": EVIDENCE_PATH.exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "disable", "clear"))
    parser.add_argument("--confirm-clear", default="")
    args = parser.parse_args()
    try:
        result = (
            clear(args.confirm_clear)
            if args.command == "clear"
            else asyncio.run(prepare() if args.command == "prepare" else disable())
        )
    except Exception as exc:
        result = {"status": "blocked", "error": type(exc).__name__, "message": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"ready", "disabled", "cleared"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

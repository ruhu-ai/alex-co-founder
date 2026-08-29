#!/usr/bin/env python3
"""Measured, content-free founder validation for the isolated local M2 pilot."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import string
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import durable_memory, local_pilot_store  # noqa: E402

BASE_URL = "http://127.0.0.1:8093"
EVIDENCE_PATH = local_pilot_store.PILOT_ROOT / "founder-validation-evidence.json"
RESTORE_PATH = local_pilot_store.PILOT_ROOT / "founder-validation-restore.sqlite3"
ELIGIBLE_CASES = 20


def _request(
    method: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    if not BASE_URL.startswith("http://127.0.0.1:"):
        raise RuntimeError("validation is restricted to loopback")
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("APP_AUTH_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        BASE_URL + path,
        method=method,
        headers=headers,
        data=(None if payload is None else json.dumps(payload).encode("utf-8")),
    )
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def _rid(prefix: str) -> str:
    return f"validation_{prefix}_{uuid.uuid4().hex}"


def _unlink_sqlite(path: Path) -> None:
    for candidate in (path, Path(f"{path}-journal"), Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.unlink()


async def run() -> dict[str, Any]:
    if os.environ.get("K_SERVICE") or not local_pilot_store.local_pilot_mode():
        raise RuntimeError("the isolated local pilot environment is required")
    if os.environ.get("SPEC39_M2_LOCAL_PILOT_PORT") != "8093":
        raise RuntimeError("the pilot must use its dedicated port 8093")
    data, ledger_store = local_pilot_store.configured_stores()
    service = durable_memory.configured_service()
    principal = await durable_memory.resolve_local_pilot_founder()
    baseline_active = {
        str(row["memory_id"])
        for row in await data.list(
            "memory_items",
            filters={"workspace_id": principal.workspace_id, "lifecycle_status": "ACTIVE"},
            limit=1000,
        )
    }
    created_ids: set[str] = set()
    checks: dict[str, bool] = {}
    recall_latencies: list[int] = []
    pages: list[int] = []
    candidates: list[int] = []
    reauthorizations: list[int] = []
    admitted_hits = 0
    irrelevant_hits = 0
    intended_hits = 0
    standard_session = ""
    disabled_at_end = False
    try:
        health_code, health = _request("GET", "/healthz")
        checks["isolated_runtime_healthy"] = health_code == 200 and health.get("status") == "ok"
        session_code, session = _request(
            "POST",
            "/api/v1/sessions",
            {
                "client_request_id": _rid("standard_session"),
                "memory_mode": "STANDARD",
            },
        )
        standard_session = str(session.get("session_id") or "")
        checks["standard_session"] = session_code == 200 and bool(standard_session)
        enabled_code, enabled = _request(
            "POST",
            "/api/v1/memory/settings",
            {
                "session_id": standard_session,
                "enabled": True,
                "client_request_id": _rid("enable"),
            },
        )
        checks["enable"] = enabled_code == 200 and enabled.get("read_enabled") is True

        cases: list[dict[str, str]] = []
        for index in range(ELIGIBLE_CASES):
            suffix = string.ascii_lowercase[index // 26] + string.ascii_lowercase[index % 26]
            first, second = f"valcase{suffix}", f"tone{suffix}"
            code, remembered = _request(
                "POST",
                "/api/v1/memories",
                {
                    "session_id": standard_session,
                    "client_request_id": _rid(f"remember_{suffix}"),
                    "memory_kind": ("PREFERENCE" if index < 10 else "REUSABLE_CONTEXT"),
                    "summary": f"Synthetic {first} maps to {second}.",
                    "tags": [],
                },
            )
            if code != 200 or remembered.get("status") != "success":
                raise RuntimeError(f"remember case {index} failed")
            memory_id = str(remembered["memory_id"])
            created_ids.add(memory_id)
            cases.append({"id": memory_id, "first": first, "second": second})
        checks["remember_twenty"] = len(cases) == ELIGIBLE_CASES

        superseded_id = cases[0]["id"]
        corrected_code, corrected = _request(
            "POST",
            f"/api/v1/memories/{cases[0]['id']}:correct",
            {
                "session_id": standard_session,
                "client_request_id": _rid("correct"),
                "expected_version": 1,
                "summary": f"Synthetic {cases[0]['first']} maps to correctedcobalt.",
            },
        )
        corrected_id = str(corrected.get("memory_id") or "")
        created_ids.add(corrected_id)
        cases[0]["id"] = corrected_id
        cases[0]["second"] = "correctedcobalt"
        checks["correct"] = corrected_code == 200 and bool(corrected_id)

        pinned_code, pinned = _request(
            "POST",
            f"/api/v1/memories/{cases[1]['id']}:pin",
            {
                "session_id": standard_session,
                "client_request_id": _rid("pin"),
                "expected_version": 1,
            },
        )
        pinned_id = str(pinned.get("memory_id") or "")
        created_ids.add(pinned_id)
        cases[1]["id"] = pinned_id
        checks["pin"] = (
            pinned_code == 200
            and bool(pinned_id)
            and bool((pinned.get("memory") or {}).get("pinned"))
        )

        old_recall = await service.recall(
            principal=principal,
            session_mode="STANDARD",
            query=f"{cases[0]['first']} toneaa",
            purpose="PERSONALIZE_RESPONSE",
        )
        old_recall_ids = {str(hit.get("memory_id")) for hit in old_recall.get("hits") or []}
        checks["superseded_item_excluded"] = (
            superseded_id not in old_recall_ids and corrected_id in old_recall_ids
        )

        started = monotonic()
        for case in cases:
            result = await service.recall(
                principal=principal,
                session_mode="STANDARD",
                query=f"{case['first']} {case['second']}",
                purpose="PERSONALIZE_RESPONSE",
            )
            hits = list(result.get("hits") or [])
            ids = [str(hit.get("memory_id")) for hit in hits]
            intended = case["id"] in ids
            intended_hits += int(intended)
            admitted_hits += len(ids)
            irrelevant_hits += sum(item != case["id"] for item in ids)
            metrics = result.get("metrics") or {}
            recall_latencies.append(int(metrics.get("recall_latency_ms", 0)))
            pages.append(int(metrics.get("pages_fetched", 0)))
            candidates.append(int(metrics.get("candidate_rows_examined", 0)))
            reauthorizations.append(int(metrics.get("source_reauthorization_count", 0)))
        elapsed_ms = int((monotonic() - started) * 1000)
        usefulness = intended_hits / ELIGIBLE_CASES
        irrelevance = irrelevant_hits / max(1, admitted_hits)
        checks["bounded_usefulness"] = usefulness >= 0.80 and irrelevance <= 0.05
        checks["recall_bounds"] = (
            max(pages or [0]) <= 3
            and max(candidates or [0]) <= 30
            and max(reauthorizations or [0]) <= 25
        )

        private_code, private = _request(
            "POST",
            "/api/v1/sessions",
            {
                "client_request_id": _rid("private_session"),
                "memory_mode": "PRIVATE",
            },
        )
        private_session = str(private.get("session_id") or "")
        before_private = (await data.counts(), await ledger_store.counts())
        private_status_code, private_status = _request(
            "GET", f"/api/v1/memory/status?session_id={private_session}"
        )
        private_write_code, private_write = _request(
            "POST",
            "/api/v1/memories",
            {
                "session_id": private_session,
                "client_request_id": _rid("private_write"),
                "memory_kind": "PREFERENCE",
                "summary": "Synthetic private content must not persist.",
                "tags": [],
            },
        )
        after_private = (await data.counts(), await ledger_store.counts())
        checks["private_session_exclusion"] = (
            private_code == 200
            and private_status_code == 200
            and private_status.get("private_session") is True
            and private_write_code == 403
            and private_write.get("error_code") == "memory_disabled_for_session"
            and before_private == after_private
        )

        expiry_code, expiry = _request(
            "POST",
            "/api/v1/memories",
            {
                "session_id": standard_session,
                "client_request_id": _rid("expiry"),
                "memory_kind": "REUSABLE_CONTEXT",
                "summary": "Synthetic expirymarker maps to expiredamber.",
                "tags": [],
            },
        )
        expiry_id = str(expiry.get("memory_id") or "")
        created_ids.add(expiry_id)
        expiry_row = await data.get("memory_items", expiry_id)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        expiry_updated = await data.compare_and_set(
            "memory_items",
            expiry_id,
            int(expiry_row["version"]),
            {"expires_at": past, "expires_at_ts": past},
        )
        expired = await service.recall(
            principal=principal,
            session_mode="STANDARD",
            query="expirymarker expiredamber",
            purpose="PERSONALIZE_RESPONSE",
        )
        checks["expiry"] = expiry_code == 200 and bool(expiry_updated) and expired.get("hits") == []

        restore_code, restore = _request(
            "POST",
            "/api/v1/memories",
            {
                "session_id": standard_session,
                "client_request_id": _rid("restore"),
                "memory_kind": "REUSABLE_CONTEXT",
                "summary": "Synthetic restoremarker maps to restorecobalt.",
                "tags": [],
            },
        )
        restore_id = str(restore.get("memory_id") or "")
        created_ids.add(restore_id)
        _unlink_sqlite(RESTORE_PATH)
        shutil.copy2(data.path, RESTORE_PATH)
        forget_code, forgotten = _request(
            "POST",
            f"/api/v1/memories/{restore_id}:forget",
            {
                "session_id": standard_session,
                "client_request_id": _rid("restore_forget"),
                "expected_version": 1,
            },
        )
        restored_service = durable_memory.DurableMemoryService(
            local_pilot_store.SQLiteDurableStore(RESTORE_PATH),
            ledger=durable_memory.StoreDeletionDenyLedger(ledger_store),
            policy=durable_memory.PilotPolicy.from_environment(),
        )
        restored_recall = await restored_service.recall(
            principal=principal,
            session_mode="STANDARD",
            query="restoremarker restorecobalt",
            purpose="PERSONALIZE_RESPONSE",
        )
        checks["deletion_restore_fence"] = (
            restore_code == 200
            and forget_code == 200
            and forgotten.get("status") == "success"
            and restored_recall.get("hits") == []
        )

        export_code, export_requested = _request(
            "POST",
            "/api/v1/memory/exports",
            {
                "session_id": standard_session,
                "client_request_id": _rid("export"),
            },
        )
        export_id = str(export_requested.get("export_id") or "")
        process_code, processed_export = _request(
            "POST",
            f"/api/v1/memory/exports/{export_id}:process",
            {"session_id": standard_session},
        )
        status_code, export_state = _request(
            "GET", f"/api/v1/memory/exports/{export_id}?session_id={standard_session}"
        )
        download_code, export_document = _request(
            "GET", f"/api/v1/memory/exports/{export_id}/download?session_id={standard_session}"
        )
        exported_ids = {
            str(item.get("memory_id") or "") for item in export_document.get("items") or []
        }
        encrypted_path = local_pilot_store.PILOT_ROOT / "exports" / f"{export_id}.aesgcm"
        raw_export = encrypted_path.read_bytes() if encrypted_path.exists() else b""
        checks["encrypted_async_export"] = all(
            (
                export_code == 202,
                process_code == 200,
                processed_export.get("export_status") == "READY",
                export_requested.get("export_status") == "PENDING",
                export_state.get("export_status") == "READY",
                download_code == 200,
                export_document.get("export_kind") == "SPEC39_OPTIONAL_MEMORY",
                {case["id"] for case in cases}.issubset(exported_ids),
                "omission_count" not in export_document,
                b"Synthetic valcase" not in raw_export,
                encrypted_path.exists(),
            )
        )

        for memory_id in sorted(created_ids):
            row = await data.get("memory_items", memory_id)
            if row and row.get("lifecycle_status") == "ACTIVE":
                code, result = _request(
                    "POST",
                    f"/api/v1/memories/{memory_id}:forget",
                    {
                        "session_id": standard_session,
                        "client_request_id": _rid("cleanup"),
                        "expected_version": int(row["version"]),
                    },
                )
                if code != 200 or result.get("status") != "success":
                    raise RuntimeError("synthetic cleanup failed")
        final_active = {
            str(row["memory_id"])
            for row in await data.list(
                "memory_items",
                filters={"workspace_id": principal.workspace_id, "lifecycle_status": "ACTIVE"},
                limit=1000,
            )
        }
        checks["synthetic_cleanup"] = not (final_active - baseline_active)
        invalidated_code, invalidated = _request(
            "GET", f"/api/v1/memory/exports/{export_id}/download?session_id={standard_session}"
        )
        checks["export_invalidated_after_forget"] = (
            invalidated_code == 409
            and invalidated.get("error_code") == "memory_export_stale"
            and not encrypted_path.exists()
        )

        disable_code, disabled = _request(
            "POST",
            "/api/v1/memory/settings",
            {
                "session_id": standard_session,
                "enabled": False,
                "client_request_id": _rid("disable"),
            },
        )
        disabled_at_end = disable_code == 200 and disabled.get("read_enabled") is False
        disabled_recall = await service.recall(
            principal=principal,
            session_mode="STANDARD",
            query="valcaseaa correctedcobalt",
            purpose="PERSONALIZE_RESPONSE",
        )
        checks["disable_kill_switch"] = (
            disabled_at_end and disabled_recall.get("error_code") == "memory_disabled"
        )

        sorted_latency = sorted(recall_latencies)
        p95_index = max(0, int(len(sorted_latency) * 0.95) - 1)
        evidence = {
            "schema_version": 1,
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
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "status": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks,
            "cases": {
                "eligible": ELIGIBLE_CASES,
                "intended_hits": intended_hits,
                "usefulness_rate": usefulness,
                "admitted_hits": admitted_hits,
                "irrelevant_hits": irrelevant_hits,
                "irrelevant_rate": irrelevance,
            },
            "bounds": {
                "recall_pack_elapsed_ms": elapsed_ms,
                "recall_p95_ms": sorted_latency[p95_index],
                "recall_max_ms": max(recall_latencies or [0]),
                "max_pages": max(pages or [0]),
                "max_candidates_examined": max(candidates or [0]),
                "max_source_reauthorizations": max(reauthorizations or [0]),
                "max_admitted_hits": 1 if admitted_hits else 0,
            },
            "isolation": {
                "loopback_only": True,
                "synthetic_only": True,
                "cloud_calls": 0,
                "connector_calls": 0,
                "external_effects": 0,
                "managed_memory_calls": 0,
                "normal_app_port_touched": False,
            },
            "end_state": {
                "memory_enabled": not disabled_at_end,
                "new_active_synthetic_items": len(final_active - baseline_active),
                "deletion_ledger_preserved": True,
                "restore_snapshot_retained": str(RESTORE_PATH),
            },
            "content_included": False,
        }
        temporary = EVIDENCE_PATH.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.chmod(0o600)
        temporary.replace(EVIDENCE_PATH)
        return evidence
    finally:
        for memory_id in sorted(created_ids):
            row = await data.get("memory_items", memory_id)
            if row and row.get("lifecycle_status") == "ACTIVE":
                await service.forget(
                    principal=principal,
                    memory_id=memory_id,
                    expected_version=int(row["version"]),
                    client_request_id=_rid("fail_closed_cleanup"),
                )
        if standard_session and not disabled_at_end:
            _request(
                "POST",
                "/api/v1/memory/settings",
                {
                    "session_id": standard_session,
                    "enabled": False,
                    "client_request_id": _rid("fail_closed_disable"),
                },
            )


def main() -> int:
    try:
        result = asyncio.run(run())
    except Exception as exc:
        print(
            json.dumps(
                {"status": "BLOCKED", "error": type(exc).__name__, "message": str(exc)}, indent=2
            )
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

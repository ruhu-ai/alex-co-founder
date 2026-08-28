"""Recoverable unfiltered mailbox batches and trusted role-route selection.

H0-H3 accepts only durable synthetic fixture messages. The same receipt/cursor
contracts are provider-ready, while real Gmail credential use remains blocked.
"""

from __future__ import annotations

from typing import Any

from services import hiring_activation
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import canonical_hash, stable_id, utc_now
from services.hiring_service import HiringService


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    del http_status
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


class HiringMailboxService:
    def __init__(self, hiring: HiringService, *, store: DurableStore | None = None):
        self.store = store or production_store()
        self.hiring = hiring

    async def configure_binding(self, *, role_id: str, connection_id: str,
                                provider_route_id: str, provider_label_id: str,
                                expected_role_version: int,
                                synthetic_guard: dict[str, Any]) -> dict[str, Any]:
        gate = hiring_activation.require_synthetic(synthetic_guard)
        if gate.get("error"):
            return gate
        role = await self.store.get("hiring_roles", role_id)
        if not role or role.get("synthetic") is not True:
            return _error("role_not_found", "Synthetic role does not exist.", 404)
        binding_id = stable_id("binding", role_id, connection_id, provider_route_id)
        binding = {
            "schema_version": 1, "binding_id": binding_id,
            "connection_id": connection_id, "provider_route_id": provider_route_id,
            "provider_label_id": provider_label_id, "binding_version": 1,
            "status": "PROVISIONAL", "positive_probe_receipt_id": None,
            "negative_probe_receipt_id": None,
            "watch_scope": "UNFILTERED_MAILBOX_HISTORY",
            "configured_at": utc_now(), "revoked_at": None,
        }
        current_binding = dict(role.get("mailbox_binding") or {})
        binding_matches = all(current_binding.get(key) == binding.get(key) for key in (
            "binding_id", "connection_id", "provider_route_id", "provider_label_id"))
        state = await self.store.get("hiring_mailbox_state", connection_id)
        if state and state.get("workspace_id") != role["workspace_id"]:
            return _error("connection_authority_conflict",
                          "Mailbox connection belongs to another workspace.", 403)
        if not state:
            await self.store.create("hiring_mailbox_state", connection_id, {
                "schema_version": 1, "connection_id": connection_id,
                "workspace_id": role["workspace_id"], "cursor": "0",
                "watch_scope": "UNFILTERED_MAILBOX_HISTORY",
                "watch_configuration_hash": canonical_hash({
                    "scope": "UNFILTERED_MAILBOX_HISTORY", "connection_id": connection_id}),
                "watch_expires_at": None, "last_complete_sync_at": None,
                "last_cursor_advanced_at": None, "health": "DEGRADED_PROBES_REQUIRED",
                "synthetic": True,
                "synthetic_namespace": synthetic_guard["synthetic_namespace"],
                "fixture_id": synthetic_guard["fixture_id"], "created_at": utc_now(),
                "updated_at": utc_now(), "version": 1,
            })
            state = await self.store.get("hiring_mailbox_state", connection_id)
            if not state or state.get("workspace_id") != role["workspace_id"]:
                return _error("connection_authority_conflict",
                              "Mailbox connection raced with another workspace.", 403)
        if binding_matches:
            return {"status": "success", "duplicate": True,
                    "binding": current_binding, "role_version": role["version"]}
        committed = await self.store.compare_and_set(
            "hiring_roles", role_id, expected_role_version,
            {"mailbox_binding": binding, "updated_at": utc_now()})
        if not committed:
            return _error("version_conflict", "Role changed; reload the binding.")
        return {"status": "success", "duplicate": False, "binding": binding,
                "role_version": committed["version"]}

    async def record_probe(self, *, role_id: str, fixture_message_id: str,
                           probe_kind: str, expected_role_version: int) -> dict[str, Any]:
        """Require both delivery and forged-header negative proof before trust."""
        role = await self.store.get("hiring_roles", role_id)
        message = await self.store.get("hiring_fixture_messages", fixture_message_id)
        binding = dict((role or {}).get("mailbox_binding") or {})
        if not role or not message or not binding:
            return _error("probe_not_found", "Binding probe fixture does not exist.", 404)
        if message.get("synthetic") is not True or role.get("synthetic") is not True:
            return _error("production_hiring_disabled", "Only synthetic probes are enabled.")
        receipt_id = stable_id("probe", binding["binding_id"], fixture_message_id,
                               probe_kind)
        if probe_kind == "POSITIVE":
            passed = (
                message.get("provider_route_id") == binding["provider_route_id"]
                and binding["provider_label_id"] in message.get("provider_label_ids", [])
                and message.get("delivery_authentic") is True)
            field = "positive_probe_receipt_id"
        elif probe_kind == "NEGATIVE_FORGED_HEADER":
            passed = (
                message.get("raw_recipient_header") == role["publication_package"]["application_address"]
                and message.get("provider_route_id") != binding["provider_route_id"]
                and binding["provider_label_id"] not in message.get("provider_label_ids", []))
            field = "negative_probe_receipt_id"
        else:
            return _error("invalid_contract", "Unknown probe kind.", 400)
        existing_receipt = await self.store.get(
            "hiring_mailbox_probe_receipts", receipt_id)
        if (existing_receipt and binding.get(field) == receipt_id
                and existing_receipt.get("passed") is True):
            return {"status": "success", "duplicate": True,
                    "probe_receipt_id": receipt_id, "passed": True,
                    "binding_status": binding["status"],
                    "role_version": role["version"]}
        await self.store.create("hiring_mailbox_probe_receipts", receipt_id, {
            "schema_version": 1, "probe_receipt_id": receipt_id,
            "workspace_id": role["workspace_id"], "role_id": role_id,
            "binding_id": binding["binding_id"], "probe_kind": probe_kind,
            "passed": passed, "safe_failure_code": None if passed else "PROBE_FAILED",
            "created_at": utc_now(), "synthetic": True,
            "synthetic_namespace": role["synthetic_namespace"],
            "fixture_id": role["fixture_id"], "version": 1,
        })
        if not passed:
            binding.update({"status": "REVOKED", "revoked_at": utc_now(),
                            "safe_degraded_reason": "PROBE_FAILED"})
        else:
            binding[field] = receipt_id
            if binding.get("positive_probe_receipt_id") and binding.get(
                    "negative_probe_receipt_id"):
                binding["status"] = "ACTIVE"
                binding["activated_at"] = utc_now()
        committed = await self.store.compare_and_set(
            "hiring_roles", role_id, expected_role_version,
            {"mailbox_binding": binding, "updated_at": utc_now()})
        if not committed:
            return _error("version_conflict", "Role changed; repeat the probe.")
        return {"status": "success", "probe_receipt_id": receipt_id,
                "passed": passed, "binding_status": binding["status"],
                "role_version": committed["version"]}

    async def seed_fixture_message(self, *, message: dict[str, Any],
                                   synthetic_guard: dict[str, Any]) -> dict[str, Any]:
        gate = hiring_activation.require_synthetic(synthetic_guard)
        if gate.get("error"):
            return gate
        required = {"message_id", "thread_id", "connection_id", "provider_internal_date",
                    "provider_label_ids", "provider_route_id", "delivery_authentic",
                    "raw_recipient_header", "identity", "blocks", "source_sha256"}
        if set(message) != required:
            return _error("invalid_fixture_contract", "Fixture message envelope is not closed.", 400)
        state = await self.store.get("hiring_mailbox_state", message["connection_id"])
        if not state or state.get("synthetic") is not True:
            return _error("fixture_connection_missing",
                          "Synthetic mailbox connection is not configured.", 404)
        row = {
            "schema_version": 1, **message, "synthetic": True,
            "workspace_id": state["workspace_id"],
            "synthetic_namespace": synthetic_guard["synthetic_namespace"],
            "fixture_id": synthetic_guard["fixture_id"],
            "created_at": utc_now(), "version": 1,
        }
        created = await self.store.create("hiring_fixture_messages", message["message_id"], row)
        existing = row if created else await self.store.get(
            "hiring_fixture_messages", message["message_id"])
        if not existing or canonical_hash({k: existing.get(k) for k in required}) != canonical_hash(message):
            return _error("idempotency_conflict", "Fixture id names another message.")
        return {"status": "success", "duplicate": not created,
                "message_id": message["message_id"]}

    async def create_fetch_batch(self, *, connection_id: str, old_cursor: str,
                                 proposed_cursor: str, message_ids: list[str],
                                 batch_key: str, mode: str = "HISTORY",
                                 fully_paginated: bool = True,
                                 synthetic_guard: dict[str, Any],
                                 crash_point: str = "") -> dict[str, Any]:
        gate = hiring_activation.require_synthetic(synthetic_guard)
        if gate.get("error"):
            return gate
        if mode not in {"HISTORY", "EXPIRED_CURSOR_RECOVERY"}:
            return _error("invalid_contract", "Invalid mailbox batch mode.", 400)
        if mode == "EXPIRED_CURSOR_RECOVERY" and not fully_paginated:
            return _error("recovery_incomplete", "Recovery must paginate to exhaustion.")
        state = await self.store.get("hiring_mailbox_state", connection_id)
        if not state or str(state.get("cursor")) != str(old_cursor):
            return _error("cursor_conflict", "Mailbox cursor changed; replay/reconcile.")
        batch_id = stable_id("mailbatch", connection_id, old_cursor,
                             proposed_cursor, batch_key)
        unique_message_ids = sorted(set(message_ids))
        message_manifest_hash = canonical_hash({"message_ids": unique_message_ids})
        now = utc_now()
        batch = {
            "schema_version": 1, "batch_id": batch_id,
            "workspace_id": state["workspace_id"], "connection_id": connection_id,
            "old_cursor": str(old_cursor), "proposed_cursor": str(proposed_cursor),
            "mode": mode, "status": "PREPARING_ENTRIES",
            "fully_paginated": fully_paginated,
            "entry_count": len(unique_message_ids), "terminal_count": 0,
            "message_manifest_hash": message_manifest_hash,
            "configuration_hash": state["watch_configuration_hash"],
            "cursor_commit_receipt_id": None, "safe_failure_code": None,
            "synthetic": True,
            "synthetic_namespace": synthetic_guard["synthetic_namespace"],
            "fixture_id": synthetic_guard["fixture_id"],
            "created_at": now, "updated_at": now, "version": 1,
        }
        # Reserve the manifest before entries. A conflicting retry is rejected
        # before it can create orphan entries; a crash leaves an inert batch
        # that the same exact request can heal.
        created = await self.store.create("mailbox_fetch_batches", batch_id, batch)
        current_batch = batch if created else await self.store.get(
            "mailbox_fetch_batches", batch_id)
        current_batch = current_batch or {}
        if (current_batch.get("message_manifest_hash") != message_manifest_hash
                or current_batch.get("fixture_id") != synthetic_guard["fixture_id"]
                or current_batch.get("connection_id") != connection_id):
            return _error("idempotency_conflict",
                          "Mailbox batch key names another manifest.")
        if crash_point == "AFTER_BATCH_RESERVATION":
            return _error("injected_crash",
                          "Synthetic crash injected before batch entries.", 503)
        for message_id in unique_message_ids:
            entry_id = stable_id("mailentry", batch_id, message_id)
            message = await self.store.get("hiring_fixture_messages", message_id)
            # Manifest entries intentionally carry no identity/body/subject/filename.
            await self.store.create("mailbox_fetch_batch_entries", entry_id, {
                "schema_version": 1, "entry_id": entry_id, "batch_id": batch_id,
                "workspace_id": state["workspace_id"],
                "connection_id": connection_id, "provider_message_id": message_id,
                "provider_internal_date": (message or {}).get("provider_internal_date"),
                "provider_label_ids": (message or {}).get("provider_label_ids", []),
                "disposition": "PENDING", "receipt_ref": None,
                "safe_failure_code": None, "created_at": now,
                "synthetic": True,
                "synthetic_namespace": synthetic_guard["synthetic_namespace"],
                "fixture_id": synthetic_guard["fixture_id"], "version": 1,
            })
        entries = await self.store.list(
            "mailbox_fetch_batch_entries", filters={"batch_id": batch_id}, limit=1000)
        if ({str(entry.get("provider_message_id")) for entry in entries}
                != set(unique_message_ids)):
            return _error("batch_incomplete",
                          "Mailbox batch manifest remains recoverable.", 503)
        latest = await self.store.get("mailbox_fetch_batches", batch_id)
        if latest and latest.get("status") == "PREPARING_ENTRIES":
            advanced = await self.store.compare_and_set(
                "mailbox_fetch_batches", batch_id, int(latest["version"]), {
                    "status": "RECEIPTING", "updated_at": utc_now()})
            if advanced:
                latest = advanced
        latest = latest or current_batch
        if latest.get("status") == "PREPARING_ENTRIES":
            return _error("batch_incomplete",
                          "Mailbox batch manifest remains recoverable.", 503)
        return {**latest, "batch_status": latest.get("status"),
                "status": "success", "duplicate": not created}

    async def process_batch(self, batch_id: str, *,
                            crash_after_terminal_entries: int | None = None,
                            crash_point: str = "") -> dict[str, Any]:
        batch = await self.store.get("mailbox_fetch_batches", batch_id)
        if not batch or batch.get("synthetic") is not True:
            return _error("batch_not_found", "Synthetic mailbox batch does not exist.", 404)
        if batch.get("status") == "PREPARING_ENTRIES":
            return _error("batch_incomplete",
                          "Mailbox manifest setup must be retried first.", 503)
        entries = await self.store.list(
            "mailbox_fetch_batch_entries", filters={"batch_id": batch_id}, limit=1000)
        processed_this_call = 0
        for entry in sorted(entries, key=lambda item: item["provider_message_id"]):
            if entry.get("disposition") != "PENDING":
                continue
            outcome = await self._process_entry(batch, entry)
            if outcome.get("error") and outcome.get("recoverable"):
                return outcome
            disposition = ("RECEIPTED" if outcome.get("status") == "success"
                           else "QUARANTINED")
            current = await self.store.get("mailbox_fetch_batch_entries", entry["entry_id"])
            if current:
                await self.store.compare_and_set(
                    "mailbox_fetch_batch_entries", entry["entry_id"],
                    int(current["version"]), {
                        "disposition": disposition,
                        "receipt_ref": outcome.get("event_id") or outcome.get("inbox_item_id"),
                        "safe_failure_code": outcome.get("error_code"),
                        "updated_at": utc_now(),
                    })
            processed_this_call += 1
            if (crash_after_terminal_entries is not None
                    and processed_this_call >= crash_after_terminal_entries):
                return _error("injected_crash", "Synthetic crash injected before cursor commit.", 503)
        final_entries = await self.store.list(
            "mailbox_fetch_batch_entries", filters={"batch_id": batch_id}, limit=1000)
        if len(final_entries) != int(batch["entry_count"]) or any(
                item.get("disposition") == "PENDING" for item in final_entries):
            return _error("batch_incomplete", "Mailbox batch remains recoverable.", 503)
        current_batch = await self.store.get("mailbox_fetch_batches", batch_id)
        if current_batch and current_batch.get("status") != "COMPLETE":
            committed_batch = await self.store.compare_and_set(
                "mailbox_fetch_batches", batch_id, int(current_batch["version"]), {
                    "status": "COMPLETE", "terminal_count": len(final_entries),
                    "completed_at": utc_now(), "updated_at": utc_now(),
                })
            if not committed_batch:
                return _error("concurrency_conflict", "Mailbox batch changed concurrently.")
        if crash_point == "AFTER_BATCH_COMPLETE":
            return _error("injected_crash",
                          "Synthetic crash injected after batch completion.", 503)
        state = await self.store.get("hiring_mailbox_state", batch["connection_id"])
        if not state:
            return _error("mailbox_state_missing", "Mailbox state disappeared.", 503)
        if str(state.get("cursor")) == str(batch["proposed_cursor"]):
            receipt_id = await self._ensure_cursor_receipt(batch)
            return {"status": "success", "duplicate": True, "batch_id": batch_id,
                    "cursor": batch["proposed_cursor"],
                    "cursor_commit_receipt_id": receipt_id}
        if str(state.get("cursor")) != str(batch["old_cursor"]):
            return _error("cursor_conflict", "Another batch advanced the cursor.")
        committed_state = await self.store.compare_and_set(
            "hiring_mailbox_state", batch["connection_id"], int(state["version"]), {
                "cursor": str(batch["proposed_cursor"]),
                "last_cursor_advanced_at": utc_now(),
                "last_complete_sync_at": (utc_now() if batch["mode"] == "EXPIRED_CURSOR_RECOVERY"
                                           else state.get("last_complete_sync_at")),
                "health": "HEALTHY", "updated_at": utc_now(),
            })
        if not committed_state:
            return _error("cursor_conflict", "Mailbox cursor changed concurrently.")
        if crash_point == "AFTER_CURSOR_CAS":
            return _error("injected_crash",
                          "Synthetic crash injected after cursor compare-and-set.", 503)
        cursor_receipt_id = await self._ensure_cursor_receipt(batch)
        return {"status": "success", "duplicate": False, "batch_id": batch_id,
                "cursor": batch["proposed_cursor"],
                "cursor_commit_receipt_id": cursor_receipt_id,
                "terminal_entries": len(final_entries)}

    async def _ensure_cursor_receipt(self, batch: dict[str, Any]) -> str:
        """Heal the post-CAS crash window without advancing the cursor again."""
        batch_id = batch["batch_id"]
        cursor_receipt_id = stable_id("cursorreceipt", batch_id,
                                      str(batch["proposed_cursor"]))
        complete = await self.store.get("mailbox_fetch_batches", batch_id)
        if complete:
            await self.store.compare_and_set(
                "mailbox_fetch_batches", batch_id, int(complete["version"]), {
                    "cursor_commit_receipt_id": cursor_receipt_id,
                    "updated_at": utc_now(),
                })
        await self.store.create("hiring_cursor_receipts", cursor_receipt_id, {
            "schema_version": 1, "cursor_commit_receipt_id": cursor_receipt_id,
            "batch_id": batch_id, "connection_id": batch["connection_id"],
            "workspace_id": batch["workspace_id"],
            "old_cursor": batch["old_cursor"], "new_cursor": batch["proposed_cursor"],
            "committed_at": utc_now(), "synthetic": True,
            "synthetic_namespace": batch["synthetic_namespace"],
            "fixture_id": batch["fixture_id"], "version": 1,
        })
        return cursor_receipt_id

    async def _process_entry(self, batch: dict[str, Any],
                             entry: dict[str, Any]) -> dict[str, Any]:
        message = await self.store.get(
            "hiring_fixture_messages", entry["provider_message_id"])
        if not message:
            return {**_error("message_fetch_failed", "Message remains recoverable.", 503),
                    "recoverable": True}
        roles = await self.store.list(
            "hiring_roles", filters={"workspace_id": batch["workspace_id"]}, limit=1000)
        matches = []
        for role in roles:
            binding = role.get("mailbox_binding") or {}
            if (binding.get("connection_id") == batch["connection_id"]
                    and binding.get("status") == "ACTIVE"
                    and message.get("delivery_authentic") is True
                    and message.get("provider_route_id") == binding.get("provider_route_id")
                    and binding.get("provider_label_id") in message.get("provider_label_ids", [])):
                matches.append(role)
        if len(matches) != 1:
            inbox_id = stable_id("hinbox", batch["batch_id"], entry["provider_message_id"])
            await self.store.create("founder_inbox", inbox_id, {
                "schema_version": 2, "inbox_item_id": inbox_id,
                "workspace_id": batch["workspace_id"],
                "founder_id": batch["workspace_id"],
                "kind": "HIRING_MAIL_QUARANTINE",
                "status": "OPEN", "provider_message_id": entry["provider_message_id"],
                "safe_reason": "NO_EXACT_TRUSTED_ROUTE" if not matches else "AMBIGUOUS_ROUTE",
                "created_at": utc_now(), "synthetic": True,
                "synthetic_namespace": batch["synthetic_namespace"],
                "fixture_id": batch["fixture_id"], "version": 1,
            })
            return {**_error("trusted_route_missing", "Message was quarantined."),
                    "inbox_item_id": inbox_id, "recoverable": False}
        role = matches[0]
        event_id = stable_id("hevent", batch["connection_id"], message["message_id"])
        event = {
            "schema_version": 2, "event_id": event_id,
            "workspace_id": batch["workspace_id"],
            "founder_id": batch["workspace_id"], "role_id": role["role_id"],
            "connection_id": batch["connection_id"], "connector_id": "alex_mail",
            "provider_event_id": message["message_id"],
            "provider_thread_id": message["thread_id"],
            "event_kind": "HIRING_APPLICATION_EMAIL", "correlation_status": "EXACT",
            "correlation_basis": "TRUSTED_ROUTE_AND_PROVIDER_LABEL",
            "processing_status": "RECEIVED", "payload_hash": message["source_sha256"],
            "received_at": utc_now(), "synthetic": True,
            "synthetic_namespace": batch["synthetic_namespace"],
            "fixture_id": batch["fixture_id"], "version": 1,
        }
        await self.store.create("external_events", event_id, event)
        result = await self.hiring.ingest_synthetic_application(
            role_id=role["role_id"], provider_message_id=message["message_id"],
            provider_thread_id=message["thread_id"], identity_fields=message["identity"],
            blocks=message["blocks"], source_sha256=message["source_sha256"],
            external_event_id=event_id, synthetic_guard={
                "synthetic": True,
                "synthetic_namespace": batch["synthetic_namespace"],
                "fixture_id": batch["fixture_id"],
            })
        if result.get("error"):
            from services.error_contracts import http_status

            recoverable = http_status(result) >= 500
            if recoverable:
                return {**result, "recoverable": True}
            inbox_id = stable_id("hinbox", batch["batch_id"],
                                 entry["provider_message_id"], "pipeline")
            await self.store.create("founder_inbox", inbox_id, {
                "schema_version": 2, "inbox_item_id": inbox_id,
                "workspace_id": batch["workspace_id"],
                "founder_id": batch["workspace_id"], "role_id": role["role_id"],
                "candidate_application_id": result.get("candidate_application_id"),
                "kind": "HIRING_APPLICATION_PIPELINE_QUARANTINE", "status": "OPEN",
                "safe_reason": str(result.get("error_code") or "PIPELINE_REJECTED")[:120],
                "provider_message_id": entry["provider_message_id"],
                "created_at": utc_now(), "synthetic": True,
                "synthetic_namespace": batch["synthetic_namespace"],
                "fixture_id": batch["fixture_id"], "version": 1,
            })
            return {**result, "inbox_item_id": inbox_id, "recoverable": False}
        current = await self.store.get("external_events", event_id)
        if current:
            await self.store.compare_and_set(
                "external_events", event_id, int(current["version"]), {
                    "processing_status": "APPLIED",
                    "candidate_application_id": result["candidate_application_id"],
                    "processed_at": utc_now(),
                })
        return {"status": "success", "event_id": event_id,
                "candidate_application_id": result["candidate_application_id"]}

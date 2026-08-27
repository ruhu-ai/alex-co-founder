"""Verified H4S Gmail reply correlation and durable CandidateRun wake.

This service accepts only metadata produced by the authenticated sandbox Gmail
worker. It never reads a mailbox, trusts sender text, or stores a reply body.
Both causal headers from the outbound receipt and the run-owned test sender
allowlist are required before a dormant CandidateRun can be resumed.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

from services.durable_store import DurableStore, production_store
from services.hiring_contracts import stable_id, utc_now
from services.hiring_sandbox import HiringSandboxService
from services.workflow_runtime import WorkflowRuntime

_OPAQUE = re.compile(r"^[A-Za-z0-9._:@+-]{3,512}$")


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "http_status": http_status}


def _message_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


class H4SReplyService:
    """Correlate one worker-verified inbound test reply without polling."""

    def __init__(self, *, sandbox: HiringSandboxService,
                 store: DurableStore | None = None):
        self.store = store or production_store()
        self.sandbox = sandbox
        self.runtime = WorkflowRuntime(self.store)

    async def ingest_verified_reply(
            self, *, sandbox_run_id: str, binding_id: str,
            provider_message_id: str, provider_thread_id: str,
            in_reply_to_message_id: str, causal_token: str,
            sender_destination_id: str, message_kind: str,
            auto_submitted: bool = False) -> dict[str, Any]:
        """Record and wake exactly one causal reply from a sandbox worker.

        ``provider_message_id``, thread, reply-to id and token come from the
        provider's parsed MIME metadata after the worker has authenticated its
        route. Raw headers, subjects and body content deliberately do not enter
        this contract or durable record.
        """
        if (message_kind != "INBOX" or auto_submitted
                or not all(_OPAQUE.fullmatch(value or "") for value in (
                    provider_message_id, provider_thread_id, causal_token))
                or not in_reply_to_message_id.startswith("<")
                or not in_reply_to_message_id.endswith(">")
                or len(in_reply_to_message_id) > 512
                or "\n" in in_reply_to_message_id or "\r" in in_reply_to_message_id):
            return await self._quarantine(
                sandbox_run_id=sandbox_run_id, provider_message_id=provider_message_id,
                reason="UNSAFE_OR_NON_INBOX_REPLY")
        binding = await self.sandbox.resolve_connector_binding(
            sandbox_run_id=sandbox_run_id, binding_id=binding_id,
            provider_kind="GMAIL_TEST")
        if binding.get("error"):
            return binding
        sender = await self.sandbox.resolve_destination(
            sandbox_run_id=sandbox_run_id, destination_id=sender_destination_id,
            allowed_kinds={"TEST_CANDIDATE"})
        if sender.get("error"):
            return await self._quarantine(
                sandbox_run_id=sandbox_run_id, provider_message_id=provider_message_id,
                reason="SENDER_NOT_ALLOWLISTED")
        sandbox_row = await self.store.get("hiring_sandbox_runs", sandbox_run_id)
        if not sandbox_row:
            return _error("sandbox_not_found", "Sandbox does not exist.", 404)
        actions = await self.store.list(
            "external_actions", filters={"workspace_id": sandbox_row["workspace_id"]}, limit=1000)
        candidates = [
            row for row in actions
            if row.get("action_kind") == "H4S_SEND_EMAIL"
            and row.get("status") == "SUCCEEDED"
            and (row.get("sandbox_context") or {}).get("sandbox_run_id") == sandbox_run_id
            and (row.get("sandbox_context") or {}).get("connector_binding_id") == binding_id
            and sender_destination_id in ((row.get("sandbox_context") or {}).get("destination_ids") or [])
            and (row.get("result_ref") or {}).get("thread_id") == provider_thread_id
            and hmac.compare_digest(
                str((row.get("result_ref") or {}).get("causal_message_id_hash") or ""),
                _message_hash(in_reply_to_message_id))
            and hmac.compare_digest(causal_token, stable_id("h4scausal", row["action_id"]))
        ]
        if len(candidates) != 1:
            return await self._quarantine(
                sandbox_run_id=sandbox_run_id, provider_message_id=provider_message_id,
                reason="AMBIGUOUS_OR_FORGED_CAUSAL_REPLY")
        action = candidates[0]
        exact = action.get("exact_action") or {}
        run_id = str(exact.get("candidate_run_id") or "")
        correlation_id = stable_id("h4src", sandbox_run_id, binding_id, provider_message_id)
        existing = await self.store.get("hiring_reply_correlations", correlation_id)
        if existing and existing.get("status") == "CORRELATED":
            return {"status": "success", "duplicate": True,
                    "correlation_id": correlation_id,
                    "candidate_run_id": existing.get("candidate_run_id")}
        if existing and existing.get("status") == "QUARANTINED":
            return _error("reply_quarantined", "This reply was quarantined.")
        base = {
            "schema_version": 1, "correlation_id": correlation_id,
            "workspace_id": sandbox_row["workspace_id"], "sandbox_run_id": sandbox_run_id,
            "binding_id": binding_id, "action_id": action["action_id"],
            "candidate_run_id": run_id, "sender_destination_id": sender_destination_id,
            "provider_message_id": provider_message_id,
            "provider_thread_id": provider_thread_id,
            "in_reply_to_hash": _message_hash(in_reply_to_message_id),
            "causal_token_hash": _message_hash(causal_token),
            "status": "RECEIVED", "safe_reason": None,
            "synthetic": True, "fixture_id": sandbox_row["fixture_id"],
            "synthetic_namespace": sandbox_row["synthetic_namespace"],
            "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
        }
        if not existing:
            await self.store.create("hiring_reply_correlations", correlation_id, base)
        event = await self.runtime.append_event(
            run_id, event_kind="H4S_TEST_REPLY_RECEIVED",
            idempotency_key=f"h4s-reply:{correlation_id}",
            safe_payload={"correlation_id": correlation_id,
                          "action_id": action["action_id"]})
        if event.get("error"):
            return event
        wait = await self.runtime.create_wait(
            run_id, wait_kind="H4S_TEST_REPLY",
            correlation_key=f"h4s-email:{action['action_id']}")
        if wait.get("error"):
            return wait
        resolved = await self.runtime.resolve_wait(
            wait["wait_id"], event_id=event["event_id"],
            expected_generation=int(wait.get("generation", 1)))
        if resolved.get("error") and resolved.get("error_code") != "wait_already_resolved":
            return resolved
        current = await self.store.get("hiring_reply_correlations", correlation_id)
        if current and current.get("status") != "CORRELATED":
            await self.store.compare_and_set(
                "hiring_reply_correlations", correlation_id, int(current["version"]), {
                    "status": "CORRELATED", "event_id": event["event_id"],
                    "wait_id": wait["wait_id"], "resolved_at": utc_now(),
                    "updated_at": utc_now()})
        return {"status": "success", "duplicate": False,
                "correlation_id": correlation_id, "candidate_run_id": run_id,
                "state_delta": {"hiring_reply_correlation_id": correlation_id}}

    async def _quarantine(self, *, sandbox_run_id: str, provider_message_id: str,
                          reason: str) -> dict[str, Any]:
        """Durably retain only safe metadata; never wake a run on refusal."""
        sandbox = await self.store.get("hiring_sandbox_runs", sandbox_run_id)
        if not sandbox:
            return _error("sandbox_not_found", "Sandbox does not exist.", 404)
        correlation_id = stable_id("h4src", sandbox_run_id, "quarantine", provider_message_id)
        inbox_id = stable_id("hinbox", correlation_id)
        await self.store.create("hiring_reply_correlations", correlation_id, {
            "schema_version": 1, "correlation_id": correlation_id,
            "workspace_id": sandbox["workspace_id"], "sandbox_run_id": sandbox_run_id,
            "provider_message_id": provider_message_id[:512], "status": "QUARANTINED",
            "safe_reason": reason, "synthetic": True,
            "fixture_id": sandbox["fixture_id"],
            "synthetic_namespace": sandbox["synthetic_namespace"],
            "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
        })
        await self.store.create("founder_inbox", inbox_id, {
            "schema_version": 2, "inbox_item_id": inbox_id,
            "workspace_id": sandbox["workspace_id"], "founder_id": sandbox["workspace_id"],
            "kind": "HIRING_H4S_REPLY_QUARANTINE", "status": "OPEN",
            "safe_reason": reason, "provider_message_id": provider_message_id[:512],
            "synthetic": True, "fixture_id": sandbox["fixture_id"],
            "synthetic_namespace": sandbox["synthetic_namespace"],
            "created_at": utc_now(), "version": 1,
        })
        return _error("reply_quarantined", "Reply did not satisfy sandbox correlation guards.")

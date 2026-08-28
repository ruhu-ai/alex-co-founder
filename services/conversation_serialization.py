"""Fail-closed Gate-B conversation serialization foundation.

Firestore stores actor-private delivery intents.  The authoritative sequence
and append fence belong beside ADK events in Cloud SQL, so production defaults
to an unavailable sequence port until that separately proved adapter exists.
Nothing in this module invokes ADK or appends a transcript event.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from importlib import metadata
from typing import Any, Callable, Protocol

from services.actor_identity import ActorPrincipal
from services.canonical import canonical_hash
from services.durable_store import DurableStore, production_store
from services.workflow_contracts import run_visible_to_actor, stable_id, utc_now


def _error(code: str, message: str, *, fallback: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "error", "error": True,
        "error_code": code, "message": message,
    }
    if fallback:
        result["fallback"] = fallback
    return result


REQUIRED_WRITE_PATHS = frozenset({
    "app.direct_append",
    "live.canonical_transcript",
    "runner.compaction",
    "runner.control_and_tool_events",
    "runner.live_persistence",
    "runner.partial_streaming",
    "runner.rewind",
    "runner.state_only_delta",
    "runner.user_event",
    "session.app_state_delta",
    "session.session_state_delta",
    "session.user_state_delta",
    "session_linked.artifact_write",
})


@dataclass(frozen=True)
class ADKRuntimeFingerprint:
    package_version: str
    runner_modes: tuple[str, ...]
    session_service: str
    session_configuration_hash: str
    plugin_configuration_hash: str
    persistence_adapters: tuple[str, ...]


@dataclass(frozen=True)
class ADKWriteSurfaceManifest:
    schema_version: int
    fingerprint: ADKRuntimeFingerprint
    covered_paths: tuple[str, ...]
    proof_status: str
    fence_implementation_hash: str
    test_evidence_hash: str

    @property
    def proof_hash(self) -> str:
        return canonical_hash(
            asdict(self), domain="adk-write-surface-proof")


def current_foundation_fingerprint() -> ADKRuntimeFingerprint:
    """Return the reviewed build tuple without claiming interception proof."""
    try:
        version = metadata.version("google-adk")
    except metadata.PackageNotFoundError:
        version = "missing"
    return ADKRuntimeFingerprint(
        package_version=version,
        runner_modes=("direct_app_append", "run_async", "run_live"),
        session_service="google.adk.DatabaseSessionService",
        session_configuration_hash="UNBOUND",
        plugin_configuration_hash="UNBOUND",
        persistence_adapters=("cloud_sql_adk_events", "gcs_artifacts"),
    )


UNPROVED_WRITE_SURFACE = ADKWriteSurfaceManifest(
    schema_version=1,
    fingerprint=current_foundation_fingerprint(),
    covered_paths=tuple(sorted(REQUIRED_WRITE_PATHS)),
    proof_status="UNPROVED",
    fence_implementation_hash="",
    test_evidence_hash="",
)


def attest_write_surface(*, fingerprint: ADKRuntimeFingerprint,
                         manifest: ADKWriteSurfaceManifest,
                         expected_proof_hash: str) -> dict[str, Any]:
    """Require an exact release proof; version/config drift fails closed."""
    if (not expected_proof_hash
            or manifest.proof_hash != expected_proof_hash
            or manifest.proof_status != "PROVED"
            or manifest.schema_version != 1
            or manifest.fingerprint != fingerprint
            or set(manifest.covered_paths) != REQUIRED_WRITE_PATHS
            or not manifest.fence_implementation_hash
            or not manifest.test_evidence_hash):
        return _error(
            "adk_write_surface_unproved",
            "Proactive conversation delivery has no current append-fence proof.")
    return {"status": "success", "proof_hash": manifest.proof_hash}


class ConversationSequencePort(Protocol):
    """Cloud-SQL-owned sequence/turn lease contract."""

    async def reserve(self, *, session_id: str) -> dict[str, Any]: ...
    async def claim(self, *, session_id: str, turn_id: str,
                    lease_owner: str, lease_seconds: int) -> dict[str, Any]: ...
    async def renew(self, *, session_id: str, turn_id: str,
                    lease_owner: str, generation: int,
                    lease_seconds: int) -> dict[str, Any]: ...
    async def release(self, *, session_id: str, turn_id: str,
                      lease_owner: str, generation: int) -> dict[str, Any]: ...


class UnavailableConversationSequencePort:
    """Production default until the Cloud SQL append transaction is proved."""

    @staticmethod
    def _unavailable() -> dict[str, Any]:
        return _error(
            "session_serializer_unavailable",
            "Conversation serialization is not enabled for this session.",
            fallback="INBOX_RUN_CARD")

    async def reserve(self, *, session_id: str) -> dict[str, Any]:
        del session_id
        return self._unavailable()

    async def claim(self, **_: Any) -> dict[str, Any]:
        return self._unavailable()

    async def renew(self, **_: Any) -> dict[str, Any]:
        return self._unavailable()

    async def release(self, **_: Any) -> dict[str, Any]:
        return self._unavailable()


class InMemoryConversationSequencePort:
    """Deterministic race-test support; never a production fallback."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._rows: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def set_mode(self, session_id: str, mode: str) -> None:
        if mode not in {"LEGACY", "ENABLING", "ENABLED"}:
            raise ValueError("invalid serializer mode")
        async with self._lock:
            row = self._rows.setdefault(session_id, {
                "mode": "LEGACY", "next_sequence": 1,
                "turn_lease_generation": 0, "active_turn_id": None,
                "lease_owner": None, "lease_expires_at": None,
            })
            row["mode"] = mode

    async def reserve(self, *, session_id: str) -> dict[str, Any]:
        async with self._lock:
            row = self._rows.setdefault(session_id, {
                "mode": "LEGACY", "next_sequence": 1,
                "turn_lease_generation": 0, "active_turn_id": None,
                "lease_owner": None, "lease_expires_at": None,
            })
            if row["mode"] != "ENABLED":
                return _error(
                    "session_serializer_not_enabled",
                    "Target session cannot receive proactive delivery.",
                    fallback="INBOX_RUN_CARD")
            sequence = int(row["next_sequence"])
            row["next_sequence"] = sequence + 1
            return {"status": "success", "sequence": sequence}

    async def claim(self, *, session_id: str, turn_id: str,
                    lease_owner: str, lease_seconds: int) -> dict[str, Any]:
        if not turn_id or not lease_owner or not 5 <= lease_seconds <= 120:
            return _error("turn_lease_invalid", "Turn lease is invalid.")
        async with self._lock:
            row = self._rows.get(session_id)
            if not row or row["mode"] != "ENABLED":
                return _error(
                    "session_serializer_not_enabled",
                    "Target session cannot receive proactive delivery.",
                    fallback="INBOX_RUN_CARD")
            now = self._clock()
            expiry = row.get("lease_expires_at")
            if (row.get("active_turn_id") and isinstance(expiry, datetime)
                    and expiry > now):
                return _error("turn_lease_conflict", "Another turn owns the session.")
            generation = int(row["turn_lease_generation"]) + 1
            row.update({
                "turn_lease_generation": generation,
                "active_turn_id": turn_id,
                "lease_owner": lease_owner,
                "lease_expires_at": now + timedelta(seconds=lease_seconds),
            })
            return {"status": "success", "turn_id": turn_id,
                    "turn_lease_generation": generation,
                    "turn_lease_expires_at": row["lease_expires_at"].isoformat()}

    async def renew(self, *, session_id: str, turn_id: str,
                    lease_owner: str, generation: int,
                    lease_seconds: int) -> dict[str, Any]:
        async with self._lock:
            row = self._rows.get(session_id)
            if (not row or row.get("active_turn_id") != turn_id
                    or row.get("lease_owner") != lease_owner
                    or int(row.get("turn_lease_generation") or 0) != generation
                    or not isinstance(row.get("lease_expires_at"), datetime)
                    or row["lease_expires_at"] <= self._clock()
                    or not 5 <= lease_seconds <= 120):
                return _error("turn_lease_lost", "Turn lease is no longer current.")
            row["lease_expires_at"] = self._clock() + timedelta(
                seconds=lease_seconds)
            return {"status": "success", "turn_lease_generation": generation,
                    "turn_lease_expires_at": row["lease_expires_at"].isoformat()}

    async def release(self, *, session_id: str, turn_id: str,
                      lease_owner: str, generation: int) -> dict[str, Any]:
        async with self._lock:
            row = self._rows.get(session_id)
            if (not row or row.get("active_turn_id") != turn_id
                    or row.get("lease_owner") != lease_owner
                    or int(row.get("turn_lease_generation") or 0) != generation):
                return _error("turn_lease_lost", "Turn lease is no longer current.")
            row.update({"active_turn_id": None, "lease_owner": None,
                        "lease_expires_at": None})
            return {"status": "success", "turn_lease_generation": generation}


_DELIVERY_KINDS = frozenset({
    "APPROVAL", "FAILURE", "MILESTONE", "TERMINAL", "UNCERTAINTY",
})
_KIND_CAPTIONS = {
    "APPROVAL": frozenset({"BACKGROUND_APPROVAL_REQUIRED"}),
    "FAILURE": frozenset({
        "BACKGROUND_RECOVERABLE_FAILURE", "BACKGROUND_TERMINAL_FAILURE"}),
    "MILESTONE": frozenset({"BACKGROUND_MILESTONE"}),
    "TERMINAL": frozenset({"BACKGROUND_CANCELLED", "BACKGROUND_COMPLETE"}),
    "UNCERTAINTY": frozenset({"BACKGROUND_UNCERTAIN"}),
}


class ConversationDeliveryService:
    """Actor-private delivery ledger; append completion needs a fenced receipt."""

    def __init__(self, store: DurableStore | None = None, *,
                 sequence_port: ConversationSequencePort | None = None,
                 fingerprint: ADKRuntimeFingerprint | None = None,
                 manifest: ADKWriteSurfaceManifest = UNPROVED_WRITE_SURFACE,
                 expected_proof_hash: str = "",
                 clock: Callable[[], datetime] | None = None,
                 delivery_enabled: bool | None = None):
        self.store = store or production_store()
        self.sequence_port = sequence_port or UnavailableConversationSequencePort()
        self.fingerprint = fingerprint or current_foundation_fingerprint()
        self.manifest = manifest
        self.expected_proof_hash = expected_proof_hash
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.delivery_enabled = (
            os.environ.get(
                "BACKGROUND_CONVERSATION_DELIVERY_ENABLED", "false").lower()
            in {"1", "true", "yes", "on"}
            if delivery_enabled is None else bool(delivery_enabled))

    async def reserve(self, *, principal: ActorPrincipal, run_id: str,
                      session_id: str, causal_event_id: str, kind: str,
                      safe_caption_code: str, payload_ref: str = "") -> dict[str, Any]:
        run = await self.store.get("workflow_runs", run_id)
        if not self.delivery_enabled:
            return _error(
                "background_conversation_delivery_disabled",
                "Proactive conversation delivery is disabled.",
                fallback="INBOX_RUN_CARD")
        if (not run or run.get("execution_mode") != "BACKGROUND"
                or not run_visible_to_actor(
                    run, workspace_id=principal.workspace_id,
                    actor_id=principal.actor_id)
                or session_id not in {
                    run.get("origin_session_id"), run.get("delivery_session_id")}
                or kind not in _DELIVERY_KINDS
                or safe_caption_code not in _KIND_CAPTIONS.get(kind, frozenset())
                or not causal_event_id or len(causal_event_id) > 160
                or len(payload_ref) > 240):
            return _error(
                "conversation_delivery_invalid",
                "Conversation delivery is not authorized.",
                fallback="INBOX_RUN_CARD")
        delivery_id = stable_id(
            "delivery", principal.workspace_id, principal.actor_id,
            session_id, causal_event_id, kind)
        existing = await self.store.get("conversation_deliveries", delivery_id)
        if existing:
            identity = {
                "workspace_id": principal.workspace_id,
                "origin_actor_id": principal.actor_id,
                "session_id": session_id,
                "kind": kind,
                "run_id": run_id,
                "causal_event_id": causal_event_id,
                "payload_ref": payload_ref or None,
                "safe_caption_code": safe_caption_code,
            }
            if any(existing.get(key) != value for key, value in identity.items()):
                return _error(
                    "conversation_delivery_conflict",
                    "Delivery identity names different content.")
            return {"status": "success", "duplicate": True,
                    "delivery": existing}
        reserved = await self.sequence_port.reserve(session_id=session_id)
        if reserved.get("error"):
            return reserved
        row = {
            "schema_version": 1,
            "delivery_id": delivery_id,
            "workspace_id": principal.workspace_id,
            "origin_actor_id": principal.actor_id,
            "subject_kind": "ACTOR",
            "subject_id": principal.actor_id,
            "visibility_scope": "ACTOR_PRIVATE",
            "session_id": session_id,
            "sequence": int(reserved["sequence"]),
            "kind": kind,
            "run_id": run_id,
            "causal_event_id": causal_event_id,
            "payload_ref": payload_ref or None,
            "safe_caption_code": safe_caption_code,
            "status": "PENDING",
            "lease_owner": None,
            "lease_generation": 0,
            "turn_lease_generation": None,
            "lease_expires_at": None,
            "attempts": 0,
            "max_attempts": 3,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "delivered_at": None,
            "version": 1,
        }
        created = await self.store.create(
            "conversation_deliveries", delivery_id, row)
        existing = row if created else await self.store.get(
            "conversation_deliveries", delivery_id)
        identity = (
            "workspace_id", "origin_actor_id", "session_id", "kind", "run_id",
            "causal_event_id", "payload_ref", "safe_caption_code")
        if not existing or any(existing.get(key) != row.get(key) for key in identity):
            return _error(
                "conversation_delivery_conflict",
                "Delivery identity names different content.")
        return {"status": "success", "duplicate": not created,
                "delivery": existing}

    async def claim(self, *, workspace_id: str, actor_id: str,
                    delivery_id: str, lease_owner: str,
                    lease_seconds: int = 30) -> dict[str, Any]:
        if not self.delivery_enabled:
            return _error(
                "background_conversation_delivery_disabled",
                "Proactive conversation delivery is disabled.",
                fallback="INBOX_RUN_CARD")
        proof = attest_write_surface(
            fingerprint=self.fingerprint, manifest=self.manifest,
            expected_proof_hash=self.expected_proof_hash)
        if proof.get("error"):
            return proof
        delivery = await self.store.get("conversation_deliveries", delivery_id)
        if (not delivery or delivery.get("workspace_id") != workspace_id
                or delivery.get("subject_id") != actor_id
                or delivery.get("visibility_scope") != "ACTOR_PRIVATE"):
            return _error("conversation_delivery_not_found", "Delivery does not exist.")
        if delivery.get("status") == "DELIVERED":
            return {"status": "success", "duplicate": True,
                    "delivery": delivery}
        expiry = _parse_time(delivery.get("lease_expires_at"))
        if (delivery.get("status") == "LEASED" and expiry
                and expiry > self._clock()):
            return _error("delivery_lease_conflict", "Delivery is already leased.")
        if delivery.get("status") not in {"PENDING", "LEASED"}:
            return _error(
                "conversation_delivery_terminal",
                "Delivery is not eligible for another attempt.")
        if int(delivery.get("attempts") or 0) >= int(
                delivery.get("max_attempts") or 3):
            committed = await self.store.compare_and_set(
                "conversation_deliveries", delivery_id,
                int(delivery["version"]), {
                    "status": "DEAD_LETTER", "lease_owner": None,
                    "lease_expires_at": None, "updated_at": utc_now(),
                })
            if not committed:
                return _error(
                    "concurrency_conflict", "Delivery changed concurrently.")
            if delivery.get("status") == "LEASED":
                await self.sequence_port.release(
                    session_id=delivery["session_id"], turn_id=delivery_id,
                    lease_owner=str(delivery.get("lease_owner") or ""),
                    generation=int(delivery.get("turn_lease_generation") or 0))
            return _error(
                "delivery_attempts_exhausted",
                "Delivery retry budget is exhausted.")
        turn = await self.sequence_port.claim(
            session_id=delivery["session_id"], turn_id=delivery_id,
            lease_owner=lease_owner, lease_seconds=lease_seconds)
        if turn.get("error"):
            return turn
        generation = int(delivery.get("lease_generation") or 0) + 1
        committed = await self.store.compare_and_set(
            "conversation_deliveries", delivery_id, int(delivery["version"]), {
                "status": "LEASED", "lease_owner": lease_owner,
                "lease_generation": generation,
                "turn_lease_generation": int(turn["turn_lease_generation"]),
                "lease_expires_at": turn["turn_lease_expires_at"],
                "attempts": int(delivery.get("attempts") or 0) + 1,
                "updated_at": utc_now(),
            })
        if not committed:
            await self.sequence_port.release(
                session_id=delivery["session_id"], turn_id=delivery_id,
                lease_owner=lease_owner,
                generation=int(turn["turn_lease_generation"]))
            return _error("concurrency_conflict", "Delivery changed concurrently.")
        return {"status": "success", "duplicate": False,
                "delivery": committed, "proof_hash": proof["proof_hash"]}

    async def renew(self, *, workspace_id: str, actor_id: str,
                    delivery_id: str, lease_owner: str, generation: int,
                    lease_seconds: int = 30) -> dict[str, Any]:
        delivery = await self.store.get("conversation_deliveries", delivery_id)
        expiry = _parse_time((delivery or {}).get("lease_expires_at"))
        if (not delivery or delivery.get("workspace_id") != workspace_id
                or delivery.get("subject_id") != actor_id
                or delivery.get("status") != "LEASED"
                or delivery.get("lease_owner") != lease_owner
                or int(delivery.get("lease_generation") or 0) != generation
                or not expiry or expiry <= self._clock()):
            return _error("delivery_lease_lost", "Delivery lease is no longer current.")
        renewed = await self.sequence_port.renew(
            session_id=delivery["session_id"], turn_id=delivery_id,
            lease_owner=lease_owner,
            generation=int(delivery["turn_lease_generation"]),
            lease_seconds=lease_seconds)
        if renewed.get("error"):
            return renewed
        committed = await self.store.compare_and_set(
            "conversation_deliveries", delivery_id, int(delivery["version"]), {
                "lease_expires_at": renewed["turn_lease_expires_at"],
                "updated_at": utc_now(),
            })
        return ({"status": "success", "delivery": committed} if committed else
                _error("concurrency_conflict", "Delivery changed during renewal."))

    async def complete(self, *, workspace_id: str, actor_id: str,
                       delivery_id: str, lease_owner: str, generation: int,
                       append_receipt: dict[str, Any]) -> dict[str, Any]:
        delivery = await self.store.get("conversation_deliveries", delivery_id)
        expiry = _parse_time((delivery or {}).get("lease_expires_at"))
        if (not delivery or delivery.get("workspace_id") != workspace_id
                or delivery.get("subject_id") != actor_id):
            return _error("conversation_delivery_not_found", "Delivery does not exist.")
        if delivery.get("status") == "DELIVERED":
            return {"status": "success", "duplicate": True,
                    "delivery": delivery}
        if (delivery.get("status") != "LEASED"
                or delivery.get("lease_owner") != lease_owner
                or int(delivery.get("lease_generation") or 0) != generation
                or not expiry or expiry <= self._clock()
                or append_receipt.get("delivery_id") != delivery_id
                or int(append_receipt.get("event_ordinal") or 0) < 1
                or int(append_receipt.get("turn_lease_generation") or 0)
                != int(delivery.get("turn_lease_generation") or 0)
                or append_receipt.get("generation_fenced") is not True
                or not append_receipt.get("event_id")):
            return _error(
                "append_receipt_invalid",
                "A current generation-fenced append receipt is required.")
        now = utc_now()
        committed = await self.store.compare_and_set(
            "conversation_deliveries", delivery_id, int(delivery["version"]), {
                "status": "DELIVERED", "delivered_at": now,
                "event_id": str(append_receipt["event_id"])[:160],
                "event_ordinal": int(append_receipt["event_ordinal"]),
                "lease_owner": None, "lease_expires_at": None,
                "updated_at": now,
            })
        if not committed:
            return _error("concurrency_conflict", "Delivery changed concurrently.")
        released = await self.sequence_port.release(
            session_id=delivery["session_id"], turn_id=delivery_id,
            lease_owner=lease_owner,
            generation=int(delivery["turn_lease_generation"]))
        if released.get("error"):
            return _error(
                "turn_lease_release_failed",
                "Delivery committed but its turn lease needs reconciliation.")
        return {"status": "success", "duplicate": False,
                "delivery": committed}

    async def fail(self, *, workspace_id: str, actor_id: str,
                   delivery_id: str, lease_owner: str, generation: int,
                   error_code: str) -> dict[str, Any]:
        delivery = await self.store.get("conversation_deliveries", delivery_id)
        expiry = _parse_time((delivery or {}).get("lease_expires_at"))
        if (not delivery or delivery.get("workspace_id") != workspace_id
                or delivery.get("subject_id") != actor_id
                or delivery.get("status") != "LEASED"
                or delivery.get("lease_owner") != lease_owner
                or int(delivery.get("lease_generation") or 0) != generation
                or not expiry or expiry <= self._clock()):
            return _error("delivery_lease_lost", "Delivery lease is no longer current.")
        terminal = int(delivery.get("attempts") or 0) >= int(
            delivery.get("max_attempts") or 3)
        committed = await self.store.compare_and_set(
            "conversation_deliveries", delivery_id, int(delivery["version"]), {
                "status": "DEAD_LETTER" if terminal else "PENDING",
                "last_safe_error": str(error_code or "delivery_failed")[:120],
                "lease_owner": None, "lease_expires_at": None,
                "updated_at": utc_now(),
            })
        if not committed:
            return _error("concurrency_conflict", "Delivery changed concurrently.")
        await self.sequence_port.release(
            session_id=delivery["session_id"], turn_id=delivery_id,
            lease_owner=lease_owner,
            generation=int(delivery["turn_lease_generation"]))
        return {"status": "success", "delivery": committed,
                "retryable": not terminal}


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

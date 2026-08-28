"""Versioned workspace facts and actor-private preferences (docs/34 Phase 4A)."""

from __future__ import annotations

from typing import Any

from services.canonical import canonical_hash
from services.durable_store import AtomicMutation, DurableStore, production_store
from services.workflow_contracts import stable_id, utc_now


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


class ProfileFactService:
    """Immutable fact versions with one independently contended pointer per key."""

    def __init__(self, store: DurableStore | None = None):
        self.store = store or production_store()

    async def put(self, *, workspace_id: str, actor_id: str, scope: str,
                  key: str, value: Any, verification_status: str,
                  source_refs: list[str], sensitivity: str = "INTERNAL",
                  retention_class: str = "WORKSPACE_STANDARD",
                  visibility: str = "WORKSPACE", confidence: float = 1.0,
                  conflict_state: str = "CLEAR",
                  idempotency_key: str) -> dict[str, Any]:
        if (scope not in {"WORKSPACE_BUSINESS", "ACTOR_PREFERENCE"}
                or not workspace_id or not actor_id or not key
                or not idempotency_key or len(key) > 160
                or not 0 <= confidence <= 1
                or verification_status not in {
                    "FOUNDER_CONFIRMED", "EVIDENCE_VERIFIED", "PROPOSED"}
                or conflict_state not in {"CLEAR", "CONFLICTING", "RESOLVED"}):
            return _error("profile_fact_invalid", "Profile fact contract is invalid.")
        if scope == "ACTOR_PREFERENCE":
            visibility = "ACTOR_PRIVATE"
        subject = actor_id if scope == "ACTOR_PREFERENCE" else workspace_id
        pointer_id = stable_id("factpointer", workspace_id, scope, subject, key)
        receipt_id = stable_id("factreceipt", workspace_id, idempotency_key)
        existing_receipt = await self.store.get("profile_fact_receipts", receipt_id)
        value_hash = canonical_hash(value, domain="profile-fact-value")
        request_hash = canonical_hash({
            "scope": scope, "subject": subject, "key": key,
            "value_hash": value_hash, "verification_status": verification_status,
            "source_refs": source_refs, "sensitivity": sensitivity,
            "retention_class": retention_class, "visibility": visibility,
        }, domain="profile-fact-request")
        if existing_receipt:
            if existing_receipt.get("request_hash") != request_hash:
                return _error("idempotency_conflict", "Fact key names another update.")
            return {"status": "success", "duplicate": True,
                    "fact": await self.store.get(
                        "profile_facts", existing_receipt["fact_id"])}
        pointer = await self.store.get("profile_fact_pointers", pointer_id)
        previous_id = str((pointer or {}).get("current_fact_id") or "")
        fact_id = stable_id("profilefact", workspace_id, idempotency_key)
        now = utc_now()
        fact = {
            "schema_version": 1, "fact_id": fact_id,
            "workspace_id": workspace_id, "actor_id": actor_id,
            "profile_scope": scope, "subject_id": subject, "key": key,
            "value": value, "value_hash": value_hash,
            "verification_status": verification_status,
            "confidence": confidence, "source_refs": list(source_refs)[:16],
            "created_at": now,
            "confirmed_at": now if verification_status == "FOUNDER_CONFIRMED" else None,
            "last_used_at": None, "sensitivity": sensitivity,
            "retention_class": retention_class, "visibility": visibility,
            "supersedes_fact_id": previous_id or None,
            "conflict_state": conflict_state, "version": 1,
        }
        pointer_row = {
            "schema_version": 1, "pointer_id": pointer_id,
            "workspace_id": workspace_id, "profile_scope": scope,
            "subject_id": subject, "key": key, "current_fact_id": fact_id,
            "updated_at": now, "version": 1,
        }
        receipt = {
            "schema_version": 1, "receipt_id": receipt_id,
            "workspace_id": workspace_id, "request_hash": request_hash,
            "fact_id": fact_id, "pointer_id": pointer_id,
            "created_at": now, "version": 1,
        }
        mutations = [
            AtomicMutation("profile_facts", fact_id, None, record=fact),
            AtomicMutation("profile_fact_receipts", receipt_id, None,
                           record=receipt),
            AtomicMutation(
                "profile_fact_pointers", pointer_id,
                int(pointer["version"]) if pointer else None,
                updates={"current_fact_id": fact_id, "updated_at": now}
                if pointer else None,
                record=None if pointer else pointer_row),
        ]
        committed = await self.store.atomic_compare_and_set(tuple(mutations))
        if not committed:
            return _error("concurrency_conflict", "Fact changed concurrently.")
        return {"status": "success", "duplicate": False,
                "fact": committed[("profile_facts", fact_id)]}

    async def current(self, *, workspace_id: str, actor_id: str = "",
                      include_actor_private: bool = False) -> dict[str, Any]:
        pointers = await self.store.list(
            "profile_fact_pointers", filters={"workspace_id": workspace_id},
            limit=1000)
        facts: dict[str, Any] = {}
        records = []
        for pointer in pointers:
            if pointer.get("profile_scope") == "ACTOR_PREFERENCE":
                if not include_actor_private or pointer.get("subject_id") != actor_id:
                    continue
            fact = await self.store.get(
                "profile_facts", str(pointer.get("current_fact_id") or ""))
            if not fact or fact.get("workspace_id") != workspace_id:
                continue
            facts[fact["key"]] = fact["value"]
            records.append(fact)
        return {"status": "success", "facts": facts, "records": records}

    async def migrate_compatibility(self, *, workspace_id: str, actor_id: str,
                                    profile: dict[str, Any]) -> dict[str, Any]:
        migrated = []
        provenance = dict(profile.get("fact_provenance") or {})
        for key, value in dict(profile.get("facts") or {}).items():
            result = await self.put(
                workspace_id=workspace_id, actor_id=actor_id,
                scope="WORKSPACE_BUSINESS", key=str(key), value=value,
                verification_status=str(
                    (provenance.get(key) or {}).get("verification_level")
                    or "FOUNDER_CONFIRMED"),
                source_refs=[str((provenance.get(key) or {}).get("source_id")
                                 or "compatibility-profile")],
                idempotency_key=f"compat:{profile.get('version', 0)}:{key}")
            if result.get("error"):
                return result
            migrated.append(result["fact"]["fact_id"])
        return {"status": "success", "migrated": len(migrated),
                "fact_ids": migrated}

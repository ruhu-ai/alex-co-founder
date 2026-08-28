"""Portable, tenant-filtered advisory memory adapter (docs/34 Phase 4B)."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import Any

from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types

from services.canonical import canonical_hash
from services.durable_store import AtomicMutation, DurableStore, production_store
from services.workflow_contracts import stable_id, utc_now


class MemoryBackendError(RuntimeError):
    """Visible backend failure; callers must not reinterpret it as no recall."""


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "degraded": True}


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", str(value or "").casefold()))


class PersistentMemoryAdapter:
    async def put(self, item: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        raise NotImplementedError

    async def search(self, query: str, *, workspace_id: str, actor_id: str,
                     allowed_scopes: set[str], sensitivity_ceiling: str,
                     purpose: str, limit: int) -> dict[str, Any]:
        raise NotImplementedError

    async def get(self, memory_id: str, *, workspace_id: str, actor_id: str,
                  allowed_scopes: set[str], purpose: str) -> dict[str, Any]:
        raise NotImplementedError

    async def list_by_source(self, source_ref: str, *, workspace_id: str,
                             cursor: str = "") -> dict[str, Any]:
        raise NotImplementedError

    async def delete_by_source(self, source_ref: str, *, workspace_id: str,
                               deletion_job_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def delete_workspace(self, workspace_id: str,
                               deletion_job_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def health(self) -> dict[str, Any]:
        raise NotImplementedError


class FirestorePersistentMemoryAdapter(PersistentMemoryAdapter):
    """Portable keyword fallback; hard tenant filtering happens before rank."""

    _SENSITIVITY = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2,
                    "RESTRICTED": 3}

    def __init__(self, store: DurableStore | None = None,
                 *, available: bool = True):
        self.store = store or production_store()
        self.available = available

    def _available(self) -> dict[str, Any] | None:
        return None if self.available else _error(
            "memory_backend_unavailable", "Persistent recall is unavailable.")

    async def put(self, item: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        if (failure := self._available()) is not None:
            return failure
        required = {"workspace_id", "source_ref", "summary", "purpose",
                    "scope", "sensitivity", "retention_class", "source_trust"}
        if (not required <= item.keys() or not idempotency_key
                or item["sensitivity"] not in self._SENSITIVITY):
            return _error("memory_contract_invalid", "Memory item is invalid.")
        workspace_id = str(item["workspace_id"])
        memory_id = stable_id("memory", workspace_id, idempotency_key)
        receipt_id = stable_id("memoryreceipt", workspace_id, idempotency_key)
        request_hash = canonical_hash(item, domain="episodic-memory-write")
        prior = await self.store.get("memory_write_receipts", receipt_id)
        if prior:
            if prior.get("request_hash") != request_hash:
                return _error("idempotency_conflict", "Memory key names another item.")
            return {"status": "success", "duplicate": True,
                    "memory_id": prior["memory_id"]}
        now = utc_now()
        row = {
            "schema_version": 1, "memory_id": memory_id,
            "workspace_id": workspace_id,
            "actor_id": str(item.get("actor_id") or ""),
            "source_ref": str(item["source_ref"]),
            "source_hash": canonical_hash(
                item["source_ref"], domain="memory-source"),
            "summary": str(item["summary"])[:2000],
            "purpose": str(item["purpose"]), "scope": str(item["scope"]),
            "sensitivity": str(item["sensitivity"]),
            "retention_class": str(item["retention_class"]),
            "source_trust": str(item["source_trust"]),
            "writer_policy_version": str(
                item.get("writer_policy_version") or "memory-policy-v1"),
            "summary_schema_version": str(
                item.get("summary_schema_version") or "1"),
            "embedding_model": "keyword", "embedding_version": "v1",
            "search_terms": sorted(_terms(str(item["summary"])))[:128],
            "status": "ACTIVE", "created_at": now,
            "expires_at": item.get("expires_at"), "deleted_at": None,
            "version": 1,
        }
        receipt = {
            "schema_version": 1, "receipt_id": receipt_id,
            "workspace_id": workspace_id, "memory_id": memory_id,
            "request_hash": request_hash, "created_at": now, "version": 1,
        }
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("memory_items", memory_id, None, record=row),
            AtomicMutation("memory_write_receipts", receipt_id, None,
                           record=receipt),
        ))
        if not committed:
            return _error("concurrency_conflict", "Memory write raced.")
        return {"status": "success", "duplicate": False,
                "memory_id": memory_id, "receipt_id": receipt_id}

    def _authorized(self, row: dict[str, Any], *, actor_id: str,
                    allowed_scopes: set[str], ceiling: str) -> bool:
        if (row.get("status") != "ACTIVE"
                or row.get("scope") not in allowed_scopes
                or self._SENSITIVITY.get(str(row.get("sensitivity")), 99)
                > self._SENSITIVITY.get(ceiling, -1)):
            return False
        return not (row.get("scope") == "ACTOR_PRIVATE"
                    and row.get("actor_id") != actor_id)

    async def search(self, query: str, *, workspace_id: str, actor_id: str,
                     allowed_scopes: set[str], sensitivity_ceiling: str,
                     purpose: str, limit: int = 10) -> dict[str, Any]:
        if (failure := self._available()) is not None:
            return failure
        if not query or not purpose or not 1 <= limit <= 50:
            return _error("memory_search_invalid", "Memory search is invalid.")
        # Workspace filter is part of the datastore query, before any rank.
        rows = await self.store.list(
            "memory_items", filters={"workspace_id": workspace_id,
                                     "status": "ACTIVE"}, limit=1000)
        query_terms = _terms(query)
        ranked = []
        for row in rows:
            if not self._authorized(
                    row, actor_id=actor_id, allowed_scopes=allowed_scopes,
                    ceiling=sensitivity_ceiling):
                continue
            score = len(query_terms & set(row.get("search_terms") or []))
            if score:
                rendered = str(row.get("summary") or "")
                if row.get("source_trust") != "TRUSTED_FOUNDER":
                    rendered = f"<untrusted-memory>{rendered}</untrusted-memory>"
                ranked.append((score, row, rendered))
        ranked.sort(key=lambda item: (-item[0], item[1]["memory_id"]))
        hits = [{"memory_id": row["memory_id"], "summary": rendered,
                 "score": score, "source_ref": row["source_ref"],
                 "source_trust": row["source_trust"]}
                for score, row, rendered in ranked[:limit]]
        search_id = stable_id(
            "memorysearch", workspace_id, utc_now(), purpose,
            canonical_hash(sorted(query_terms), domain="memory-query-terms"))
        await self.store.create("memory_search_receipts", search_id, {
            "schema_version": 1, "search_id": search_id,
            "workspace_id": workspace_id, "actor_id": actor_id,
            "purpose": purpose, "allowed_scopes": sorted(allowed_scopes),
            "sensitivity_ceiling": sensitivity_ceiling,
            "returned_ids": [hit["memory_id"] for hit in hits],
            "query_term_count": len(query_terms), "backend": "firestore-keyword",
            "created_at": utc_now(), "version": 1,
        })
        return {"status": "success", "degraded": True,
                "backend": "firestore-keyword", "hits": hits}

    async def get(self, memory_id: str, *, workspace_id: str, actor_id: str,
                  allowed_scopes: set[str], purpose: str) -> dict[str, Any]:
        if (failure := self._available()) is not None:
            return failure
        row = await self.store.get("memory_items", memory_id)
        if (not row or row.get("workspace_id") != workspace_id
                or not self._authorized(
                    row, actor_id=actor_id, allowed_scopes=allowed_scopes,
                    ceiling="RESTRICTED")):
            return _error("memory_not_found", "Memory item does not exist.")
        return {"status": "success", "memory": row, "purpose": purpose}

    async def list_by_source(self, source_ref: str, *, workspace_id: str,
                             cursor: str = "") -> dict[str, Any]:
        del cursor
        if (failure := self._available()) is not None:
            return failure
        rows = await self.store.list(
            "memory_items", filters={"workspace_id": workspace_id,
                                     "source_ref": source_ref}, limit=1000)
        return {"status": "success", "items": rows, "next_cursor": None}

    async def _delete(self, rows: list[dict[str, Any]], *, workspace_id: str,
                      deletion_job_id: str) -> dict[str, Any]:
        deleted = 0
        for row in rows:
            if row.get("workspace_id") != workspace_id or row.get("status") == "DELETED":
                continue
            committed = await self.store.compare_and_set(
                "memory_items", row["memory_id"], int(row["version"]), {
                    "status": "DELETED", "summary": "", "search_terms": [],
                    "deleted_at": utc_now(), "deletion_job_id": deletion_job_id})
            deleted += bool(committed)
        receipt_id = stable_id(
            "deletionreceipt", workspace_id, deletion_job_id, "memory")
        await self.store.create("deletion_receipts", receipt_id, {
            "schema_version": 1, "receipt_id": receipt_id,
            "workspace_id": workspace_id, "deletion_job_id": deletion_job_id,
            "backend": "persistent_memory", "deleted_count": deleted,
            "created_at": utc_now(), "version": 1})
        return {"status": "success", "deleted_count": deleted,
                "receipt_id": receipt_id}

    async def delete_by_source(self, source_ref: str, *, workspace_id: str,
                               deletion_job_id: str) -> dict[str, Any]:
        listed = await self.list_by_source(source_ref, workspace_id=workspace_id)
        return await self._delete(
            listed.get("items") or [], workspace_id=workspace_id,
            deletion_job_id=deletion_job_id)

    async def delete_workspace(self, workspace_id: str,
                               deletion_job_id: str) -> dict[str, Any]:
        rows = await self.store.list(
            "memory_items", filters={"workspace_id": workspace_id}, limit=1000)
        return await self._delete(
            rows, workspace_id=workspace_id, deletion_job_id=deletion_job_id)

    def health(self) -> dict[str, Any]:
        return {"status": "success" if self.available else "error",
                "backend": "firestore-keyword", "namespace": "workspace_id",
                "hard_filters": True, "source_enumeration": True,
                "deletion": True, "degraded": True}


class ADKPersistentMemoryService(BaseMemoryService):
    """ADK binding over the same server-owned adapter used by product routes."""

    def __init__(self, adapter: PersistentMemoryAdapter):
        self.adapter = adapter

    async def add_session_to_memory(self, session) -> None:
        raise NotImplementedError("Only declared milestone writes are permitted.")

    async def add_memory(self, *, app_name: str, user_id: str,
                         memories: Sequence[MemoryEntry],
                         custom_metadata=None) -> None:
        metadata = dict(custom_metadata or {})
        workspace_id = str(metadata.get("workspace_id") or user_id)
        for index, memory in enumerate(memories):
            text = " ".join(str(getattr(part, "text", "") or "")
                            for part in (memory.content.parts or []))
            result = await self.adapter.put({
                "workspace_id": workspace_id,
                "actor_id": str(metadata.get("actor_id") or user_id),
                "source_ref": str(metadata.get("source_ref") or memory.id or ""),
                "summary": text, "purpose": str(metadata.get("purpose") or "recall"),
                "scope": str(metadata.get("scope") or "WORKSPACE"),
                "sensitivity": str(metadata.get("sensitivity") or "INTERNAL"),
                "retention_class": str(metadata.get("retention_class") or "STANDARD"),
                "source_trust": str(metadata.get("source_trust") or "UNTRUSTED_EXTERNAL"),
            }, f"adk:{app_name}:{memory.id or index}:{metadata.get('policy_version', 'v1')}")
            if result.get("error"):
                raise MemoryBackendError(result["error_code"])

    async def search_memory(self, *, app_name: str, user_id: str,
                            query: str) -> SearchMemoryResponse:
        del app_name
        result = await self.adapter.search(
            query, workspace_id=user_id, actor_id=user_id,
            allowed_scopes={"WORKSPACE", "ACTOR_PRIVATE"},
            sensitivity_ceiling="INTERNAL", purpose="adk_recall", limit=10)
        if result.get("error"):
            raise MemoryBackendError(result["error_code"])
        entries = [MemoryEntry(
            id=hit["memory_id"], author="memory",
            content=types.Content(role="user", parts=[types.Part(text=hit["summary"])]),
            custom_metadata={"source_ref": hit["source_ref"],
                             "source_trust": hit["source_trust"]})
            for hit in result["hits"]]
        return SearchMemoryResponse(memories=entries)


_adapter: PersistentMemoryAdapter | None = None


def configured_adapter() -> PersistentMemoryAdapter | None:
    global _adapter
    if os.environ.get("PERSISTENT_MEMORY_BACKEND", "disabled") != "firestore":
        return None
    if _adapter is None:
        _adapter = FirestorePersistentMemoryAdapter()
    return _adapter


def configured_adk_service() -> ADKPersistentMemoryService | None:
    adapter = configured_adapter()
    return ADKPersistentMemoryService(adapter) if adapter else None

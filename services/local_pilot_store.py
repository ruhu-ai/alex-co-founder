"""Persistent, local-only durable stores for the controlled Spec 39 M2 pilot.

This module is never a production-store fallback.  It activates only through
the explicit local-pilot configuration, refuses Cloud Run, restricts paths to
the ignored pilot directory, and keeps application memory and the deletion
deny ledger in different SQLite files.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime
from typing import Any, Sequence

from services.durable_store import AtomicMutation


ROOT = Path(__file__).resolve().parents[1]
PILOT_ROOT = (ROOT / "tmp" / "spec39-m2-local-pilot").resolve()
DEFAULT_DATA_PATH = PILOT_ROOT / "pilot-data.sqlite3"
DEFAULT_LEDGER_PATH = PILOT_ROOT / "deletion-ledger.sqlite3"


def local_pilot_mode() -> bool:
    """Return true only for the exact local opt-in outside Cloud Run."""
    return (os.environ.get("DURABLE_MEMORY_M2_LOCAL_PILOT", "0") == "1"
            and not os.environ.get("K_SERVICE"))


def _validated_path(value: str, default: Path) -> Path:
    path = Path(value).expanduser() if value else default
    resolved = path.resolve()
    try:
        resolved.relative_to(PILOT_ROOT)
    except ValueError as exc:
        raise ValueError("local M2 pilot paths must stay inside the pilot directory") from exc
    if path.is_symlink():
        raise ValueError("local M2 pilot store path cannot be a symlink")
    return resolved


def configured_paths() -> tuple[Path, Path]:
    """Resolve and validate the distinct application and ledger paths."""
    data = _validated_path(
        os.environ.get("DURABLE_MEMORY_M2_LOCAL_DATA_PATH", ""),
        DEFAULT_DATA_PATH)
    ledger = _validated_path(
        os.environ.get("DURABLE_MEMORY_M2_LOCAL_LEDGER_PATH", ""),
        DEFAULT_LEDGER_PATH)
    if data == ledger:
        raise ValueError("local M2 pilot data and deletion ledger must be separate files")
    return data, ledger


def _sort_key(value: Any) -> tuple[int, float, str]:
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, bool):
        return (1, float(value), "")
    if isinstance(value, (int, float)):
        return (1, float(value), "")
    return (2, 0.0, str(value))


class SQLiteDurableStore:
    """Small persistent DurableStore for one isolated synthetic local pilot."""

    def __init__(self, path: Path):
        self.path = _validated_path(str(path), path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS records ("
                "collection TEXT NOT NULL, document_id TEXT NOT NULL, "
                "payload TEXT NOT NULL, PRIMARY KEY(collection, document_id))")
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _encode(record: dict[str, Any]) -> str:
        def encode_value(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat()
            raise TypeError(f"unsupported local pilot value: {type(value).__name__}")
        return json.dumps(record, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, default=encode_value)

    @staticmethod
    def _decode(payload: str) -> dict[str, Any]:
        row = json.loads(payload)
        if not isinstance(row, dict):
            raise ValueError("local pilot record is not an object")
        return row

    @staticmethod
    def _read(connection: sqlite3.Connection, collection: str,
              document_id: str) -> dict[str, Any] | None:
        found = connection.execute(
            "SELECT payload FROM records WHERE collection=? AND document_id=?",
            (collection, document_id)).fetchone()
        return SQLiteDurableStore._decode(found["payload"]) if found else None

    async def get(self, collection: str,
                  document_id: str) -> dict[str, Any] | None:
        async with self._lock:
            with self._connect() as connection:
                row = self._read(connection, collection, document_id)
        return ({**deepcopy(row), "id": document_id} if row is not None else None)

    async def create(self, collection: str, document_id: str,
                     record: dict[str, Any]) -> bool:
        async with self._lock:
            with self._connect() as connection:
                try:
                    connection.execute(
                        "INSERT INTO records(collection,document_id,payload) VALUES(?,?,?)",
                        (collection, document_id, self._encode(deepcopy(record))))
                except sqlite3.IntegrityError:
                    return False
        return True

    async def put(self, collection: str, document_id: str,
                  record: dict[str, Any]) -> None:
        async with self._lock:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO records(collection,document_id,payload) VALUES(?,?,?) "
                    "ON CONFLICT(collection,document_id) DO UPDATE SET payload=excluded.payload",
                    (collection, document_id, self._encode(deepcopy(record))))

    async def delete(self, collection: str, document_id: str) -> None:
        async with self._lock:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM records WHERE collection=? AND document_id=?",
                    (collection, document_id))

    async def compare_and_set(self, collection: str, document_id: str,
                              expected_version: int,
                              updates: dict[str, Any]) -> dict[str, Any] | None:
        committed = await self.atomic_compare_and_set((AtomicMutation(
            collection, document_id, expected_version, updates=updates),))
        return (committed or {}).get((collection, document_id))

    async def atomic_compare_and_set(
            self, mutations: Sequence[AtomicMutation]
            ) -> dict[tuple[str, str], dict[str, Any]] | None:
        items = tuple(mutations)
        if not items or len(items) > 100:
            raise ValueError("atomic mutation batch must contain 1..100 items")
        keys = [(item.collection, item.document_id) for item in items]
        if len(set(keys)) != len(keys):
            raise ValueError("atomic mutation batch contains duplicate document")
        async with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                committed: dict[tuple[str, str], dict[str, Any]] = {}
                for item in items:
                    if item.check_only and item.expected_version is None:
                        raise ValueError("check-only mutation requires an existing version")
                    current = self._read(
                        connection, item.collection, item.document_id)
                    if item.expected_version is None:
                        if current is not None:
                            connection.rollback()
                            return None
                        row = {**deepcopy(item.record), "version": 1}
                    else:
                        if (current is None
                                or int(current.get("version", 0))
                                != item.expected_version):
                            connection.rollback()
                            return None
                        row = (deepcopy(current) if item.check_only else {
                            **({} if item.replace else current),
                            **(deepcopy(item.record) if item.replace
                               else deepcopy(item.updates)),
                            "version": item.expected_version + 1,
                        })
                    committed[(item.collection, item.document_id)] = row
                for item in items:
                    if item.check_only:
                        continue
                    row = committed[(item.collection, item.document_id)]
                    connection.execute(
                        "INSERT INTO records(collection,document_id,payload) VALUES(?,?,?) "
                        "ON CONFLICT(collection,document_id) DO UPDATE SET payload=excluded.payload",
                        (item.collection, item.document_id, self._encode(row)))
                connection.commit()
                return {
                    key: {**deepcopy(row), "id": key[1]}
                    for key, row in committed.items()
                }
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    async def list(self, collection: str, *, filters: dict[str, Any],
                   order_by: str = "", descending: bool = False,
                   limit: int = 200,
                   start_after: tuple[str, Any] | None = None
                   ) -> list[dict[str, Any]]:
        async with self._lock:
            with self._connect() as connection:
                found = connection.execute(
                    "SELECT document_id,payload FROM records WHERE collection=?",
                    (collection,)).fetchall()
        rows = [
            {**self._decode(item["payload"]), "id": item["document_id"]}
            for item in found
        ]
        rows = [row for row in rows if all(
            row.get(key) == value for key, value in filters.items())]
        if start_after:
            field_name, value = start_after
            rows = [row for row in rows
                    if row.get(field_name) is not None
                    and row.get(field_name) > value]
        if order_by:
            rows.sort(key=lambda row: _sort_key(row.get(order_by)),
                      reverse=descending)
        return rows[:max(1, min(limit, 1000))]

    async def counts(self) -> dict[str, int]:
        """Return content-free collection counts for pilot evidence."""
        async with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT collection,COUNT(*) AS count FROM records "
                    "GROUP BY collection ORDER BY collection").fetchall()
        return {str(row["collection"]): int(row["count"]) for row in rows}


_stores: tuple[SQLiteDurableStore, SQLiteDurableStore] | None = None
_store_paths: tuple[Path, Path] | None = None


def configured_stores() -> tuple[SQLiteDurableStore, SQLiteDurableStore]:
    """Return process singletons for the validated, distinct pilot files."""
    global _stores, _store_paths
    if not local_pilot_mode():
        raise RuntimeError("local M2 pilot mode is not active")
    paths = configured_paths()
    if _stores is None or _store_paths != paths:
        _stores = (SQLiteDurableStore(paths[0]), SQLiteDurableStore(paths[1]))
        _store_paths = paths
    return _stores


def reset_for_tests() -> None:
    global _stores, _store_paths
    _stores = None
    _store_paths = None

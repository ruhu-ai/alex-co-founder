"""Encrypted Gemini Live session-resumption state."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services import firestore, live_resumption


@pytest.fixture
def resumption_store(monkeypatch):
    rows = {}

    async def _upsert(workspace_id, session_id, *, ciphertext, expires_at):
        rows[(workspace_id, session_id)] = {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "ciphertext": ciphertext,
            "expires_at": expires_at,
        }

    async def _get(workspace_id, session_id):
        return rows.get((workspace_id, session_id))

    async def _delete(workspace_id, session_id):
        rows.pop((workspace_id, session_id), None)

    monkeypatch.setenv("APP_SESSION_SECRET", "test-live-secret")
    monkeypatch.setattr(firestore, "upsert_live_session_resumption", _upsert)
    monkeypatch.setattr(firestore, "get_live_session_resumption", _get)
    monkeypatch.setattr(firestore, "delete_live_session_resumption", _delete)
    return rows


@pytest.mark.asyncio
async def test_handle_is_encrypted_and_bound_to_workspace_session(resumption_store):
    handle = "opaque-provider-handle-never-log"
    saved = await live_resumption.save(
        workspace_id="founder", session_id="s-1", handle=handle)

    assert saved["status"] == "success"
    row = resumption_store[("founder", "s-1")]
    assert handle not in row["ciphertext"]
    assert await live_resumption.load(
        workspace_id="founder", session_id="s-1") == handle

    # Copying ciphertext to another session fails authenticated decryption and
    # removes the unusable copy without exposing provider material.
    resumption_store[("founder", "s-2")] = {
        **row, "session_id": "s-2"}
    assert await live_resumption.load(
        workspace_id="founder", session_id="s-2") == ""
    assert ("founder", "s-2") not in resumption_store


@pytest.mark.asyncio
async def test_expired_or_corrupt_handle_fails_closed(resumption_store):
    resumption_store[("founder", "expired")] = {
        "workspace_id": "founder", "session_id": "expired",
        "ciphertext": "not-ciphertext",
        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    }
    assert await live_resumption.load(
        workspace_id="founder", session_id="expired") == ""
    assert resumption_store == {}


@pytest.mark.asyncio
async def test_store_degradation_does_not_terminate_live_voice(monkeypatch):
    monkeypatch.setenv("APP_SESSION_SECRET", "test-live-secret")

    async def _failed(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(firestore, "upsert_live_session_resumption", _failed)
    result = await live_resumption.save(
        workspace_id="founder", session_id="s", handle="opaque")
    assert result == {"status": "error", "error": True,
                      "error_code": "live_resumption_store_unavailable"}


def test_live_surface_wires_resumption_compression_and_long_call_budget():
    source = (live_resumption.__file__.replace("services/live_resumption.py", "app/live.py"))
    text = open(source, encoding="utf-8").read()  # noqa: SIM115
    assert "SessionResumptionConfig" in text
    assert "ContextWindowCompressionConfig" in text
    assert "live_session_resumption_update" in text
    assert "GetSessionConfig(num_recent_events=100)" in text
    assert "60 * 60, 5 * 60, 4 * 60 * 60" in text
    assert "new_handle" not in text.split("logger.")[-1]
    assert "google_adk.google.adk.flows.llm_flows.base_llm_flow" in text


def test_live_handle_has_a_declared_firestore_ttl():
    manifest = json.loads(Path("infra/firestore.ttl.json").read_text())
    assert {"collectionGroup": "live_session_resumptions",
            "fieldPath": "expires_at", "ttl": True} in manifest["policies"]

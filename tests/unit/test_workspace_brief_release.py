"""M1-only route gates and release configuration."""

from __future__ import annotations

import json

import pytest

from services.actor_identity import ActorPrincipal, WorkspaceRole


def _principal() -> ActorPrincipal:
    return ActorPrincipal(
        actor_id="actor-a", workspace_id="workspace-a",
        role=WorkspaceRole.FOUNDER, session_auth_time=1_800_000_000,
        role_grants=frozenset(), candidate_assignments=frozenset(),
        interview_assignments=frozenset(), membership_version=1,
        membership_id="member-a")


@pytest.mark.asyncio
async def test_private_session_stops_before_brief_assembly(monkeypatch):
    from app import main as appmod

    async def principal(_request):
        return _principal()

    async def private_mode(_principal, _session_id):
        return "PRIVATE"

    class BombAssembler:
        async def assemble(self, **_kwargs):
            raise AssertionError("private session assembled a workspace brief")

    monkeypatch.setenv("DURABLE_BRIEF_M1_ENABLED", "true")
    monkeypatch.setenv("DURABLE_BRIEF_M1_WORKSPACE_ALLOWLIST", "workspace-a")
    monkeypatch.setattr(appmod, "_platform_human", principal)
    monkeypatch.setattr(appmod, "_owned_session_memory_mode", private_mode)
    monkeypatch.setattr(
        appmod.workspace_brief, "configured_assembler", lambda: BombAssembler())

    response = await appmod.api_workspace_brief(
        object(), session_id="session-private", since="")
    payload = json.loads(bytes(response.body))
    assert response.status_code == 403
    assert payload["error_code"] == "brief_disabled_for_private_session"


@pytest.mark.asyncio
async def test_standard_session_returns_only_assembler_evidence(monkeypatch):
    from app import main as appmod

    calls = []

    async def principal(_request):
        return _principal()

    async def standard_mode(_principal, _session_id):
        return "STANDARD"

    class Assembler:
        async def assemble(self, **kwargs):
            calls.append(kwargs)
            return {
                "status": "success", "scope": "WORKSPACE",
                "source": "registered_durable_projections",
                "model_calls": 0, "memory_backend_calls": 0,
                "transcript_reads": 0, "pending_signals_reads": 0,
                "partial": False, "unavailable": False,
            }

    monkeypatch.setenv("DURABLE_BRIEF_M1_ENABLED", "true")
    monkeypatch.setenv("DURABLE_BRIEF_M1_WORKSPACE_ALLOWLIST", "workspace-a")
    monkeypatch.setattr(appmod, "_platform_human", principal)
    monkeypatch.setattr(appmod, "_owned_session_memory_mode", standard_mode)
    monkeypatch.setattr(
        appmod.workspace_brief, "configured_assembler", lambda: Assembler())

    result = await appmod.api_workspace_brief(
        object(), session_id="session-standard", since="2026-08-27T00:00:00Z")
    assert result["session_memory_mode"] == "STANDARD"
    assert result["private_session"] is False
    assert result["source"] == "registered_durable_projections"
    assert result["model_calls"] == result["memory_backend_calls"] == 0
    assert result["transcript_reads"] == result["pending_signals_reads"] == 0
    assert calls[0]["principal"].workspace_id == "workspace-a"


@pytest.mark.asyncio
async def test_unlisted_workspace_stops_before_session_or_assembly(monkeypatch):
    from app import main as appmod

    async def principal(_request):
        return _principal()

    async def must_not_read_session(*_args):
        raise AssertionError("unlisted workspace reached the session store")

    class BombAssembler:
        async def assemble(self, **_kwargs):
            raise AssertionError("unlisted workspace assembled a brief")

    monkeypatch.setenv("DURABLE_BRIEF_M1_ENABLED", "true")
    monkeypatch.setenv("DURABLE_BRIEF_M1_WORKSPACE_ALLOWLIST", "workspace-b")
    monkeypatch.setattr(appmod, "_platform_human", principal)
    monkeypatch.setattr(appmod, "_owned_session_memory_mode", must_not_read_session)
    monkeypatch.setattr(
        appmod.workspace_brief, "configured_assembler", lambda: BombAssembler())

    response = await appmod.api_workspace_brief(
        object(), session_id="session-standard", since="")
    payload = json.loads(bytes(response.body))
    assert response.status_code == 503
    assert payload["error_code"] == "durable_brief_not_released"

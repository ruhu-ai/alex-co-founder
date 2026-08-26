"""Operator backfill safety contracts (docs/23 WI-5)."""

from __future__ import annotations

from scripts import backfill_session_resources as backfill


def _plan():
    return [
        backfill._linked(
            resource_type="document", canonical_id="app:pack",
            session_id="s-a", relationship="produced",
            occurrence_key="legacy_document:d1", title="Sensitive title"),
        backfill._unlinked(
            resource_type="application", canonical_id="app-2",
            title="Another sensitive title", reason="ambiguous"),
    ]


async def test_dry_run_writes_nothing(monkeypatch):
    async def _forbidden(**_kwargs):
        raise AssertionError("dry-run attempted a write")

    monkeypatch.setattr(
        backfill.sr, "register_session_resource", _forbidden)
    monkeypatch.setattr(
        backfill.sr, "register_legacy_unlinked_resource", _forbidden)
    summary = await backfill.apply_plan("founder", _plan(), apply=False)
    assert summary["mode"] == "dry_run"
    assert summary["linked"] == 1
    assert summary["legacy_unlinked"] == 1
    assert "Sensitive" not in str(summary)


async def test_apply_uses_live_idempotent_service_seams(monkeypatch):
    calls = []

    async def _linked(**kwargs):
        calls.append(("linked", kwargs))
        return {"status": "success", "replayed": True}

    async def _unlinked(**kwargs):
        calls.append(("unlinked", kwargs))
        return {"status": "success", "replayed": False}

    monkeypatch.setattr(backfill.sr, "register_session_resource", _linked)
    monkeypatch.setattr(
        backfill.sr, "register_legacy_unlinked_resource", _unlinked)
    summary = await backfill.apply_plan("founder", _plan(), apply=True)
    assert [kind for kind, _ in calls] == ["linked", "unlinked"]
    assert calls[0][1]["session_verified"] is True
    assert calls[0][1]["producer_kind"] == "migration"
    assert "session_id" not in calls[1][1]
    assert summary["replayed"] == 1
    assert summary["errors"] == 0

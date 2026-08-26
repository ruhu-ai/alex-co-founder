from datetime import datetime, timedelta, timezone

import pytest

from services import browser_expiry, browser_service, task_queue
from services.browser_migrations import missing_projection_fields

pytestmark = pytest.mark.asyncio


async def test_stale_expiry_is_noop_and_matching_generation_closes(monkeypatch):
    now = datetime.now(timezone.utc)
    run = {
        "run_id": "r1", "status": "active", "kind": "browse",
        "lease_generation": 2,
        "expires_at": (now - timedelta(seconds=1)).isoformat(),
    }
    closed = []

    async def get_run(_run_id):
        return dict(run)

    async def close_run(run_id, reason, actor, *, require_lease_generation=None):
        # The close must re-verify the generation INSIDE the close lock; the
        # caller's earlier check can be invalidated by a concurrent renewal.
        closed.append((run_id, reason, actor, require_lease_generation))
        if (require_lease_generation is not None
                and require_lease_generation != run["lease_generation"]):
            return {"status": "success", "already_renewed": True}
        return {"status": "success", "run_id": run_id, "already_closed": False}

    monkeypatch.setattr(browser_service.firestore, "get_browser_run", get_run)
    monkeypatch.setattr(browser_service, "close_run", close_run)

    stale = await browser_service.expire_run("r1", 1)
    assert stale == {"status": "success", "already_renewed": True}
    assert closed == []
    matching = await browser_service.expire_run("r1", 2)
    assert matching["error_code"] == "run_expired"
    assert closed == [("r1", "expired", "system:browser_expiry", 2)]


async def test_expiry_aborts_when_the_run_is_renewed_before_the_close_lock(
        monkeypatch):
    """The window between expire_run's checks and the close: a successful action
    renews the lease, so the close must abort rather than kill a live run."""
    now = datetime.now(timezone.utc)
    run = {"run_id": "r1", "status": "active", "kind": "browse",
           "lease_generation": 2,
           "expires_at": (now - timedelta(seconds=1)).isoformat()}

    async def get_run(_run_id):
        return dict(run)

    async def close_run(run_id, reason, actor, *, require_lease_generation=None):
        run["lease_generation"] = 3          # renewed after expire_run's check
        if require_lease_generation != run["lease_generation"]:
            return {"status": "success", "already_renewed": True}
        raise AssertionError("closed a renewed run")

    monkeypatch.setattr(browser_service.firestore, "get_browser_run", get_run)
    monkeypatch.setattr(browser_service, "close_run", close_run)
    assert await browser_service.expire_run("r1", 2) == {
        "status": "success", "already_renewed": True
    }


async def test_unparseable_expiry_never_closes_a_run(monkeypatch):
    async def get_run(_run_id):
        return {"run_id": "r1", "status": "active", "kind": "browse",
                "lease_generation": 1, "expires_at": "not-a-timestamp"}

    async def close_run(*_args, **_kwargs):
        raise AssertionError("closed on an unparseable expiry")

    monkeypatch.setattr(browser_service.firestore, "get_browser_run", get_run)
    monkeypatch.setattr(browser_service, "close_run", close_run)
    result = await browser_service.expire_run("r1", 1)
    assert result["not_due"] is True and result["unparsed_expiry"] is True


async def test_not_yet_due_expiry_is_harmless(monkeypatch):
    async def get_run(_run_id):
        return {
            "run_id": "r1", "status": "active", "kind": "fill",
            "lease_generation": 4,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
        }

    monkeypatch.setattr(browser_service.firestore, "get_browser_run", get_run)
    assert await browser_service.expire_run("r1", 4) == {
        "status": "success", "not_due": True
    }


async def test_same_generation_reenqueue_uses_expiry_specific_task_identity(
        monkeypatch):
    """An ambiguously-created early task must not dedupe away the later task
    that covers the durable expiry eventually committed for that generation."""
    monkeypatch.setenv("K_SERVICE", "co-founder")
    keys = []

    def enqueue(_path, _payload, dedupe_key, **_kwargs):
        keys.append(dedupe_key)
        return {"status": "success"}

    monkeypatch.setattr(task_queue, "enqueue", enqueue)
    async def callback(*_args):
        return {"status": "success"}
    first = "2026-08-25T12:00:00+00:00"
    later = "2026-08-25T12:05:00+00:00"

    await browser_expiry.schedule("r1", 2, first, callback)
    await browser_expiry.schedule("r1", 2, later, callback)

    assert keys == [f"browser-expire:r1:2:{first}",
                    f"browser-expire:r1:2:{later}"]
    assert keys[0] != keys[1]


def test_browser_run_migration_is_idempotent_and_kind_aware():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fill = missing_projection_fields({"kind": "fill", "version": 8}, now)
    browse = missing_projection_fields({"kind": "browse"}, now)
    assert "version" not in fill
    assert datetime.fromisoformat(fill["expires_at"]) == now + timedelta(minutes=30)
    assert datetime.fromisoformat(browse["expires_at"]) == now + timedelta(minutes=5)
    complete = {"kind": "browse", **browse}
    assert missing_projection_fields(complete, now) == {}

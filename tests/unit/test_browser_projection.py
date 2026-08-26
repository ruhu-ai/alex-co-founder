"""Upload/transaction/publication ordering for browser frames (docs/22)."""

from unittest.mock import AsyncMock

import pytest

from services import browser_service

pytestmark = pytest.mark.asyncio


class Page:
    url = "https://example.test/path?token=secret"

    def __init__(self, shot=b"jpeg"):
        self.shot = shot

    async def screenshot(self, **_kwargs):
        if isinstance(self.shot, Exception):
            raise self.shot
        return self.shot

    async def title(self):
        return "Example"


@pytest.fixture
def projection(monkeypatch):
    run_id = "frame-run"
    run = {
        "run_id": run_id, "app_name": "co_founder", "user_id": "founder",
        "session_id": "s1", "status": "active", "version": 1, "frame_seq": 0,
    }

    async def get_run(_run_id):
        return dict(run)

    monkeypatch.setattr(browser_service.firestore, "get_browser_run", get_run)
    publish = AsyncMock()
    monkeypatch.setattr(browser_service.browser_event_hub, "publish", publish)
    browser_service._browse_contexts[run_id] = {"page": Page(), "artifact": None}
    yield run_id, run, publish
    browser_service._browse_contexts.pop(run_id, None)
    browser_service._frame_locks.pop(run_id, None)


async def test_capture_failure_commits_null_artifact_then_publishes(
    projection, monkeypatch
):
    run_id, _run, publish = projection
    browser_service._browse_contexts[run_id]["page"] = Page(RuntimeError("capture"))
    commits = []

    async def commit(_run_id, seq, frame, artifact):
        commits.append((seq, frame, artifact))
        return {"committed": True, **frame, "seq": seq, "run_version": 2,
                "artifact": artifact}

    monkeypatch.setattr(browser_service.firestore, "commit_browser_frame", commit)
    assert await browser_service.capture_frame(run_id, "after") is None
    assert commits[0][2] is None
    publish.assert_awaited_once()


async def test_failed_transaction_publishes_nothing_and_leaves_orphan(
    projection, monkeypatch
):
    run_id, _run, publish = projection
    uploaded = []
    monkeypatch.setattr(
        browser_service.storage, "save_bytes",
        lambda name, body: uploaded.append((name, body)),
    )

    async def rejected(*_args):
        return {"committed": False, "conflict": False}

    monkeypatch.setattr(browser_service.firestore, "commit_browser_frame", rejected)
    assert await browser_service.capture_frame(run_id, "after") is None
    assert uploaded == [(f"browserframe_{run_id}_1.jpg", b"jpeg")]
    publish.assert_not_awaited()


async def test_oversized_frame_commits_metadata_without_upload(
    projection, monkeypatch
):
    run_id, _run, publish = projection
    browser_service._browse_contexts[run_id]["page"] = Page(b"x" * (500 * 1024 + 1))
    saved = []
    monkeypatch.setattr(
        browser_service.storage, "save_bytes",
        lambda *args: saved.append(args),
    )
    artifacts = []

    async def commit(_run_id, seq, frame, artifact):
        artifacts.append(artifact)
        return {"committed": True, **frame, "seq": seq, "run_version": 2,
                "artifact": artifact}

    monkeypatch.setattr(browser_service.firestore, "commit_browser_frame", commit)
    await browser_service.capture_frame(run_id, "nav")
    assert saved == [] and artifacts == [None]
    publish.assert_awaited_once()

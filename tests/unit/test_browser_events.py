"""Bounded browser observation fan-out checks (docs/22)."""

import pytest

from services.browser_events import (
    MAX_SESSION_SUBSCRIBERS,
    QUEUE_LIMIT,
    BrowserEventHub,
    TooManyBrowserSubscribers,
)

pytestmark = pytest.mark.asyncio
KEY = ("co_founder", "founder", "session-1")


async def test_subscription_cleanup_and_overflow_resync():
    hub = BrowserEventHub()
    async with hub.subscribe(KEY) as subscription:
        for version in range(QUEUE_LIMIT + 1):
            await hub.publish(
                KEY, {"type": "browser.frame", "run_id": "r1", "version": version}
            )
        assert subscription.queue.qsize() == 1
        assert (await subscription.queue.get())["type"] == "browser.resync"
        assert hub.subscriber_count(KEY) == 1
    assert hub.subscriber_count(KEY) == 0
    assert subscription.queue.empty()


async def test_fourth_session_stream_is_refused_and_all_exit_paths_clean():
    hub = BrowserEventHub()
    managers = [hub.subscribe(KEY) for _ in range(MAX_SESSION_SUBSCRIBERS)]
    subscriptions = [await manager.__aenter__() for manager in managers]
    assert len(subscriptions) == 3
    with pytest.raises(TooManyBrowserSubscribers):
        async with hub.subscribe(KEY):
            pass
    for manager in managers:
        await manager.__aexit__(None, None, None)
    assert hub.subscriber_count() == 0

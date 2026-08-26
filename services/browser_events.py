"""Bounded snapshot-first browser event fan-out (docs/22).

The hub is intentionally ephemeral: Firestore remains durable truth.  This
module imports neither Playwright nor application/ADK state.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from services import browser_metrics

SessionKey = tuple[str, str, str]
QUEUE_LIMIT = 16
MAX_SESSION_SUBSCRIBERS = 3
# Internal marker: ends a stream at process shutdown. Never sent to a client.
SHUTDOWN_SENTINEL = "browser.__shutdown__"


class TooManyBrowserSubscribers(RuntimeError):
    """A founder/session already owns the reviewed number of streams."""


@dataclass(slots=True, eq=False)
class Subscription:
    session_key: SessionKey
    queue: asyncio.Queue[dict[str, Any]]
    closed: bool = False
    resync_pending: bool = False


class BrowserEventHub:
    def __init__(self) -> None:
        self._subscribers: dict[SessionKey, set[Subscription]] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def subscribe(self, session_key: SessionKey) -> AsyncIterator[Subscription]:
        subscription = Subscription(
            session_key=session_key, queue=asyncio.Queue(maxsize=QUEUE_LIMIT)
        )
        async with self._lock:
            bucket = self._subscribers.setdefault(session_key, set())
            if len(bucket) >= MAX_SESSION_SUBSCRIBERS:
                if not bucket:
                    self._subscribers.pop(session_key, None)
                raise TooManyBrowserSubscribers("too many Browser streams")
            bucket.add(subscription)
            browser_metrics.record(
                "browser_subscriber_open", subscriber_count=len(bucket)
            )
        try:
            yield subscription
        finally:
            subscription.closed = True
            # Shielded: a second cancellation while acquiring the lock would
            # otherwise skip removal, leaving a zombie that permanently consumes
            # one of this session's MAX_SESSION_SUBSCRIBERS slots.
            async def _remove() -> None:
                async with self._lock:
                    bucket = self._subscribers.get(session_key)
                    if bucket is not None:
                        bucket.discard(subscription)
                        if not bucket:
                            self._subscribers.pop(session_key, None)

            await asyncio.shield(asyncio.ensure_future(_remove()))
            browser_metrics.record(
                "browser_subscriber_close",
                subscriber_count=self.subscriber_count(session_key),
            )
            while not subscription.queue.empty():
                try:
                    subscription.queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - race defense
                    break

    async def publish(self, session_key: SessionKey, event: dict[str, Any]) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers.get(session_key, ()))
        for subscriber in subscribers:
            if subscriber.closed:
                continue
            if subscriber.resync_pending:
                continue
            if subscriber.queue.full():
                browser_metrics.record("browser_event_overflow")
                while not subscriber.queue.empty():
                    try:
                        subscriber.queue.get_nowait()
                    except asyncio.QueueEmpty:  # pragma: no cover
                        break
                subscriber.queue.put_nowait(
                    {
                        "type": "browser.resync",
                        "run_id": event.get("run_id"),
                        "version": event.get("version", 0),
                    }
                )
                subscriber.resync_pending = True
                continue
            subscriber.queue.put_nowait(dict(event))

    def subscriber_count(self, session_key: SessionKey | None = None) -> int:
        if session_key is not None:
            return len(self._subscribers.get(session_key, ()))
        return sum(len(bucket) for bucket in self._subscribers.values())

    async def clear(self) -> None:
        """Detach every subscriber and wake any blocked reader.

        Marking subscribers closed is not enough at shutdown: a stream parked in
        `queue.get()` would keep yielding keepalives until its horizon. The
        sentinel lets the route end the response promptly.
        """
        async with self._lock:
            subscribers = [
                subscriber
                for bucket in self._subscribers.values()
                for subscriber in bucket
            ]
            self._subscribers.clear()
        for subscriber in subscribers:
            subscriber.closed = True
            while not subscriber.queue.empty():
                try:
                    subscriber.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            with contextlib.suppress(asyncio.QueueFull):
                subscriber.queue.put_nowait({"type": SHUTDOWN_SENTINEL})


hub = BrowserEventHub()

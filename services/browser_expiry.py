"""Generation-safe, event-driven browser lease expiry (docs/22)."""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import datetime, timezone
from typing import Awaitable, Callable

ExpireCallback = Callable[[str, int], Awaitable[dict]]
_local_timers: dict[str, asyncio.Task[None]] = {}


def _seconds_until(expires_at: str) -> float:
    value = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (value - datetime.now(timezone.utc)).total_seconds())


async def schedule(
    run_id: str,
    lease_generation: int,
    expires_at: str,
    callback: ExpireCallback,
) -> dict:
    """Schedule one durable Cloud Task or one cancellable local timer."""
    cancel(run_id)
    if os.environ.get("K_SERVICE"):
        from services import task_queue

        return await asyncio.to_thread(
            task_queue.enqueue,
            "/tasks/browser_expire",
            {"run_id": run_id, "lease_generation": lease_generation},
            # Include the exact expiry, not only the generation. An ambiguous
            # first enqueue may have created the task even though the client
            # saw an error. A later retry of the same generation must create a
            # second, later task; otherwise the earlier task can wake before the
            # durable expiry, no-op, and leave no task for the real deadline.
            f"browser-expire:{run_id}:{lease_generation}:{expires_at}",
            queue_name="co-founder-browser-expiry",
            schedule_at=expires_at,
        )

    async def _wait() -> None:
        try:
            await asyncio.sleep(_seconds_until(expires_at))
            await callback(run_id, lease_generation)
        except asyncio.CancelledError:
            raise
        finally:
            if _local_timers.get(run_id) is asyncio.current_task():
                _local_timers.pop(run_id, None)

    _local_timers[run_id] = asyncio.create_task(_wait())
    return {"status": "success", "local": True}


def cancel(run_id: str) -> None:
    task = _local_timers.pop(run_id, None)
    if task is not None:
        task.cancel()


async def shutdown() -> None:
    tasks = list(_local_timers.values())
    _local_timers.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        with contextlib.suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)


def timer_count() -> int:
    return len(_local_timers)

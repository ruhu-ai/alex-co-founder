"""Durable, lease-fenced delivery of committed founder-session wakes."""

from __future__ import annotations

from typing import Awaitable, Callable

from services import firestore

WakeFn = Callable[[str, str, str, dict], Awaitable[None]]


async def deliver(founder_id: str, delivery_id: str,
                  wake: WakeFn) -> dict:
    """Deliver one receipt; failures remain visible and retryable."""
    claim = await firestore.claim_wake_delivery(founder_id, delivery_id)
    if claim.get("duplicate"):
        return claim
    if not claim.get("claimed"):
        return claim
    delivery = claim["delivery"]
    try:
        await wake(founder_id, delivery["session_id"], delivery["notice"],
                   dict(delivery.get("state_delta") or {}))
    except Exception:
        finished = await firestore.finish_wake_delivery(
            founder_id, delivery_id, claim["lease_owner"], delivered=False,
            error_code="dispatch_failed")
        if finished.get("delivery_status") == "DEAD_LETTER":
            return {**finished, "status": "error", "error": True,
                    "error_code": "delivery_dead_letter",
                    "message": "wake delivery exhausted its retry budget"}
        return {"status": "error", "error": True,
                "error_code": "dispatch_failed",
                "retryable": True,
                "delivery_id": delivery_id,
                "message": "wake delivery failed and remains queued for retry"}
    return await firestore.finish_wake_delivery(
        founder_id, delivery_id, claim["lease_owner"], delivered=True)

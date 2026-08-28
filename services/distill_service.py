"""Distiller service (docs/06, 07).

The interactive path is SYNCHRONOUS: record_feedback awaits run_distillation so
the new rule exists before the next draft — the money shot never races a queue.
The distill runner is attached lazily (needs ADC for the model call); when it
is unavailable the feedback row stays distilled=false for the admin retry
route, and the error surfaces as data.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_distill_runner = None
_session_service = None


def attach(runner, session_service) -> None:
    """Called once at server startup (app/main.py)."""
    global _distill_runner, _session_service
    _distill_runner = runner
    _session_service = session_service


async def run_distillation(feedback_id: str, session_service=None,
                           founder_id: str = "") -> dict[str, Any]:
    """Run the distiller agent on one feedback record (isolated session)."""
    session_service = session_service or _session_service
    if _distill_runner is None or session_service is None:
        return {"status": "error", "error": True,
                "message": "distill runner not attached (needs ADC at startup)"}
    from google.genai import types

    from services import firestore

    record = await firestore.get_feedback(feedback_id, founder_id)
    if not record:
        return {"status": "error", "error": True, "message": f"feedback {feedback_id} not found"}
    if record.get("distilled"):
        return {"status": "success", "skipped": "already distilled"}

    founder_id = record["founder_id"]
    # Snapshot the founder's existing voice-rule ids so we can attribute exactly
    # which rules THIS feedback produced — the distiller applies the rule via
    # apply_profile_update but is not relied on to also call mark_distilled
    # (the model frequently applies the rule and forgets the bookkeeping, which
    # left every feedback row distilled=false and distilled_rule_ids empty).
    before_profile = await firestore.get_profile(founder_id) or {}
    before_rule_ids = {r.get("id") for r in before_profile.get("voice_rules", [])
                       if r.get("id")}

    session_id = f"distill-{feedback_id[:12]}"
    message = (
        f"Distill feedback {feedback_id}: "
        + json.dumps({
            "type": record["type"],
            "original": record["original"],
            "edited_text": record.get("edited_text", ""),
            "reason": record.get("reason", ""),
        })
    )
    try:
        try:
            # Deterministic session id: a retry after a mid-run failure must not
            # raise "session already exists" — treat an existing session as fine.
            await session_service.create_session(
                app_name="co_founder_distill", user_id="system",
                session_id=session_id,
                state={"feedback_id": feedback_id,
                       "founder_id": record["founder_id"]},
            )
        except Exception:
            pass
        async for event in _distill_runner.run_async(
            user_id="system",
            session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=message)]),
        ):
            # ADK events can contain the founder's verbatim feedback and model
            # output. Standard logs carry routing metadata only.
            logger.info(json.dumps({
                "severity": "INFO",
                "message": "distill runner event",
                "event": "distill_runner_event",
                "session_id": session_id,
                "author": getattr(event, "author", None),
                "invocation_id": getattr(event, "invocation_id", None),
                "part_count": len(getattr(getattr(event, "content", None),
                                          "parts", None) or []),
            }))
    except Exception as exc:  # surfaced as data; feedback row stays distilled=false
        logger.warning("distillation failed for %s (%s)", feedback_id,
                       type(exc).__name__)
        return {"status": "error", "error": True,
                "error_code": "distillation_failed",
                "message": "distillation failed; the feedback remains queued for retry"}

    # The run succeeded — record which rules it learned and flip the row so it is
    # never re-distilled. Deterministic (a diff of the profile), not model-driven.
    # Bookkeeping failure must not fail an otherwise-successful distillation.
    new_rule_ids: list[str] = []
    try:
        after_profile = await firestore.get_profile(founder_id) or {}
        new_rule_ids = sorted(
            r["id"] for r in after_profile.get("voice_rules", [])
            if r.get("id") and r["id"] not in before_rule_ids)
        fresh = await firestore.get_feedback(feedback_id, founder_id)
        if fresh and not fresh.get("distilled"):
            await firestore.mark_distilled(feedback_id, new_rule_ids, founder_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mark_distilled bookkeeping failed for %s: %s", feedback_id, exc)
    return {"status": "success", "feedback_id": feedback_id, "rule_ids": new_rule_ids}

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


async def run_distillation(feedback_id: str, session_service=None) -> dict[str, Any]:
    """Run the distiller agent on one feedback record (isolated session)."""
    session_service = session_service or _session_service
    if _distill_runner is None or session_service is None:
        return {"status": "error", "error": True,
                "message": "distill runner not attached (needs ADC at startup)"}
    from google.genai import types

    from services import firestore

    record = await firestore.get_feedback(feedback_id)
    if not record:
        return {"status": "error", "error": True, "message": f"feedback {feedback_id} not found"}
    if record.get("distilled"):
        return {"status": "success", "skipped": "already distilled"}

    session_id = f"distill-{feedback_id[:12]}"
    await session_service.create_session(
        app_name="co_founder_distill", user_id="system", session_id=session_id,
        state={"feedback_id": feedback_id, "founder_id": record["founder_id"]},
    )
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
        async for event in _distill_runner.run_async(
            user_id="system",
            session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=message)]),
        ):
            logger.info(json.dumps({"severity": "INFO", "message": f"distill event: {event}",
                                    "event": "distill_runner_event", "session_id": session_id}))
    except Exception as exc:  # surfaced as data; feedback row stays distilled=false
        logger.warning("distillation failed for %s: %s", feedback_id, exc)
        return {"status": "error", "error": True, "message": f"distillation failed: {exc}"}
    return {"status": "success", "feedback_id": feedback_id}

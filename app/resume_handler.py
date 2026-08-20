"""Resume handler (docs/07): hydrate the persisted session, apply the state
transition via state_delta BEFORE the next inference, then wake the agent.

Pattern verified against the reference repo (new-hire-onboarding) and ADK 2.7.1
(docs/verification-notes.md).
"""

import json
import logging

from google.adk.runners import Runner
from google.genai import types

logger = logging.getLogger(__name__)


class ResumeHandler:
    def __init__(self, runner: Runner):
        self.runner = runner

    def _log(self, severity: str, message: str, **kwargs) -> None:
        logger.info(json.dumps({"severity": severity, "message": message, **kwargs}))

    async def wake(self, *, user_id: str, session_id: str, notice: str,
                   state_delta: dict) -> None:
        """Hydrate, transition, resume — in that order.

        Args:
            user_id: Session owner (founder id, or "system" for task runs).
            session_id: The parked session to resume — never a fresh one.
            notice: Short factual wake notice, e.g.
                "Resume: portal confirmed submission MP-1042."
            state_delta: Transition applied before the next inference call.
        """
        self._log("INFO", f"wake received: {notice}", event="webhook_received",
                  session_id=session_id, user_id=user_id)
        try:
            async for event in self.runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=types.Content(
                    role="user", parts=[types.Part.from_text(text=notice)]
                ),
                state_delta=state_delta,
            ):
                self._log("INFO", f"wake event: {event}", event="runner_event",
                          session_id=session_id)
            self._log("INFO", "wake turn completed", event="runner_turn_success",
                      session_id=session_id)
        except Exception as exc:
            self._log("ERROR", f"wake turn failed: {exc}", event="runner_turn_failure",
                      session_id=session_id, error=str(exc))
            raise

"""Resume handler (docs/07): hydrate the persisted session, apply the state
transition via state_delta BEFORE the next inference, then wake the agent.

Pattern verified against the reference repo (new-hire-onboarding) and ADK 2.7.1
(docs/verification-notes.md).
"""

import json
import logging

from google.adk.runners import Runner
from google.genai import types

try:
    from google.adk.errors import StaleSessionError
except ImportError:  # ADK 2.7 ships it under a private module
    from google.adk.errors._stale_session_error import StaleSessionError

logger = logging.getLogger(__name__)

# Wake notices are system-authored user turns. Tag them with an invisible
# marker so the transcript view (app.main.chat_history) hides them by TAG, not
# by matching a visible "System:/Resume:" text prefix — which would also hide a
# founder message that legitimately starts with those words (docs/16). The
# marker is a zero-width code point no human types; the model still reads the
# notice text unchanged.
SYSTEM_NOTICE_MARKER = "⁣"  # INVISIBLE SEPARATOR


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
        # Metadata only — the notice body can carry untrusted email subjects /
        # excerpts (docs/12 §logging), so log its length, never its text.
        self._log("INFO", "wake received", event="webhook_received",
                  session_id=session_id, user_id=user_id, notice_len=len(notice))
        message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=SYSTEM_NOTICE_MARKER + notice)])
        delivered = False  # the notice was appended to the session at least once
        for attempt in (1, 2):
            try:
                async for event in self.runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    # On the retry, if a downstream append already committed the
                    # notice (any event was yielded before the stale error),
                    # resume WITHOUT re-appending it — otherwise the founder
                    # sees the wake, and its state_delta, applied twice.
                    new_message=None if delivered else message,
                    state_delta={} if delivered else state_delta,
                ):
                    delivered = True
                    # Metadata only — full events carry model/tool content
                    # (scraped pages, email excerpts, PII) that must not land
                    # in logs (docs/12).
                    self._log("INFO", "wake event", event="runner_event",
                              session_id=session_id,
                              author=getattr(event, "author", ""),
                              invocation_id=getattr(event, "invocation_id", ""))
                self._log("INFO", "wake turn completed", event="runner_turn_success",
                          session_id=session_id)
                return
            except StaleSessionError:
                # A concurrent/cancelled run modified the stored session after
                # this runner loaded it. One retry reloads it from storage.
                if attempt == 2:
                    raise
                self._log("INFO", "stale session — reloading and retrying once",
                          event="stale_session_retry", session_id=session_id)
            except Exception as exc:
                self._log("ERROR", f"wake turn failed: {exc}", event="runner_turn_failure",
                          session_id=session_id, error=str(exc))
                raise

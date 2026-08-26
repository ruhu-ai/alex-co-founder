"""Stale-session retry in the resume handler (post-feedback wake path)."""

import pytest

from app.resume_handler import ResumeHandler, StaleSessionError

pytestmark = pytest.mark.asyncio


class _FakeRunner:
    """Fails with `exc` for the first `failures` invocations. Captures every
    call's kwargs — a fake that swallows them would pass even if the handler
    dropped state_delta, the single most load-bearing argument of the resume
    path."""

    def __init__(self, failures: int = 0, exc=StaleSessionError("stale")):
        self.failures = failures
        self.exc = exc
        self.calls = 0
        self.kwargs: list[dict] = []

    def run_async(self, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)

        async def _gen():
            if self.calls <= self.failures:
                raise self.exc
            if False:  # pragma: no cover - makes this an async generator
                yield None

        return _gen()


async def test_wake_retries_once_after_stale_session():
    runner = _FakeRunner(failures=1)
    handler = ResumeHandler(runner)
    await handler.wake(user_id="founder", session_id="s-1",
                       notice="Resume: founder reviewed a section.",
                       state_delta={"pending_signals": []})
    assert runner.calls == 2


async def test_wake_raises_after_repeated_stale_session():
    runner = _FakeRunner(failures=2)
    handler = ResumeHandler(runner)
    with pytest.raises(StaleSessionError):
        await handler.wake(user_id="founder", session_id="s-1",
                           notice="Resume: founder reviewed a section.",
                           state_delta={"pending_signals": []})
    assert runner.calls == 2


async def test_wake_forwards_the_full_contract_to_the_runner():
    """state_delta applied before inference, correct session, the notice as
    the user message — the docs/07 resume contract, asserted end to end."""
    runner = _FakeRunner()
    handler = ResumeHandler(runner)
    delta = {"pending_signals": [], "current_step": "SUBMITTED"}
    await handler.wake(user_id="founder", session_id="s-42",
                       notice="Resume: portal confirmed submission MP-1042.",
                       state_delta=delta)
    kw = runner.kwargs[0]
    assert kw["user_id"] == "founder"
    assert kw["session_id"] == "s-42"
    assert kw["state_delta"] == delta
    assert "MP-1042" in kw["new_message"].parts[0].text


async def test_non_stale_errors_propagate_without_retry():
    runner = _FakeRunner(failures=1, exc=RuntimeError("model quota exhausted"))
    handler = ResumeHandler(runner)
    with pytest.raises(RuntimeError):
        await handler.wake(user_id="founder", session_id="s-1",
                           notice="Resume: x", state_delta={})
    assert runner.calls == 1  # no blind retry on non-stale failures

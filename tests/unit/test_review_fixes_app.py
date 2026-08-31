"""New-behaviour tests for app-server and frontend review fixes.

Scoped to telemetry and the resume handler plus the app.main filename
sanitizer and the ?key= bootstrap redirect.
The heavier app.main behaviours already have coverage in test_app_server.py.
"""

import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.resume_handler import StaleSessionError

# ---------------------------------------------------------------------------
# telemetry gate (finding 16)
# ---------------------------------------------------------------------------

class TestTelemetryGate:
    _OTEL = ("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
             "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH",
             "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY")

    def _clear(self, monkeypatch):
        for k in self._OTEL:
            monkeypatch.delenv(k, raising=False)

    def test_logs_bucket_alone_enables_no_content_upload(self, monkeypatch):
        from app.app_utils.telemetry import setup_telemetry
        self._clear(monkeypatch)
        monkeypatch.setenv("LOGS_BUCKET_NAME", "my-logs")
        assert setup_telemetry() == "my-logs"
        assert os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] == "NO_CONTENT"
        assert os.environ["OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH"].startswith(
            "gs://my-logs/")

    def test_disabled_without_bucket(self, monkeypatch):
        from app.app_utils.telemetry import setup_telemetry
        self._clear(monkeypatch)
        monkeypatch.delenv("LOGS_BUCKET_NAME", raising=False)
        assert setup_telemetry() is None
        assert os.environ.get(
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT") != "NO_CONTENT"


# ---------------------------------------------------------------------------
# resume handler (findings 12, 14)
# ---------------------------------------------------------------------------

class _ScriptRunner:
    """run_async replays a per-attempt script and records every call's kwargs."""

    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.kwargs = []

    def run_async(self, **kwargs):
        self.kwargs.append(kwargs)
        mode = self.script[self.calls] if self.calls < len(self.script) else "ok"
        self.calls += 1

        async def _gen():
            if mode == "yield_then_stale":
                yield SimpleNamespace(author="model", invocation_id="i1")
                raise StaleSessionError("stale")
            if mode == "stale":
                raise StaleSessionError("stale")
            if False:  # pragma: no cover - make this an async generator
                yield None

        return _gen()


class TestResumeHandlerNotice:
    async def test_notice_is_tagged_with_the_invisible_marker(self):
        from app.resume_handler import SYSTEM_NOTICE_MARKER, ResumeHandler
        runner = _ScriptRunner(["ok"])
        await ResumeHandler(runner).wake(
            user_id="founder", session_id="s1",
            notice="System: deadline scan.", state_delta={"x": 1})
        text = runner.kwargs[0]["new_message"].parts[0].text
        assert text.startswith(SYSTEM_NOTICE_MARKER)
        assert "System: deadline scan." in text

    async def test_retry_does_not_re_append_a_delivered_notice(self):
        from app.resume_handler import ResumeHandler
        runner = _ScriptRunner(["yield_then_stale", "ok"])
        await ResumeHandler(runner).wake(
            user_id="founder", session_id="s1",
            notice="Resume: portal confirmed MP-1042.",
            state_delta={"current_step": "SUBMITTED"})
        assert runner.calls == 2
        # first attempt appended the notice + delta
        assert runner.kwargs[0]["new_message"] is not None
        assert runner.kwargs[0]["state_delta"] == {"current_step": "SUBMITTED"}
        # retry after the notice already landed: no second append, no re-delta
        assert runner.kwargs[1]["new_message"] is None
        assert runner.kwargs[1]["state_delta"] == {}

    async def test_stale_before_delivery_still_re_sends_the_notice(self):
        from app.resume_handler import ResumeHandler
        runner = _ScriptRunner(["stale", "ok"])
        await ResumeHandler(runner).wake(
            user_id="founder", session_id="s1", notice="Resume: x",
            state_delta={"pending_signals": []})
        assert runner.calls == 2
        # nothing was delivered on attempt 1, so the retry re-appends it
        assert runner.kwargs[1]["new_message"] is not None
        assert runner.kwargs[1]["state_delta"] == {"pending_signals": []}

    async def test_notice_body_is_not_logged(self, caplog):
        import logging

        from app.resume_handler import ResumeHandler
        secret = "SENSITIVE-SUBJECT-9f3a"
        runner = _ScriptRunner(["ok"])
        with caplog.at_level(logging.INFO):
            await ResumeHandler(runner).wake(
                user_id="founder", session_id="s1",
                notice=f"Resume: {secret}", state_delta={})
        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert secret not in blob
        assert "webhook_received" in blob and "notice_len" in blob


# ---------------------------------------------------------------------------
# app.main: filename sanitizer + ?key= bootstrap redirect (findings 1, 15)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def appmod():
    os.environ.pop("APP_AUTH_TOKEN", None)
    os.environ.pop("K_SERVICE", None)
    import app.main as m
    return m


class TestFilenameSanitizer:
    def test_path_traversal_is_stripped(self, appmod):
        f = appmod._safe_filename_component
        assert f("../../etc/passwd") == "passwd"
        assert f("a/b/c.docx") == "c.docx"
        assert "/" not in f("../../x") and ".." not in f("../../x")

    def test_dotfiles_and_bare_dots_fall_back(self, appmod):
        f = appmod._safe_filename_component
        assert f("..") == "upload"
        assert f("", fallback="document") == "document"
        assert f(None) == "upload"

    def test_disallowed_chars_become_underscores_extension_kept(self, appmod):
        f = appmod._safe_filename_component
        assert f("my deck.pdf") == "my_deck.pdf"
        assert f("evil;rm -rf.txt") == "evil_rm_-rf.txt"


class TestKeyBootstrapRedirect:
    @pytest.fixture(autouse=True)
    def _token(self, monkeypatch):
        monkeypatch.setenv("APP_AUTH_TOKEN", "t0ken")
        monkeypatch.delenv("K_SERVICE", raising=False)

    def test_html_navigation_redirects_and_strips_the_key(self, appmod):
        client = TestClient(appmod.app, follow_redirects=False)
        r = client.get("/", params={"key": "t0ken"},
                       headers={"accept": "text/html"})
        assert r.status_code == 303
        assert "key" not in r.headers["location"]
        assert r.cookies.get("app_auth") == "t0ken"

    def test_programmatic_key_sets_cookie_without_a_redirect(self, appmod):
        # Accept: */* (no HTML nav) keeps the set-cookie-only path — this is the
        # existing bootstrap contract test_app_server.py relies on.
        client = TestClient(appmod.app, follow_redirects=False)
        r = client.get("/api/config", params={"key": "t0ken"})
        assert r.status_code == 200
        assert r.cookies.get("app_auth") == "t0ken"

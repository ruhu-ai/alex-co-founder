"""Activity line and return digest UI contract (docs/24 §8, §12).

Static contracts over app/static/index.html, matching the house style of the
other *_ui_contract tests. These lock the behaviours no server test can see.
"""

from __future__ import annotations

import pathlib
import re

UI = (pathlib.Path(__file__).resolve().parents[2]
      / "app/static/index.html").read_text(encoding="utf-8")


def _fn(name):
    """The body of one top-level function, for scoped assertions."""
    return UI.split(f"function {name}(")[1].split("\n}")[0]


class TestMotionIsHonest:
    def test_an_open_wait_renders_a_static_ring_not_a_spinner(self):
        body = _fn("waitRow")
        # world/timer waits pick the dashed ring; only a live run spins.
        assert '"ring"' in body
        assert 'w.wait_kind === "discovery_running" ? "spin"' in body

    def test_the_ring_has_no_animation(self):
        css = UI.split(".act-glyph.ring {")[1].split("}")[0]
        assert "animation" not in css
        assert "dashed" in css

    def test_only_the_spinner_and_attn_dot_animate(self):
        animated = re.findall(r"\.act-glyph\.(\w+)[^{]*\{[^}]*animation:", UI)
        assert set(animated) <= {"spin", "attn"}

    def test_reduced_motion_stops_both(self):
        block = UI.split("@media (prefers-reduced-motion: reduce) {")[1].split("\n}")[0]
        assert ".act-glyph.spin" in block and ".act-glyph.attn" in block


class TestClocks:
    def test_no_elapsed_counter_under_the_threshold(self):
        assert re.search(r"ACT_ELAPSED_THRESHOLD_MS\s*=\s*(?:5000|5e3)", UI)
        body = _fn("actRender")
        assert "elapsed >= ACT_ELAPSED_THRESHOLD_MS" in body

    def test_stop_replaces_send_at_the_same_threshold(self):
        body = _fn("actRender")
        assert "showStopControl(true)" in body
        assert "elapsed >= ACT_ELAPSED_THRESHOLD_MS" in body

    def test_open_waits_never_render_elapsed_time(self):
        body = _fn("waitRow")
        assert "act-time" not in body
        assert "fmtElapsed" not in body

    def test_waits_render_the_next_check_promise(self):
        assert "next_check_action" in _fn("waitRow")


class TestGrace:
    def test_appearing_is_delayed_and_disappearing_is_not(self):
        assert "ACT_SHOW_GRACE_MS = 500" in UI
        assert "setTimeout" in _fn("actStartWorking")
        stop = _fn("actStop")
        assert "clearTimeout(actGraceTimer)" in stop
        assert "setTimeout" not in stop


class TestDigest:
    def test_blocked_on_you_renders_first(self):
        body = _fn("renderDigest")
        assert body.index("for (const w of blocked)") < body.index("for (const c of changed")

    def test_dismissal_is_local_only(self):
        block = UI.split("data-digest-dismiss]")[1].split("return;")[0]
        assert 'innerHTML = ""' in block
        assert "api(" not in block and "fetch(" not in block

    def test_digest_reads_on_load_focus_and_visibility_but_never_polls(self):
        assert "await restoreSession()" in UI
        boot = _fn("boot")
        assert boot.index("await loadIdentity()") < boot.index("await startAuthorizedApp()")
        assert 'window.addEventListener("focus", refreshWaiting)' in UI
        assert "visibilitychange" in UI
        # No timer may drive a network read.
        for m in re.finditer(r"setInterval\(([^,]+),", UI):
            target = m.group(1)
            assert "refreshWaiting" not in target
            assert "api(" not in target

    def test_waiting_failure_never_breaks_the_page(self):
        body = _fn("refreshWaiting")
        assert "catch" in body and "return" in body

    def test_unreleased_workspace_brief_is_not_requested(self):
        body = _fn("refreshWaiting")
        assert "await appConfigReady" in body
        assert "if (!workspaceBriefSurfaceEnabled)" in body
        assert body.index("if (!workspaceBriefSurfaceEnabled)") < body.index(
            "/api/v1/workspace-brief"
        )


class TestReceipt:
    def test_the_finished_turn_collapses_into_a_receipt(self):
        body = _fn("actFinish")
        assert 'dataset.tone = "done"' in body
        assert 'dataset.open = "false"' in body
        assert "data-act-toggle" in body

    def test_a_turn_with_no_trace_leaves_nothing_behind(self):
        body = _fn("actFinish")
        assert "if (!steps.length) return;" in body

    def test_receipt_rows_are_escaped(self):
        assert "esc(" in _fn("stepRow")
        assert "esc(" in _fn("waitRow")

    def test_toggle_tracks_aria_expanded(self):
        block = UI.split("data-act-toggle]")[1].split("return;")[0]
        assert 'setAttribute("aria-expanded"' in block


class TestAccessibilityAndTokens:
    def test_the_live_line_announces_politely(self):
        assert 'setAttribute("aria-live", "polite")' in _fn("actRender")

    def test_glyph_slot_is_fixed_width(self):
        css = UI.split(".act-glyph {")[1].split("}")[0]
        assert "width: 14px" in css and "height: 14px" in css

    def test_touch_target_on_the_row(self):
        css = UI.split(".act-head {")[1].split("}")[0]
        assert "min-height: 44px" in css

    def test_styles_use_semantic_tokens_only(self):
        for selector in (".act {", ".digest {", ".act-verb {", ".act-promise {"):
            block = UI.split(selector)[1].split("}")[0]
            assert not re.search(r"#[0-9A-Fa-f]{3,6}\b", block), selector


class TestNoResidue:
    """Self-review regressions: both of these shipped broken and were caught
    before merge."""

    def test_abort_clears_the_stop_control(self):
        assert "showStopControl(false)" in _fn("actStop")

    def test_switching_conversations_kills_the_live_line(self):
        # actRender re-creates its element when missing, so a running timer
        # would otherwise resurrect the line inside the newly-opened log.
        for fn in ("viewSession", "backToSession", "resumeSession", "newSession"):
            assert "actStop();" in _fn(fn), fn

    def test_a_live_voice_turn_never_starts_the_line(self):
        """A mid-call typed message goes over the live socket and returns; it
        must not arm the activity line, which the socket would never finish."""
        body = _fn("send")
        voice_at = body.index("if (voice) {")
        # The branch returns before any activity work is armed.
        assert "voice.ws.send" in body[voice_at:body.index("actStartWorking()")]
        assert "return;" in body[voice_at:body.index("setThinking(true)")]
        assert voice_at < body.index("actStartWorking()")

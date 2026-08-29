"""Executable contracts for live-voice startup and adjacent audio features."""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parents[2] / "app" / "static" / "index.html"
HTML = INDEX.read_text(encoding="utf-8")
NODE = shutil.which("node")


def _extract(name: str) -> str:
    marker = f"function {name}("
    marker_start = HTML.index(marker)
    start = marker_start - 6 if HTML[marker_start - 6:marker_start] == "async " else marker_start
    depth = 0
    opening = HTML.index(") {", marker_start) + 2
    for index in range(opening, len(HTML)):
        if HTML[index] == "{":
            depth += 1
        elif HTML[index] == "}":
            depth -= 1
            if depth == 0:
                return HTML[start:index + 1]
    raise AssertionError(f"unbalanced braces extracting {name}()")


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_socket_close_during_get_user_media_cleans_track_and_never_listens():
    functions = "\n".join(_extract(name) for name in (
        "liveStartupCurrent",
        "cleanupLiveMicrophone",
        "ensureLiveStartupCurrent",
        "liveMicrophoneActive",
        "attachLiveMicrophone",
        "toggleVoice",
    ))
    script = textwrap.dedent(f"""
        let voice = null;
        let liveUi = {{lifecycle: "OFF", speaking: false, energyTarget: 0}};
        const WebSocket = {{OPEN: 1}};
        let resolveMedia;
        let stopped = 0;
        let contextsCreated = 0;
        const track = {{
          enabled: true,
          readyState: "live",
          stop() {{ stopped += 1; this.readyState = "ended"; }}
        }};
        const stream = {{
          getTracks() {{ return [track]; }},
          getAudioTracks() {{ return [track]; }}
        }};
        const navigator = {{mediaDevices: {{getUserMedia() {{
          return new Promise(resolve => {{ resolveMedia = resolve; }});
        }}}}}};
        class AudioContext {{ constructor() {{ contextsCreated += 1; }} }}
        class AudioWorkletNode {{}}
        const classes = new Set();
        const elements = {{talkBtn: {{
          disabled: false,
          classList: {{
            add(...names) {{ names.forEach(name => classes.add(name)); }},
            remove(...names) {{ names.forEach(name => classes.delete(name)); }}
          }}
        }}}};
        function $(id) {{ return elements[id]; }}
        function requireActiveContext() {{ return true; }}
        function renderVoiceCloud() {{}}
        const toasts = [];
        function toast(message, tone) {{ toasts.push({{message, tone}}); }}
        function closeLiveConnection() {{ throw new Error("stale connection must already be gone"); }}
        function b64encode() {{ return ""; }}
        const current = {{
          ws: {{readyState: WebSocket.OPEN, send() {{}}}},
          voiceLifecycle: true,
          micCtx: null,
          micStream: null,
          node: null
        }};
        async function ensureLiveConnection() {{ voice = current; return current; }}
        {functions}
        (async () => {{
          const starting = toggleVoice();
          await new Promise(resolve => setImmediate(resolve));
          voice = null;
          current.ws.readyState = 3;
          resolveMedia(stream);
          await starting;
          process.stdout.write(JSON.stringify({{
            lifecycle: liveUi.lifecycle,
            stopped,
            contextsCreated,
            micStream: current.micStream,
            disabled: elements.talkBtn.disabled,
            liveClass: classes.has("live"),
            successToast: toasts.some(item => item.tone === "ok"),
            errorToast: toasts.some(item => item.tone === "danger")
          }}));
        }})().catch(error => {{ console.error(error); process.exit(1); }});
    """)

    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome == {
        "lifecycle": "TERMINAL_ERROR",
        "stopped": 1,
        "contextsCreated": 0,
        "micStream": None,
        "disabled": False,
        "liveClass": False,
        "successToast": False,
        "errorToast": True,
    }


def test_message_read_aloud_and_voice_note_contracts_remain_separate():
    message = _extract("addMsg")
    read_aloud = _extract("speakText")
    assert 'b.title = "Read aloud"' in message
    assert 'b.setAttribute("aria-label", "Read aloud")' in message
    assert "speakText(text, b)" in message
    assert 'appFetch("/api/v1/tts"' in read_aloud

    assert 'id="micBtn" title="Record a voice note"' in HTML
    assert "new MediaRecorder(stream)" in HTML
    assert 'appFetch("/api/v1/voice-notes"' in HTML


def test_unexpected_live_close_reconciles_durable_history():
    closed = _extract("handleLiveClosed")

    assert "chatCount = 0;" in closed
    assert "refresh();" in closed
    assert closed.index("chatCount = 0;") < closed.index("refresh();")


def test_pause_alex_is_explicit_accessible_and_separate_from_call_end():
    ensure_control = _extract("ensureVoicePauseControl")
    render = _extract("renderVoiceCloud")
    pause = _extract("pauseVoice")
    resume = _extract("resumeVoice")
    stop = _extract("stopVoice")

    assert "button.onclick = toggleVoicePause" in ensure_control
    assert 'pauseButton.setAttribute("aria-pressed", String(paused))' in render
    assert 'paused ? "Resume Alex" : "Pause Alex"' in render
    assert 'await requestVoiceControl("pause")' in pause
    assert 'if (visual) stopVision("paused")' in pause
    assert "cleanupLiveMicrophone(current)" in pause
    assert "stopLivePlayback(current)" in pause
    assert "clearLiveCaptions(true)" in pause
    assert 'await requestVoiceControl("resume")' in resume
    assert "await attachLiveMicrophone()" in resume
    assert 'toast("Alex remains paused because the microphone could not resume."' in resume
    assert "closeLiveConnection" in stop


def test_pause_during_microphone_startup_invalidates_the_startup_generation():
    startup_current = _extract("liveStartupCurrent")

    assert "!current.paused" in startup_current
    assert "!liveUi.paused" in startup_current


def test_hold_keeps_capture_connected_but_fences_audio_output_and_camera():
    hold = _extract("holdVoice")
    resume = _extract("resumeAttention")
    render = _extract("renderVoiceCloud")
    attach = _extract("attachLiveMicrophone")

    assert 'liveUi.held = true' in hold
    assert 'current.held = true' in hold
    assert 'if (visual) stopVision("paused")' in hold
    assert "cleanupLiveMicrophone" not in hold
    assert "stopLivePlayback(current)" in hold
    assert "clearLiveCaptions(true)" in hold
    assert 'await requestVoiceControl("hold")' in hold
    assert 'await requestVoiceControl("attention.resume")' in resume
    assert "attachLiveMicrophone" not in resume
    assert 'holdButton.setAttribute("aria-pressed", String(liveUi.held))' in render
    assert 'if (liveUi.held || current.held) return' in attach


def test_hold_has_no_browser_speech_recognition_or_spoken_command_parser():
    attention = "\n".join(_extract(name) for name in (
        "holdVoice", "resumeAttention", "toggleVoiceHold",
    ))
    assert "SpeechRecognition" not in attention
    assert "webkitSpeechRecognition" not in attention
    assert "captionState" not in attention
    assert "transcript" not in attention.lower()


def test_pause_and_connection_loss_clear_attention_hold_fail_closed():
    pause = _extract("pauseVoice")
    close = _extract("closeLiveConnection")
    disconnected = _extract("handleLiveClosed")

    assert "liveUi.held = false" in pause
    assert "current.held = false" in pause
    assert "liveUi.held = false" in close
    assert "liveUi.held = false" in disconnected

"""Static founder-surface contracts for explicit Live visual sharing."""

import re
from pathlib import Path

from google.genai import types

from app.live import LatestVisualLiveRequestQueue

HTML = (Path(__file__).resolve().parents[2] / "app/static/index.html").read_text()
ORB_CSS = (Path(__file__).resolve().parents[2] / "app/static/alex-voice-orb.css").read_text()
ORB_JS = (Path(__file__).resolve().parents[2] / "app/static/alex-voice-orb.js").read_text()
LIVE = (Path(__file__).resolve().parents[2] / "app/live.py").read_text()


def squashed(value: str) -> str:
    return re.sub(r"\s+", "", value)


HTML_SQUASHED = squashed(HTML)


def test_capture_is_gesture_bound_and_never_resumes_automatically():
    assert "async function beginVision(source)" in HTML
    assert "navigator.mediaDevices.getUserMedia({video:true,audio:false})" in HTML_SQUASHED
    assert "navigator.mediaDevices.getDisplayMedia({video:true,audio:false})" in HTML_SQUASHED
    assert 'stopVision("suspended")' in HTML
    assert 'stopVision("page_teardown")' in HTML
    assert "if (voice) closeLiveConnection();" in HTML
    assert "function resumeVision()" in HTML
    assert 'type: "media.prepare"' in HTML
    assert "localStorage.setItem" not in HTML[HTML.index("let consentGrants"):HTML.index("function renderVoiceCloud")]


def test_camera_permission_result_is_cancelled_if_live_voice_ended_or_paused():
    capture = HTML.split("async function captureVisionSource(source, grantId)", 1)[1].split(
        "function shareVision", 1,
    )[0]
    assert "const currentVoice = voice" in capture
    assert 'voice !== currentVoice || liveUi.lifecycle !== "ACTIVE"' in capture
    assert "!liveMicrophoneActive(currentVoice)" in capture
    assert "stream.getTracks().forEach(track => track.stop())" in capture
    assert 'track.readyState !== "live"' in capture


def test_local_camera_pilot_cannot_promote_a_live_frame_to_an_attachment():
    capture = HTML.split("async function captureVisionSource(source, grantId)", 1)[1].split(
        "function shareVision", 1,
    )[0]
    assert '$("visionCaptureBtn").hidden = source === "camera"' in capture
    assert "uploadAttachmentFile" not in capture
    still = HTML.split("async function captureVisionStill()", 1)[1].split(
        'document.addEventListener("visibilitychange"', 1,
    )[0]
    assert 'visual.source === "camera"' in still


def test_stale_visual_share_stops_local_capture_without_ending_voice():
    frames = HTML.split("function onVoiceFrame(evt)", 1)[1].split(
        "function b64encode", 1,
    )[0]
    sampling = HTML.split("function startVisionSampling()", 1)[1].split(
        "function pauseVision", 1,
    )[0]
    stop = HTML.split('function stopVision(reason = "user_stop", notifyServer = true)', 1)[1].split(
        "async function captureVisionStill", 1,
    )[0]

    assert 'frame.type === "media.stopped"' in frames
    assert 'stopVision(frame.end_reason || "budget_exhausted", false)' in frames
    assert '"stale_generation", "visual_context_limit"' in frames
    assert 'toast("Camera sharing ended because the app no longer has an active visual share.' in frames
    assert "closeLiveConnection" not in frames.split(
        '"stale_generation", "visual_context_limit"', 1,
    )[1].split("return;", 1)[0]
    assert "visual === current" in sampling
    assert 'current.track.readyState !== "live"' in sampling
    assert 'current.state = "stopped"' in stop


def test_camera_has_no_short_auto_expiry_but_all_lifecycle_stops_remain():
    media = (Path(__file__).resolve().parents[2] / "services/live_media.py").read_text()
    camera_guard = 'active["source"] != VisualSource.CAMERA.value'
    assert media.count(camera_guard) >= 2
    assert 'source != VisualSource.CAMERA.value and remaining < MIN_USEFUL_SECONDS' in media
    assert 'stopVision("paused")' in HTML
    assert 'stopVision("track_ended")' in HTML
    assert 'stopVision("suspended")' in HTML
    assert 'stopVision("page_teardown")' in HTML
    assert 'stopVision("socket_lost", false)' in HTML
    assert 'current.state = "stopped"' in HTML


def test_live_visual_launch_controls_require_an_active_voice_call():
    camera = HTML.split('id="cameraBtn"', 1)[1].split("</button>", 1)[0]
    display = HTML.split('id="displayBtn"', 1)[1].split("</button>", 1)[0]
    assert "requires an active voice call" in camera.lower() and "disabled" in camera
    assert "requires an active voice call" in display.lower() and "disabled" in display
    assert 'liveUi.lifecycle === "ACTIVE" && !liveUi.held && liveMicrophoneActive()' in HTML
    assert 'track.enabled && track.readyState === "live"' in HTML
    assert 'button.disabled = readThrough() || !voiceActive || !capabilityEnabled' in HTML
    assert 'syncVisionControlAvailability();constvisible' in HTML_SQUASHED
    begin = HTML.split("async function beginVision(source)", 1)[1].split(
        "function cancelVisionConsent", 1)[0]
    assert 'liveUi.lifecycle !== "ACTIVE" || !voice || !voice.micCtx' in begin
    assert "Start and connect a voice call before sharing camera or screen" in begin
    assert 'href="#i-camera"' in camera
    assert 'href="#i-screen-share"' in display


def test_voice_cloud_captions_and_approval_modifier_are_accessible_and_trusted():
    for state in (
        "CONNECTING", "LISTENING", "THINKING", "PROCESSING", "SPEAKING",
        "INTERRUPTED", "AWAITING_APPROVAL", "RECONNECTING", "ERROR", "MICROPHONE_OFF",
    ):
        assert state in HTML or state in ORB_JS
    assert "@media (prefers-reduced-motion: reduce)" in ORB_CSS
    assert 'id="captionYou"' in HTML and 'id="captionAlex"' in HTML
    assert "activeApp.pending_approval_id" in HTML
    assert "Object.keys(gateItems || {}).length" in HTML
    assert "/approv/i.test" not in HTML
    assert 'id="alexLive" aria-label="Alex voice status" hidden' in HTML
    assert 'Math.min(2, devicePixelRatio || 1)' in HTML


def test_private_voice_op_renderer_is_isolated_cloud_like_and_voice_only():
    assert 'href="/alex-voice-orb.css"' in HTML
    assert 'src="/alex-voice-orb.js"' in HTML
    assert 'id="alexLive" aria-label="Alex voice status" hidden' in HTML
    assert "window.AlexVoiceOrb.draw" in HTML
    assert "function cloudSilhouette" in ORB_JS
    assert "Math.min(width, height) * 0.385" in ORB_JS
    assert "grid-template-columns: 152px minmax(0, 1fr)" in ORB_CSS
    assert "width: 144px" in ORB_CSS
    assert "const puffs" in ORB_JS
    assert "devicePixelRatio" not in ORB_JS  # DPR ownership stays in the shell.
    assert "getUserMedia" not in ORB_JS and "WebSocket" not in ORB_JS
    assert "localStorage" not in ORB_JS and "fetch(" not in ORB_JS
    assert "WebGL" not in ORB_JS and "speechSynthesis" not in ORB_JS
    assert not re.search(r"\b(?:star|sparkle)\b", ORB_JS, re.I)
    assert "1e3 / 30" in HTML


def test_voice_states_animate_only_internal_cloud_volume_not_outer_shape():
    silhouette = ORB_JS.split("function cloudSilhouette", 1)[1].split(
        "function rgba", 1,
    )[0]
    geometry = ORB_JS.split("function draw", 1)[1].split(
        "// Reference layer 1", 1,
    )[0]
    assert ORB_JS.count("function cloudSilhouette(ctx, cx, cy, radius)") == 1
    assert "ctx.arc(cx, cy, radius, 0, Math.PI * 2);" in silhouette
    assert "appendSmoothBlob" not in ORB_JS
    assert "mode." not in silhouette
    assert "const radius = Math.min(width, height) * 0.385;" in geometry
    assert "breath" not in geometry
    assert "edgeWisps" not in ORB_JS


def test_listening_thinking_and_speaking_have_distinct_internal_cloud_shapes():
    assert "const CLOUD_PROFILES = Object.freeze" in ORB_JS
    assert "LISTENING: { spread: 1.12" in ORB_JS
    assert "THINKING: { spread: 0.72" in ORB_JS
    assert "SPEAKING: { spread: 0.98" in ORB_JS
    assert "ctx.scale(profile.puffX, profile.puffY)" in ORB_JS
    assert "mode.cloud * profile.wave" in ORB_JS
    assert "ctx.scale(profile.coreX, profile.coreY)" in ORB_JS


def test_private_reference_signature_is_ported_without_runtime_dependency():
    # The maintained renderer carries the reference's actual recognizable
    # composition: top-lit periwinkle atmosphere, deep cobalt base, a dense
    # lower-mid cumulus bank, three mist contours, and an internal ice core.
    assert 'sky.addColorStop(0, state === "ERROR"' in ORB_JS
    assert '"rgba(17,35,112,0.99)"' in ORB_JS
    assert "const cloudBaseY = cy + radius * 0.055" in ORB_JS
    assert "[-0.55, 0.05, 0.42, 0.32, 0.0, 0.95]" in ORB_JS
    assert "for (let wave = 0; wave < 3; wave += 1)" in ORB_JS
    assert "pale ice-blue internal radiance" in ORB_JS
    assert "bottom-edge cobalt depth" in ORB_JS
    assert "version: 6" in ORB_JS
    assert "alex-voice-orb/src" not in ORB_JS
    assert "React" not in ORB_JS and "import " not in ORB_JS


def test_server_uses_separate_latest_frame_queue_and_preserves_audio_path():
    assert "class LatestVisualLiveRequestQueue" in LIVE
    assert "send_visual_latest" in LIVE
    assert "_visual_pending" in LIVE
    assert 'mime_type="audio/pcm;rate=16000"' in LIVE
    assert "CanonicalLiveSessionService" in LIVE
    assert "LIVE_MAX_SESSION_SECONDS" in LIVE
    assert "LIVE_IDLE_SECONDS" in LIVE
    assert '"session.ending"' in LIVE


async def test_latest_visual_queue_replaces_only_the_unsent_image():
    queue = LatestVisualLiveRequestQueue()
    first = queue.send_visual_latest(
        types.Blob(mime_type="image/jpeg", data=b"first"),
        source="camera", generation=1)
    second = queue.send_visual_latest(
        types.Blob(mime_type="image/jpeg", data=b"second"),
        source="camera", generation=1)

    request = await queue.get()

    assert first is False and second is True
    assert request.blob.data == b"second"


async def test_pause_fences_queued_and_new_microphone_audio_until_resume():
    queue = LatestVisualLiveRequestQueue()
    queue.send_realtime(types.Blob(
        mime_type="audio/pcm;rate=16000", data=b"already-queued"))
    queue.pause_audio()
    queue.send_realtime(types.Blob(
        mime_type="audio/pcm;rate=16000", data=b"paused"))
    queue.send_visual_latest(
        types.Blob(mime_type="image/jpeg", data=b"image"),
        source="camera", generation=1)

    # The pre-pause audio is fenced when dequeued; visual transport remains
    # independently governed by its own consent/start state.
    request = await queue.get()
    assert request.blob.mime_type == "image/jpeg"

    queue.resume_audio()
    queue.send_realtime(types.Blob(
        mime_type="audio/pcm;rate=16000", data=b"fresh"))
    request = await queue.get()
    assert request.blob.data == b"fresh"


def test_pause_and_resume_are_explicit_server_acknowledged_privacy_controls():
    assert '"voice.pause", "voice.resume"' in LIVE
    assert "queue.pause_audio()" in LIVE
    assert "queue.resume_audio()" in LIVE
    assert "transcript_buffer.clear()" in LIVE
    assert "if voice_paused or attention_held or not downstream_armed:" in LIVE
    assert 'state": "AWAITING_FRESH_AUDIO"' in LIVE


def test_attention_hold_is_a_narrow_server_enforced_gate_not_a_prompt_rule():
    assert '"voice.hold"' in LIVE
    assert '"voice.attention.resume"' in LIVE
    assert "attention_held = True" in LIVE
    assert "if voice_paused or attention_held:" in LIVE
    assert "if voice_paused or attention_held or not downstream_armed:" in LIVE
    hold_branch = LIVE.split('elif frame_type == "voice.hold":', 1)[1].split(
        'elif voice_paused:', 1,
    )[0]
    assert "queue.pause_audio()" in hold_branch
    assert "transcript_buffer.clear()" in hold_branch
    assert 'transcript.current_turn_id = ""' in hold_branch
    assert 'await media.stop("paused")' in hold_branch
    assert "instruction" not in hold_branch.lower()


def test_live_captions_are_temporary_and_never_appended_as_chat_messages():
    caption = HTML.split("function renderCaptionEvent(frame)", 1)[1].split(
        "function clearLiveCaptions", 1,
    )[0]
    assert 'row.querySelector("span").textContent = frame.text' in caption
    assert "appendChild" not in caption
    assert "createElement" not in caption
    assert "addMsg" not in caption
    assert "renderedCaptionEvents.add(frame.final_event_id)" in caption
    assert 'await send({"transcript":' not in LIVE
    assert "text = transcript_buffer.preview(speaker)" in LIVE

    for function_name in ("closeLiveConnection", "handleLiveClosed"):
        body = HTML.split(f"function {function_name}", 1)[1].split(
            "\n}", 1,
        )[0]
        assert "clearLiveCaptions(true);" in body
        assert "chatCount = 0;" in body
        assert "refresh();" in body

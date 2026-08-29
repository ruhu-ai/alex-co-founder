"""Server-owned consent, nonce, budget, and ephemeral-frame contracts."""

from __future__ import annotations

import base64
import io
import time

from PIL import Image

from services import live_media


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 180), "navy").save(output, format="JPEG")
    return output.getvalue()


def _fake_firestore(monkeypatch):
    grants, shares = {}, {}

    async def create_grant(row):
        grants[row["grant_id"]] = dict(row)

    async def get_grant(grant_id):
        return grants.get(grant_id)

    async def find_grant(workspace_id, actor_id, session_id, source):
        return next((row for row in grants.values()
                     if row.get("workspace_id") == workspace_id
                     and row.get("actor_id") == actor_id
                     and row.get("session_id") == session_id
                     and row.get("source") == source
                     and row.get("status") == "ACTIVE"), None)

    async def create_share(row):
        shares[row["share_id"]] = dict(row)

    async def update_counters(workspace_id, session_id, share_id, **fields):
        assert (workspace_id, session_id) == ("w", "s")
        shares[share_id].update(fields)

    async def stop_share(workspace_id, session_id, share_id, **fields):
        assert (workspace_id, session_id) == ("w", "s")
        shares[share_id].update(fields)

    monkeypatch.setattr(live_media.firestore, "create_media_consent_grant", create_grant)
    monkeypatch.setattr(live_media.firestore, "get_media_consent_grant", get_grant)
    monkeypatch.setattr(
        live_media.firestore, "find_active_media_consent_grant", find_grant)
    monkeypatch.setattr(live_media.firestore, "create_live_media_share", create_share)
    monkeypatch.setattr(
        live_media.firestore, "update_live_media_share_counters", update_counters)
    monkeypatch.setattr(live_media.firestore, "stop_live_media_share", stop_share)
    return grants, shares


async def _grant_and_start(connection, source="camera"):
    challenge = await connection.consent_request({"source": source})
    grant = await connection.consent_accept({
        "challenge_id": challenge["challenge_id"],
        "challenge_nonce": challenge["challenge_nonce"], "source": source})
    prepared = await connection.media_prepare({
        "client_request_id": "prepare-1", "share_id": "share-1",
        "source": source, "consent_grant_id": grant["consent_grant_id"]})
    started = await connection.media_start({
        "client_request_id": "start-1", "share_id": "share-1",
        "source": source, "consent_grant_id": grant["consent_grant_id"],
        "start_nonce": prepared["start_nonce"],
        "capture": {"mime_type": "image/jpeg"}})
    return challenge, grant, prepared, started


async def test_consent_nonce_is_single_use_and_source_bound(monkeypatch):
    _fake_firestore(monkeypatch)
    connection = live_media.LiveMediaConnection(
        workspace_id="w", actor_id="a", session_id="s")
    challenge = await connection.consent_request({"source": "camera"})
    accepted = await connection.consent_accept({
        "challenge_id": challenge["challenge_id"],
        "challenge_nonce": challenge["challenge_nonce"], "source": "camera"})
    replay = await connection.consent_accept({
        "challenge_id": challenge["challenge_id"],
        "challenge_nonce": challenge["challenge_nonce"], "source": "camera"})

    assert accepted["type"] == "consent.granted"
    assert replay["code"] == "consent_replay"


async def test_same_session_source_reuses_disclosure_but_not_start_nonce(monkeypatch):
    _fake_firestore(monkeypatch)
    connection = live_media.LiveMediaConnection(
        workspace_id="w", actor_id="a", session_id="s")
    challenge = await connection.consent_request({"source": "camera"})
    accepted = await connection.consent_accept({
        "challenge_id": challenge["challenge_id"],
        "challenge_nonce": challenge["challenge_nonce"], "source": "camera"})

    reused = await connection.consent_request({"source": "camera"})
    prepared = await connection.media_prepare({
        "client_request_id": "prepare", "share_id": "share",
        "source": "camera", "consent_grant_id": accepted["consent_grant_id"]})

    assert reused["type"] == "consent.granted" and reused["reused"] is True
    assert prepared["type"] == "media.prepared"
    assert "start_nonce" in prepared


async def test_frame_is_validated_forwarded_once_and_never_stored(monkeypatch):
    _, shares = _fake_firestore(monkeypatch)
    connection = live_media.LiveMediaConnection(
        workspace_id="w", actor_id="a", session_id="s")
    _, _, _, started = await _grant_and_start(connection)
    sent = []

    async def forward(data):
        sent.append(data)

    payload = _jpeg()
    response = await connection.media_frame({
        "share_id": "share-1", "generation": started["generation"], "seq": 1,
        "data": base64.b64encode(payload).decode()}, forward)
    forwarded = await connection.record_forwarded(started["generation"])
    stopped = await connection.stop("user_stop")

    assert response is None and forwarded is None and sent == [payload]
    assert stopped["status"] == "STOPPED"
    assert connection.active is None
    assert all("data" not in row and "frame" not in row for row in shares.values())
    late = await connection.media_frame({
        "share_id": "share-1", "generation": started["generation"], "seq": 2,
        "data": base64.b64encode(payload).decode()}, forward)
    assert late["code"] == "stale_generation"


async def test_total_session_budget_refuses_late_share_but_not_voice(monkeypatch):
    _fake_firestore(monkeypatch)
    connection = live_media.LiveMediaConnection(
        workspace_id="w", actor_id="a", session_id="s")
    challenge = await connection.consent_request({"source": "display"})
    grant = await connection.consent_accept({
        "challenge_id": challenge["challenge_id"],
        "challenge_nonce": challenge["challenge_nonce"], "source": "display"})
    connection.started_monotonic = time.monotonic() - 100

    result = await connection.media_prepare({
        "client_request_id": "late", "share_id": "late-share",
        "source": "display", "consent_grant_id": grant["consent_grant_id"]})

    assert result["code"] == "visual_session_budget_low"
    assert result["recoverable"] is True


async def test_active_camera_share_has_no_separate_short_auto_expiry(monkeypatch):
    _fake_firestore(monkeypatch)
    connection = live_media.LiveMediaConnection(
        workspace_id="w", actor_id="a", session_id="s")
    _, _, _, started = await _grant_and_start(connection)
    connection.started_monotonic = time.monotonic() - 120
    forwarded = []

    async def forward(data):
        forwarded.append(data)

    payload = _jpeg()
    result = await connection.media_frame({
        "share_id": "share-1", "generation": started["generation"], "seq": 1,
        "data": base64.b64encode(payload).decode(),
    }, forward)

    assert result is None
    assert forwarded == [payload]
    assert connection.active is not None
    assert connection.active["source"] == "camera"


async def test_start_rechecks_consent_after_prepare(monkeypatch):
    grants, _ = _fake_firestore(monkeypatch)
    connection = live_media.LiveMediaConnection(
        workspace_id="w", actor_id="a", session_id="s")
    challenge = await connection.consent_request({"source": "camera"})
    grant = await connection.consent_accept({
        "challenge_id": challenge["challenge_id"],
        "challenge_nonce": challenge["challenge_nonce"], "source": "camera"})
    prepared = await connection.media_prepare({
        "client_request_id": "prepare", "share_id": "share",
        "source": "camera", "consent_grant_id": grant["consent_grant_id"]})
    grants[grant["consent_grant_id"]]["status"] = "REVOKED"

    result = await connection.media_start({
        "client_request_id": "start", "share_id": "share",
        "source": "camera", "consent_grant_id": grant["consent_grant_id"],
        "start_nonce": prepared["start_nonce"],
        "capture": {"mime_type": "image/jpeg"}})

    assert result["code"] == "consent_challenge_invalid"
    assert connection.active is None


async def test_authority_store_failure_is_media_scoped_error(monkeypatch):
    _fake_firestore(monkeypatch)

    async def unavailable(_grant_id):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(live_media.firestore, "get_media_consent_grant", unavailable)
    connection = live_media.LiveMediaConnection(
        workspace_id="w", actor_id="a", session_id="s")
    result = await connection.media_prepare({
        "client_request_id": "prepare", "share_id": "share",
        "source": "camera", "consent_grant_id": "a" * 32})

    assert result["type"] == "error"
    assert result["scope"] == "media"
    assert result["code"] == "media_authority_unavailable"

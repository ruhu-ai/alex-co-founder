"""Explicit still validation, provenance, and canonical citation contracts."""

from __future__ import annotations

import hashlib
import io

from PIL import Image

from services import document_ingestion, image_ingestion


def _image(fmt: str = "JPEG", size: tuple[int, int] = (320, 180)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (30, 80, 140)).save(output, format=fmt)
    return output.getvalue()


def test_static_jpeg_is_validated_and_normalized_without_metadata():
    original = _image()
    checked = image_ingestion.validate_image(original, "board.jpg", "image/jpeg")
    normalized = image_ingestion.normalize_image(original)

    assert checked == {
        "status": "success", "kind": "image",
        "detected_content_type": "image/jpeg", "detected_extension": ".jpg",
        "width": 320, "height": 180, "pixel_count": 57_600,
    }
    assert normalized["status"] == "success"
    assert normalized["width"] == 320 and normalized["height"] == 180
    assert normalized["normalized_sha256"] == hashlib.sha256(
        normalized["data"]).hexdigest()
    assert b"Exif" not in normalized["data"]


def test_animated_and_mismatched_images_fail_closed():
    frames = [Image.new("RGB", (10, 10), color) for color in ("red", "blue")]
    output = io.BytesIO()
    frames[0].save(output, format="WEBP", save_all=True,
                   append_images=frames[1:], duration=100, loop=0)

    animated = image_ingestion.validate_image(
        output.getvalue(), "animation.webp", "image/webp")
    mismatch = image_ingestion.validate_image(_image(), "fake.png", "image/png")

    assert animated["error_code"] == "animated_image_unsupported"
    assert mismatch["error_code"] == "image_type_mismatch"


def test_observation_regions_are_bounded_and_hash_bound():
    result = image_ingestion.validate_observations([
        {"description": "A blue product sketch", "ocr_text": "",
         "region": {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.3},
         "confidence": "MEDIUM", "safety_flags": []},
        {"description": "invalid", "region": {
            "x": 0.8, "y": 0.8, "width": 0.5, "height": 0.5},
         "confidence": "HIGH"},
    ], "a" * 64)

    assert result["status"] == "success"
    assert len(result["observations"]) == 1
    row = result["observations"][0]
    assert row["region"] == {"x": 0.1, "y": 0.2, "width": 0.4,
                             "height": 0.3, "unit": "normalized"}
    assert row["source_sha256"] == "a" * 64


async def test_search_attachment_image_branch_rechecks_scope_and_cites_region(monkeypatch):
    artifact = {
        "id": "a" * 32, "founder_id": "founder", "session_id": "session",
        "kind": "image", "status": "READY", "scope": "reference_only",
        "authority": "reference_only", "sha256": "b" * 64,
        "source_type": "upload", "source_ref": "whiteboard.jpg",
    }
    evidence = "A launch plan with pricing and milestones"
    observation = {
        "id": "c" * 32, "ordinal": 0, "description": evidence, "ocr_text": "",
        "region": {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.3,
                   "unit": "normalized"},
        "source_sha256": artifact["sha256"],
        "evidence_sha256": hashlib.sha256(evidence.encode()).hexdigest(),
    }

    async def get_artifact(_ref):
        return artifact

    async def list_observations(_ref, limit=64):
        assert limit == 64
        return [observation]

    monkeypatch.setattr(document_ingestion.firestore, "get_artifact", get_artifact)
    monkeypatch.setattr(
        document_ingestion.firestore, "list_image_observations", list_observations)
    monkeypatch.setattr(document_ingestion, "_embed_fn", None)

    result = await document_ingestion.search_attachments(
        founder_id="founder", session_id="session",
        attachment_refs=[artifact["id"]], query="pricing milestones")

    assert result["status"] == "success" and len(result["results"]) == 1
    row = result["results"][0]
    assert row["artifact_kind"] == "image"
    assert row["authority"] == "unconfirmed_evidence"
    assert row["citation"]["source_sha256"] == artifact["sha256"]
    assert row["citation"]["source_url"].endswith(
        "#region=0.1000,0.2000,0.4000,0.3000")


async def test_search_attachment_rejects_image_profile_scope(monkeypatch):
    async def get_artifact(_ref):
        return {"id": "a" * 32, "founder_id": "founder", "session_id": "session",
                "kind": "image", "status": "READY", "scope": "profile",
                "authority": "profile_candidate"}

    monkeypatch.setattr(document_ingestion.firestore, "get_artifact", get_artifact)
    result = await document_ingestion.search_attachments(
        founder_id="founder", session_id="session",
        attachment_refs=["a" * 32], query="anything")
    assert result["error_code"] == "attachment_not_found"


async def test_generic_image_question_returns_bounded_observation(monkeypatch):
    artifact = {
        "id": "a" * 32, "founder_id": "founder", "session_id": "session",
        "kind": "image", "status": "READY", "scope": "reference_only",
        "authority": "reference_only", "sha256": "b" * 64,
        "source_type": "upload", "source_ref": "whiteboard.jpg",
    }
    evidence = "Launch plan\nQ4"
    observation = {
        "id": "c" * 32, "ordinal": 0, "description": "Launch plan",
        "ocr_text": "Q4", "region": {"x": 0.0, "y": 0.0, "width": 1.0,
                                      "height": 1.0, "unit": "normalized"},
        "source_sha256": artifact["sha256"],
        "evidence_sha256": hashlib.sha256(evidence.encode()).hexdigest(),
    }

    async def get_artifact(_ref):
        return artifact

    async def list_observations(_ref, limit=64):
        return [observation]

    monkeypatch.setattr(document_ingestion.firestore, "get_artifact", get_artifact)
    monkeypatch.setattr(
        document_ingestion.firestore, "list_image_observations", list_observations)
    monkeypatch.setattr(document_ingestion, "_embed_fn", None)

    result = await document_ingestion.search_attachments(
        founder_id="founder", session_id="session",
        attachment_refs=[artifact["id"]], query="What is in this image?")

    assert result["status"] == "success"
    assert result["results"][0]["citation"]["evidence_text"] == evidence

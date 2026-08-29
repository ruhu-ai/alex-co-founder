"""Safe explicit-still ingestion and bounded observation extraction (docs/35).

Only founder-uploaded or founder-confirmed Capture still bytes enter this path.
Live camera/display frames must never call this module. Original bytes remain an
owner/session-scoped attachment; a metadata-stripped normalized derivative is the
only image sent to the observation extractor.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from services import firestore, storage

EXTRACTOR_VERSION = "alex-image-observation:v1"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_IMAGE_SIDE = 12_000
MAX_NORMALIZED_SIDE = 2_048
MAX_OBSERVATIONS = 64
MAX_RETURNED_OBSERVATIONS = 8

_MIME_BY_FORMAT = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_EXT_BY_FORMAT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}

ExtractorFn = Callable[[bytes, str], Awaitable[list[dict[str, Any]]]]
_extractor_fn: ExtractorFn | None = None


def enabled() -> bool:
    """Explicit still-image understanding is a disabled-by-default release flag."""
    return os.environ.get("ALEX_STILL_IMAGE_ENABLED", "").lower() in {
        "1", "true", "yes", "on"}


def set_extractor_fn(fn: ExtractorFn | None) -> None:
    """Inject the reviewed multimodal extractor; tests use deterministic fakes."""
    global _extractor_fn
    _extractor_fn = fn


def _error(code: str, message: str, *, status: str = "FAILED") -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "ingestion_status": status, "message": message}


def _open_checked(data: bytes) -> tuple[Image.Image, str] | dict[str, Any]:
    if not data or len(data) > MAX_IMAGE_BYTES:
        return _error("image_too_large", "Image is empty or exceeds 10 MB.")
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))
        detected_format = str(image.format or "").upper()
        if detected_format not in _MIME_BY_FORMAT:
            return _error(
                "unsupported_image_type",
                "Use a JPEG, PNG, or WebP still image.", status="UNSUPPORTED")
        if bool(getattr(image, "is_animated", False)) or int(
                getattr(image, "n_frames", 1)) != 1:
            return _error(
                "animated_image_unsupported",
                "Animated images are not supported; export one still frame.",
                status="UNSUPPORTED")
        width, height = image.size
        if (width < 1 or height < 1 or width > MAX_IMAGE_SIDE
                or height > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS):
            return _error(
                "image_dimensions_unsafe",
                "Image dimensions exceed the safe 25 megapixel limit.")
        image.load()
        return image, detected_format
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return _error("invalid_image", "The image is malformed or unreadable.")


def validate_image(data: bytes, filename: str,
                   declared_content_type: str) -> dict[str, Any]:
    """Validate a static JPEG/PNG/WebP from decoded bytes, never its filename."""
    opened = _open_checked(data)
    if isinstance(opened, dict):
        return opened
    image, detected_format = opened
    detected_mime = _MIME_BY_FORMAT[detected_format]
    declared = str(declared_content_type or "").split(";", 1)[0].lower()
    if declared not in {"", "application/octet-stream", detected_mime}:
        return _error(
            "image_type_mismatch",
            "The uploaded image type does not match its bytes.")
    width, height = image.size
    return {
        "status": "success",
        "kind": "image",
        "detected_content_type": detected_mime,
        "detected_extension": _EXT_BY_FORMAT[detected_format],
        "width": width,
        "height": height,
        "pixel_count": width * height,
    }


def normalize_image(data: bytes) -> dict[str, Any]:
    """Return a bounded, metadata-stripped derivative for provider/display use."""
    opened = _open_checked(data)
    if isinstance(opened, dict):
        return opened
    image, _ = opened
    before = image.size
    original_orientation = image.getexif().get(274)
    image = ImageOps.exif_transpose(image)
    orientation_applied = image.size != before or original_orientation not in {
        None, 1}
    image.thumbnail((MAX_NORMALIZED_SIDE, MAX_NORMALIZED_SIDE), Image.Resampling.LANCZOS)
    has_alpha = "A" in image.getbands()
    output = io.BytesIO()
    if has_alpha:
        image.convert("RGBA").save(output, format="PNG", optimize=True)
        mime_type, extension = "image/png", ".png"
    else:
        image.convert("RGB").save(
            output, format="JPEG", quality=88, optimize=True, progressive=True)
        mime_type, extension = "image/jpeg", ".jpg"
    normalized = output.getvalue()
    return {
        "status": "success", "data": normalized,
        "detected_content_type": mime_type, "detected_extension": extension,
        "width": image.width, "height": image.height,
        "pixel_count": image.width * image.height,
        "orientation_applied": orientation_applied,
        "normalized_sha256": hashlib.sha256(normalized).hexdigest(),
    }


def _canonical_region(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        x, y = float(value["x"]), float(value["y"])
        width, height = float(value["width"]), float(value["height"])
    except (KeyError, TypeError, ValueError):
        return None
    values = (x, y, width, height)
    if (not all(math.isfinite(item) for item in values)
            or x < 0 or y < 0 or width <= 0 or height <= 0
            or x + width > 1.000001 or y + height > 1.000001):
        return None
    return {"x": round(x, 4), "y": round(y, 4),
            "width": round(width, 4), "height": round(height, 4),
            "unit": "normalized"}


def validate_observations(rows: Any, source_sha256: str) -> dict[str, Any]:
    """Schema-check bounded provider output; malformed regions never gain precision."""
    if not isinstance(rows, list) or not rows:
        return _error("image_observation_empty", "Alex could not inspect this image.")
    clean: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows[:MAX_OBSERVATIONS]):
        if not isinstance(row, dict):
            continue
        description = str(row.get("description") or "").strip()[:2_000]
        ocr_text = str(row.get("ocr_text") or "").strip()[:2_000]
        evidence = "\n".join(
            item for item in (description, ocr_text) if item)[:4_000]
        region = _canonical_region(row.get("region"))
        confidence = str(row.get("confidence") or "LOW").upper()
        if not evidence or region is None or confidence not in {"LOW", "MEDIUM", "HIGH"}:
            continue
        evidence_sha = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
        observation_id = hashlib.sha256(
            f"image-observation:v1:{ordinal}:{evidence_sha}:{region}".encode()
        ).hexdigest()[:32]
        clean.append({
            "id": observation_id,
            "ordinal": ordinal,
            "description": description,
            "ocr_text": ocr_text,
            "region": region,
            "confidence": confidence,
            "safety_flags": [str(item)[:80] for item in
                             list(row.get("safety_flags") or [])[:8]],
            "source_sha256": source_sha256,
            "evidence_sha256": evidence_sha,
            "extractor_version": EXTRACTOR_VERSION,
        })
    if not clean:
        return _error(
            "image_observation_invalid",
            "Alex could not produce a safe, grounded observation for this image.")
    return {"status": "success", "observations": clean}


async def _extract(normalized: bytes, mime_type: str) -> list[dict[str, Any]]:
    if _extractor_fn is not None:
        return await _extractor_fn(normalized, mime_type)
    from services import gemini_backends
    return await gemini_backends.image_observation_fn(normalized, mime_type)


async def process_ingestion(ingestion_id: str, *, lease_owner: str = "",
                            retry_transient: bool = False) -> dict[str, Any]:
    """Process one explicit image attachment idempotently; live frames are forbidden."""
    claim = await firestore.claim_ingestion(ingestion_id, lease_owner=lease_owner)
    if claim.get("duplicate"):
        return {"status": "success", "duplicate": True,
                "ingestion_status": claim.get("ingestion_status")}
    if claim.get("in_progress") or claim.get("status") != "success":
        return claim
    owner = claim["lease_owner"]
    artifact = await firestore.get_artifact(ingestion_id)
    if not artifact or artifact.get("kind") != "image":
        return _error("ingestion_not_found", "Image ingestion was not found.")
    normalized_name = ""
    try:
        original = await asyncio.to_thread(storage.read_bytes, artifact["storage_name"])
        if hashlib.sha256(original).hexdigest() != artifact.get("sha256"):
            raise ValueError("artifact_hash_mismatch")
        checked = validate_image(
            original, artifact.get("source_ref", ""),
            artifact.get("declared_content_type", "application/octet-stream"))
        if checked.get("status") != "success":
            await firestore.finish_ingestion(
                ingestion_id, owner, checked.get("ingestion_status", "FAILED"),
                error_code=checked.get("error_code"), message=checked.get("message"))
            return checked
        if not await firestore.set_ingestion_stage(ingestion_id, owner, "EXTRACTING"):
            return _error("lease_changed", "Image worker lease changed.")
        normalized = await asyncio.to_thread(normalize_image, original)
        if normalized.get("status") != "success":
            raise ValueError(str(normalized.get("error_code") or "normalization_failed"))
        normalized_name = f"imagenorm_{ingestion_id}_{uuid.uuid4().hex}{normalized['detected_extension']}"
        await asyncio.to_thread(storage.save_bytes, normalized_name, normalized["data"])
        rows = await asyncio.wait_for(
            _extract(normalized["data"], normalized["detected_content_type"]),
            timeout=45)
        observations = validate_observations(rows, artifact["sha256"])
        if observations.get("status") != "success":
            raise ValueError(str(observations.get("error_code")))
        if not await firestore.set_ingestion_stage(ingestion_id, owner, "INDEXING"):
            return _error("lease_changed", "Image worker lease changed.")
        await firestore.replace_image_observations(
            ingestion_id, observations["observations"])
        await firestore.update_artifact(
            ingestion_id,
            normalized_storage_name=normalized_name,
            normalized_content_type=normalized["detected_content_type"],
            normalized_sha256=normalized["normalized_sha256"],
            normalized_width=normalized["width"],
            normalized_height=normalized["height"],
            orientation_applied=normalized["orientation_applied"],
            metadata_policy="STRIPPED",
            extractor_version=EXTRACTOR_VERSION,
        )
        committed = await firestore.finish_ingestion(
            ingestion_id, owner, "READY",
            observation_count=len(observations["observations"]))
        if not committed:
            raise ValueError("lease_changed")
        return {"status": "success", "attachment_ref": ingestion_id,
                "ingestion_status": "READY",
                "observation_count": len(observations["observations"])}
    except Exception as exc:
        if normalized_name:
            try:
                await asyncio.to_thread(storage.delete_artifact, normalized_name)
            except Exception:
                pass
        code = "image_extraction_failed"
        message = "Image inspection failed; retry or delete the image."
        from services.retry_policy import is_transient_exception

        if retry_transient and is_transient_exception(exc):
            released = await firestore.retry_ingestion(
                ingestion_id, owner, error_code=code, message=message)
            if released.get("retryable"):
                return {**_error(code, message), "retryable": True,
                        "ingestion_status": "QUEUED"}
        await firestore.finish_ingestion(
            ingestion_id, owner, "FAILED", error_code=code, message=message)
        return {**_error(code, message), "detail_code": type(exc).__name__}

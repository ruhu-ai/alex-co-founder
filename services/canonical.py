"""One bounded, versioned canonical JSON and digest implementation."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

CANONICAL_VERSION = "canonical-json-v1"
DEFAULT_MAX_BYTES = 32_768
_FLOAT_SCALE = Decimal("0.000001")


def _normalize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN and Infinity are not canonical JSON")
        # Floats are represented as an explicitly tagged fixed-scale decimal,
        # avoiding interpreter-dependent binary rendering.
        fixed = Decimal(str(value)).quantize(_FLOAT_SCALE,
                                             rounding=ROUND_HALF_EVEN)
        return {"$fixed6": format(fixed, "f")}
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = unicodedata.normalize("NFC", str(key))
            if normalized_key in normalized:
                raise ValueError("normalization creates a duplicate key")
            normalized[normalized_key] = _normalize(item)
        return normalized
    raise ValueError("value is not canonical JSON")


def canonical_json(value: Any, *, domain: str,
                   max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
    if not domain or len(domain) > 128:
        raise ValueError("canonical digest domain is invalid")
    envelope = {
        "$canonical": CANONICAL_VERSION,
        "$domain": unicodedata.normalize("NFC", domain),
        "$value": _normalize(value),
    }
    encoded = json.dumps(
        envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError("canonical JSON exceeds metadata limit")
    return encoded


def canonical_hash(value: Any, *, domain: str,
                   prefixed: bool = True,
                   max_bytes: int = DEFAULT_MAX_BYTES) -> str:
    digest = hashlib.sha256(canonical_json(
        value, domain=domain, max_bytes=max_bytes)).hexdigest()
    return f"sha256:{digest}" if prefixed else digest


def text_hash(value: str, *, domain: str) -> str:
    """Domain-separated digest for a sensitive scalar; never returns it."""
    return canonical_hash({"text": value}, domain=domain)

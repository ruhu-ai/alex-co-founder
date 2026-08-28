"""Golden and rejection tests for the one platform canonical form."""

from __future__ import annotations

import math

import pytest

from services import approval_service
from services.canonical import CANONICAL_VERSION, canonical_hash, canonical_json


def test_canonical_json_v1_golden_vector_and_unicode_normalization():
    decomposed = {"b": "e\u0301", "a": 1}
    composed = {"a": 1, "b": "é"}

    expected = "sha256:ce809b2c556c60599299a40b85cdd3a6b4832c41889445f4adccdae60d268d97"
    assert canonical_hash(decomposed, domain="golden") == expected
    assert canonical_hash(composed, domain="golden") == expected


def test_canonical_json_rejects_ambiguous_or_unbounded_values():
    with pytest.raises(ValueError, match="NaN and Infinity"):
        canonical_hash({"value": math.nan}, domain="test")
    with pytest.raises(ValueError, match="duplicate key"):
        canonical_hash({"é": 1, "e\u0301": 2}, domain="test")
    with pytest.raises(ValueError, match="metadata limit"):
        canonical_json({"body": "x" * 33_000}, domain="test")


def test_domain_and_version_are_inside_the_digest():
    value = {"same": "payload"}
    assert canonical_hash(value, domain="plan") != canonical_hash(
        value, domain="approval")
    encoded = canonical_json(value, domain="plan")
    assert CANONICAL_VERSION.encode() in encoded


def test_approval_binding_is_order_independent_and_fail_closed():
    first = approval_service.mapping_hash({"legal_name": "Ada", "year": 2026})
    reordered = approval_service.mapping_hash({"year": 2026, "legal_name": "Ada"})
    changed = approval_service.mapping_hash({"legal_name": "Ada", "year": 2027})

    assert first == reordered
    assert first != changed
    assert first.startswith("sha256:")

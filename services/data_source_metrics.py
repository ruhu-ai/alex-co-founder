"""Bounded, content-free counters for docs/24 reliability boundaries."""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

LOGGER = logging.getLogger("data_sources.metrics")
_COUNTERS: Counter[str] = Counter()
_SAFE_FIELDS = {
    "connector_id", "action_kind", "event_kind", "status", "error_code",
    "correlation_status", "delivery_status", "operation", "outcome", "phase",
    "count", "latency_ms",
}


def record(name: str, **fields: Any) -> None:
    """Increment one counter and drop all unreviewed/high-cardinality fields."""
    _COUNTERS[name] += 1
    safe = {key: value for key, value in fields.items() if key in _SAFE_FIELDS}
    LOGGER.info(json.dumps({"metric": name, **safe}, sort_keys=True))


def snapshot() -> dict[str, int]:
    return dict(_COUNTERS)


def reset() -> None:
    _COUNTERS.clear()

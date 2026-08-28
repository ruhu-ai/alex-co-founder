"""Secret-free structured metrics for the closed background artifact pilot."""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

LOGGER = logging.getLogger("background_pilot.metrics")
_COUNTERS: Counter[str] = Counter()
_SAFE_FIELDS = {
    "status", "error_code", "template_id", "attempt", "latency_ms",
    "runtime_status", "duplicate", "kill_switch_active",
}


def record(name: str, **fields: Any) -> None:
    """Count one closed event and log only reviewed non-content dimensions."""
    _COUNTERS[name] += 1
    safe = {key: value for key, value in fields.items() if key in _SAFE_FIELDS}
    LOGGER.info(json.dumps({"metric": name, **safe}, sort_keys=True))


def snapshot() -> dict[str, int]:
    return dict(_COUNTERS)


def reset() -> None:
    _COUNTERS.clear()

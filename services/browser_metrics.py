"""Secret-free structured browser runtime counters (docs/22)."""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

LOGGER = logging.getLogger("browser.metrics")
_COUNTERS: Counter[str] = Counter()
_SAFE_FIELDS = {
    "kind", "reason", "generation", "count", "status", "latency_ms",
    "subscriber_count", "frame_seq",
}


def record(name: str, **fields: Any) -> None:
    """Increment a counter and log only reviewed non-content dimensions."""
    _COUNTERS[name] += 1
    safe = {key: value for key, value in fields.items() if key in _SAFE_FIELDS}
    LOGGER.info(json.dumps({"metric": name, **safe}, sort_keys=True))


def snapshot() -> dict[str, int]:
    return dict(_COUNTERS)


def reset() -> None:
    _COUNTERS.clear()

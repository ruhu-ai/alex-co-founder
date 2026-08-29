"""Process-local trusted marker for active Gemini Live visual epochs.

The marker is created only after a validated frame is forwarded and is removed only
when that provider/WebSocket context is destroyed. Model text and tool arguments have
no API that can create, clear, or downgrade it. A process restart destroys both the
provider connection and this marker, so it cannot leave a less-conservative live epoch.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class LiveVisualEpoch:
    workspace_id: str
    session_id: str
    epoch_id: str
    source: str
    connection_generations: frozenset[int]


_epochs: dict[tuple[str, str], LiveVisualEpoch] = {}


def mark(*, workspace_id: str, session_id: str, source: str,
         connection_generation: int) -> LiveVisualEpoch:
    """Mark a session's provider context visual; repeated frames preserve the epoch."""
    key = (workspace_id, session_id)
    current = _epochs.get(key)
    if current is not None:
        if connection_generation in current.connection_generations:
            return current
        current = LiveVisualEpoch(
            current.workspace_id, current.session_id, current.epoch_id,
            current.source,
            current.connection_generations | {connection_generation})
        _epochs[key] = current
        return current
    epoch = LiveVisualEpoch(
        workspace_id, session_id, secrets.token_hex(16), source,
        frozenset({connection_generation}))
    _epochs[key] = epoch
    return epoch


def get(workspace_id: str, session_id: str) -> LiveVisualEpoch | None:
    """Return the server-owned epoch marker, never a client/model projection."""
    return _epochs.get((workspace_id, session_id))


def clear(*, workspace_id: str, session_id: str,
          connection_generation: int) -> None:
    """Clear one destroyed provider context without weakening another socket."""
    key = (workspace_id, session_id)
    current = _epochs.get(key)
    if current is None or connection_generation not in current.connection_generations:
        return
    remaining = current.connection_generations - {connection_generation}
    if not remaining:
        _epochs.pop(key, None)
        return
    _epochs[key] = LiveVisualEpoch(
        current.workspace_id, current.session_id, current.epoch_id,
        current.source, remaining)

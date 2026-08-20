"""Artifact storage helpers (docs/02).

Large payloads always become artifacts; conversations carry summaries only.
Service-layer store for artifacts written outside tool contexts (task runs,
endpoint uploads). Tools with a ToolContext should prefer
tool_context.save_artifact for model-visible artifacts.

Local layout: ARTIFACT_SERVICE_URI=file:///abs/path → files under that dir.
gs:// URIs: via gcsfs (prod).
"""

from __future__ import annotations

import os

_ROOT: str | None = None


def _root() -> str:
    global _ROOT
    if _ROOT is None:
        uri = os.environ.get("ARTIFACT_SERVICE_URI", f"file://{os.path.abspath('artifacts')}")
        if uri.startswith("file://"):
            _ROOT = uri.removeprefix("file://")
        else:
            _ROOT = os.path.abspath("artifacts")  # gs:// handled by gcsfs on write
        os.makedirs(_ROOT, exist_ok=True)
    return _ROOT


def artifact_path(name: str) -> str:
    """Absolute local path for an artifact name (parent dirs created)."""
    path = os.path.join(_root(), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def save_text(name: str, text: str) -> str:
    with open(artifact_path(name), "w", encoding="utf-8") as fh:
        fh.write(text)
    return name


def read_text(name: str) -> str:
    with open(artifact_path(name), encoding="utf-8") as fh:
        return fh.read()


def save_bytes(name: str, data: bytes) -> str:
    with open(artifact_path(name), "wb") as fh:
        fh.write(data)
    return name


def exists(name: str) -> bool:
    return os.path.exists(artifact_path(name))


def list_artifacts() -> list[str]:
    out: list[str] = []
    for dirpath, _dirs, files in os.walk(_root()):
        for f in files:
            out.append(os.path.relpath(os.path.join(dirpath, f), _root()))
    return sorted(out)

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
import re

_ROOT: str | None = None


def _root() -> str:
    """Local cache dir — always. When ARTIFACT_SERVICE_URI is gs://, writes are
    mirrored to the bucket and reads fall back to it (write-through cache:
    subprocesses and Playwright keep working on local paths; artifacts
    survive instance restarts)."""
    global _ROOT
    if _ROOT is None:
        uri = os.environ.get("ARTIFACT_SERVICE_URI", f"file://{os.path.abspath('artifacts')}")
        _ROOT = (uri.removeprefix("file://") if uri.startswith("file://")
                 else os.path.abspath("artifacts"))
        os.makedirs(_ROOT, exist_ok=True)
    return _ROOT


def _gcs():
    """(filesystem, gs:// prefix) when the artifact URI is a bucket, else None."""
    uri = os.environ.get("ARTIFACT_SERVICE_URI", "")
    if not uri.startswith("gs://"):
        return None
    import gcsfs

    return gcsfs.GCSFileSystem(), uri.rstrip("/") + "/"


def artifact_path(name: str) -> str:
    """Absolute local path for an artifact name (parent dirs created)."""
    path = os.path.join(_root(), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _mirror_to_gcs(name: str) -> None:
    gcs = _gcs()
    if gcs:
        fs, prefix = gcs
        fs.put_file(artifact_path(name), prefix + name)


def save_text(name: str, text: str) -> str:
    with open(artifact_path(name), "w", encoding="utf-8") as fh:
        fh.write(text)
    _mirror_to_gcs(name)
    return name


def read_text(name: str) -> str:
    if not os.path.exists(artifact_path(name)):
        gcs = _gcs()
        if gcs:
            fs, prefix = gcs
            fs.get_file(prefix + name, artifact_path(name))
    with open(artifact_path(name), encoding="utf-8") as fh:
        return fh.read()


def read_bytes(name: str) -> bytes:
    if not os.path.exists(artifact_path(name)):
        gcs = _gcs()
        if gcs:
            fs, prefix = gcs
            fs.get_file(prefix + name, artifact_path(name))
    with open(artifact_path(name), "rb") as fh:
        return fh.read()


def save_bytes(name: str, data: bytes) -> str:
    with open(artifact_path(name), "wb") as fh:
        fh.write(data)
    _mirror_to_gcs(name)
    return name


def exists(name: str) -> bool:
    if os.path.exists(artifact_path(name)):
        return True
    gcs = _gcs()
    return bool(gcs and gcs[0].exists(gcs[1] + name))


def download_if_missing(name: str) -> str:
    """Ensure a local copy exists (download endpoint, previews) — returns the
    local path either way."""
    if not os.path.exists(artifact_path(name)):
        gcs = _gcs()
        if gcs:
            fs, prefix = gcs
            fs.get_file(prefix + name, artifact_path(name))
    return artifact_path(name)


def list_artifacts() -> list[str]:
    out: list[str] = []
    for dirpath, _dirs, files in os.walk(_root()):
        for f in files:
            out.append(os.path.relpath(os.path.join(dirpath, f), _root()))
    gcs = _gcs()
    if gcs:
        fs, prefix = gcs
        out.extend(p.removeprefix(prefix) for p in fs.ls(prefix))
    return sorted(set(out))


def delete_artifact(name: str) -> bool:
    """Delete one exact relative artifact from local cache and GCS mirror."""
    if (not name or len(name) > 512 or os.path.isabs(name)
            or ".." in name.split("/") or not re.fullmatch(
                r"[A-Za-z0-9_.\-/]+", name)):
        raise ValueError("invalid artifact name")
    deleted = False
    path = artifact_path(name)
    if os.path.isfile(path):
        os.remove(path)
        deleted = True
    gcs = _gcs()
    if gcs:
        fs, prefix = gcs
        remote = prefix + name
        if fs.exists(remote):
            fs.rm(remote)
            deleted = True
    return deleted

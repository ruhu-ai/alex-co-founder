"""WorkflowDefinition loader — the declarative workflow engine core (docs/01).

Domain specifics live in workflows/*.yaml, never in core code. Swapping in a
second workflow file must require zero code changes to load it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

# Resolved against the repo root, not the process CWD: importing this package
# from any working directory (tests, tooling, a task runner) must still find the
# workflow file. workflow.py lives at <repo>/agents/co_founder/workflow.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKFLOW_FILE = _REPO_ROOT / "workflows" / "grant_applications.yaml"


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    display_name: str
    entity_schema: dict
    states: list[str]
    application_states: list[str]
    sources: list[dict]
    fit_criteria: dict
    approval_policy: dict
    raw: dict = field(default_factory=dict)


_REQUIRED_KEYS = (
    "workflow_id",
    "display_name",
    "entity_schema",
    "states",
    "application_states",
    "sources",
    "fit_criteria",
    "approval_policy",
)


def load_workflow(path: str | os.PathLike | None = None) -> WorkflowDefinition:
    """Load and validate a workflow YAML. Fails loudly, naming the offending key."""
    path = Path(path or os.environ.get("WORKFLOW_FILE", DEFAULT_WORKFLOW_FILE))
    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Workflow file {path} must contain a YAML mapping at top level")

    missing = [k for k in _REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(f"Workflow file {path} missing required key(s): {', '.join(missing)}")

    if not isinstance(data["entity_schema"], dict) or not data["entity_schema"]:
        raise ValueError(f"Workflow file {path}: 'entity_schema' must be a non-empty mapping")
    if not isinstance(data["sources"], list):
        raise ValueError(f"Workflow file {path}: 'sources' must be a list")
    for i, source in enumerate(data["sources"]):
        if "type" not in source:
            raise ValueError(f"Workflow file {path}: sources[{i}] missing 'type'")

    return WorkflowDefinition(
        workflow_id=str(data["workflow_id"]),
        display_name=str(data["display_name"]),
        entity_schema=dict(data["entity_schema"]),
        states=list(data["states"]),
        application_states=list(data["application_states"]),
        sources=list(data["sources"]),
        fit_criteria=dict(data["fit_criteria"]),
        approval_policy=dict(data["approval_policy"]),
        raw=data,
    )


@lru_cache(maxsize=1)
def get_workflow() -> WorkflowDefinition:
    """Cached active workflow (path from WORKFLOW_FILE env)."""
    return load_workflow()

"""Content-free shadow trace records; no authority or durable write occurs here."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShadowSkillTrace:
    correlation_id: str
    workspace_ref: str
    run_ref: str
    skill_identity: str
    definition_hash: str
    qualification_hash: str
    selector_version: str
    outcome: str
    candidate_ids: tuple[str, ...]
    context_hash: str
    context_item_count: int
    effective_capability_ids: tuple[str, ...]
    domain_writes: int = 0
    workflow_writes: int = 0
    approval_writes: int = 0
    action_writes: int = 0

    def is_shadow_only(self) -> bool:
        return not any((self.domain_writes, self.workflow_writes,
                        self.approval_writes, self.action_writes))

"""Deterministic, bounded shadow selection; never invokes a skill or model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from skills.models import CompiledCatalog, Lifecycle, SkillCard


@dataclass(frozen=True)
class SelectionRequest:
    invocation_mode: str
    intent: str
    agent_role: str
    workflow_step: str
    direct_skill_id: str = ""
    include_draft_for_shadow: bool = False


@dataclass(frozen=True)
class SelectionResult:
    outcome: Literal["SELECTED", "CLARIFY", "NO_MATCH"]
    candidates: tuple[SkillCard, ...]
    selected: SkillCard | None = None
    clarification_facets: tuple[str, ...] = ()


def select_skill(catalog: CompiledCatalog, request: SelectionRequest) -> SelectionResult:
    allowed_states = {Lifecycle.QUALIFIED, Lifecycle.CANARY, Lifecycle.ACTIVE}
    if request.include_draft_for_shadow:
        allowed_states.add(Lifecycle.DRAFT)
    candidates = tuple(card for card in catalog.cards() if (
        card.status in allowed_states
        and request.invocation_mode in card.invocation_modes
        and request.intent in card.supported_intents
        and request.agent_role in card.agent_roles
        and request.workflow_step in card.workflow_steps
        and (not request.direct_skill_id or card.skill_id == request.direct_skill_id)
    ))
    ordered = tuple(sorted(candidates, key=lambda item: (item.skill_id, item.version)))
    if not ordered:
        return SelectionResult("NO_MATCH", ())
    if len(ordered) > 5:
        return SelectionResult(
            "CLARIFY", ordered,
            clarification_facets=("goal", "workflow step", "evidence source"),
        )
    if len(ordered) == 1:
        return SelectionResult("SELECTED", ordered, selected=ordered[0])
    return SelectionResult("CLARIFY", ordered, clarification_facets=("goal", "workflow step"))

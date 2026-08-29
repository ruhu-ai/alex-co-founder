"""Per-pilot hard-gate packet; no live adoption without all evidence."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PilotReadinessPacket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pilot_id: str
    skill_identity: str
    journey_ref: str
    live_agent_ref: str
    live_tool_capability_map_ref: str
    predicate_enforcement_ref: str
    sole_authority_ref: str
    contract_map_ref: str
    context_contract_ref: str
    error_retry_approval_ref: str
    proof_suite_ref: str
    rollback_ref: str
    definition_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    qualification_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    capability_closure_verified: bool
    typed_contracts_verified: bool
    one_authority_verified: bool
    negative_authority_tests_passed: bool
    qualification_passed: bool
    live_adoption_requested: bool = False

    def blockers(self) -> tuple[str, ...]:
        required_refs = {
            "journey_ref": self.journey_ref,
            "live_agent_ref": self.live_agent_ref,
            "live_tool_capability_map_ref": self.live_tool_capability_map_ref,
            "predicate_enforcement_ref": self.predicate_enforcement_ref,
            "sole_authority_ref": self.sole_authority_ref,
            "contract_map_ref": self.contract_map_ref,
            "context_contract_ref": self.context_contract_ref,
            "error_retry_approval_ref": self.error_retry_approval_ref,
            "proof_suite_ref": self.proof_suite_ref,
            "rollback_ref": self.rollback_ref,
        }
        blocked = [name for name, value in required_refs.items() if not value or "TODO" in value]
        flags = {
            "capability_closure_verified": self.capability_closure_verified,
            "typed_contracts_verified": self.typed_contracts_verified,
            "one_authority_verified": self.one_authority_verified,
            "negative_authority_tests_passed": self.negative_authority_tests_passed,
            "qualification_passed": self.qualification_passed,
        }
        blocked.extend(name for name, passed in flags.items() if not passed)
        return tuple(blocked)

    def implementation_approved(self) -> bool:
        return self.live_adoption_requested and not self.blockers()

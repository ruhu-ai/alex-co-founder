"""State constants — the only place state names may appear as literals (docs/02, 03)."""


class OpportunityState:
    DISCOVERED = "DISCOVERED"
    SHORTLISTED = "SHORTLISTED"
    ARCHIVED = "ARCHIVED"


class ApplicationStep:
    IDLE = "IDLE"
    TRIAGE = "TRIAGE"
    INTERVIEWING = "INTERVIEWING"
    DRAFTING = "DRAFTING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    APPROVED = "APPROVED"
    FORM_FILLING = "FORM_FILLING"
    AWAITING_SUBMIT_APPROVAL = "AWAITING_SUBMIT_APPROVAL"
    SUBMITTED = "SUBMITTED"
    FOLLOW_UP = "FOLLOW_UP"
    CLOSED = "CLOSED"


class ChecklistStatus:
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    BLOCKED = "BLOCKED"


class SectionStatus:
    DRAFTED = "DRAFTED"
    IN_REVIEW = "IN_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"


class ApprovalStatus:
    PENDING = "PENDING"
    GRANTED = "GRANTED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    DENIED = "DENIED"


# Session state keys (docs/02 §session state keys)
K_CURRENT_STEP = "current_step"
K_ACTIVE_APPLICATION_ID = "active_application_id"
K_ACTIVE_OPPORTUNITY_ID = "active_opportunity_id"
K_ACTIVE_PROGRAM_REQUIREMENTS = "active_program_requirements"
K_CHECKLIST_STATUS = "checklist_status"
K_PENDING_SIGNALS = "pending_signals"
K_CURRENT_SECTION = "current_section"
K_ACTIVE_ATTACHMENTS = "active_attachments"
K_OPPORTUNITY_READINESS = "opportunity_readiness"
K_BROWSER_STATUS = "browser_status"
K_USER_PROFILE_ID = "user:profile_id"
# Server-derived interactive actor projected into ADK state. Tools may use this
# identity for audit/binding only; authorization still re-reads the durable
# workspace membership at the consequence boundary.
K_ACTOR_ID = "platform:actor_id"
K_USER_PREFS = "user:prefs"
K_APP_WORKFLOW_ID = "app:workflow_id"
K_TODAY = "today"  # refreshed every turn by the callback — the agent's clock
# The inspected portal field signature (docs/09 staleness fence). A PERSISTED
# session key on purpose: a `temp:` key is dropped by ADK at event append, so
# it never survives the cross-turn fill→approve→submit flow.
K_PORTAL_SIGNATURE = "portal_signature"


# Legal application-step transitions (docs/03 §transition table).
# Opportunity lifecycle (DISCOVERED → SHORTLISTED | ARCHIVED) is enforced by
# the matchmaker tools, not this table.
_TRANSITIONS = {
    ApplicationStep.IDLE: {ApplicationStep.TRIAGE},
    ApplicationStep.TRIAGE: {ApplicationStep.INTERVIEWING, ApplicationStep.IDLE},
    ApplicationStep.INTERVIEWING: {ApplicationStep.DRAFTING},
    ApplicationStep.DRAFTING: {ApplicationStep.AWAITING_REVIEW},
    ApplicationStep.AWAITING_REVIEW: {ApplicationStep.APPROVED, ApplicationStep.DRAFTING},
    ApplicationStep.APPROVED: {ApplicationStep.FORM_FILLING},
    ApplicationStep.FORM_FILLING: {ApplicationStep.AWAITING_SUBMIT_APPROVAL},
    ApplicationStep.AWAITING_SUBMIT_APPROVAL: {ApplicationStep.SUBMITTED},
    ApplicationStep.SUBMITTED: {ApplicationStep.FOLLOW_UP},
    ApplicationStep.FOLLOW_UP: {ApplicationStep.CLOSED, ApplicationStep.FOLLOW_UP},
    ApplicationStep.CLOSED: set(),
}


def can_transition(from_step: str, to_step: str) -> bool:
    """True iff the docs/03 transition table allows from_step → to_step."""
    return to_step in _TRANSITIONS.get(from_step, set())

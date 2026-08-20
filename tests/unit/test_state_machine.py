"""Transition-table tests (docs/03). The state machine is the guardrail:
every legal move allowed, every illegal move refused — including the two
safety gates (no drafting from INTERVIEWING, no submit skipping the gate)."""

import pytest

from agents.co_founder.state_schema import ApplicationStep as S
from agents.co_founder.state_schema import can_transition


class TestLegalTransitions:
    @pytest.mark.parametrize("frm,to", [
        (S.IDLE, S.TRIAGE),
        (S.TRIAGE, S.INTERVIEWING),
        (S.TRIAGE, S.IDLE),
        (S.INTERVIEWING, S.DRAFTING),
        (S.DRAFTING, S.AWAITING_REVIEW),
        (S.AWAITING_REVIEW, S.APPROVED),
        (S.AWAITING_REVIEW, S.DRAFTING),          # section rejected → back to drafting
        (S.APPROVED, S.FORM_FILLING),
        (S.FORM_FILLING, S.AWAITING_SUBMIT_APPROVAL),
        (S.AWAITING_SUBMIT_APPROVAL, S.SUBMITTED),
        (S.SUBMITTED, S.FOLLOW_UP),
        (S.FOLLOW_UP, S.CLOSED),
        (S.FOLLOW_UP, S.FOLLOW_UP),               # many wake cycles until closure
    ])
    def test_legal(self, frm, to):
        assert can_transition(frm, to), f"{frm} → {to} should be legal"


class TestSafetyGates:
    def test_no_drafting_from_interviewing(self):
        """G1: draft tools must never run while the interview is open."""
        assert not can_transition(S.INTERVIEWING, S.AWAITING_REVIEW)
        assert not can_transition(S.INTERVIEWING, S.APPROVED)

    def test_no_submit_skipping_approval_gate(self):
        """G2: SUBMITTED is reachable only from AWAITING_SUBMIT_APPROVAL."""
        for frm in (S.DRAFTING, S.AWAITING_REVIEW, S.APPROVED, S.FORM_FILLING):
            assert not can_transition(frm, S.SUBMITTED), f"{frm} → SUBMITTED must be refused"

    def test_no_form_fill_before_approved(self):
        """G3: FORM_FILLING is reachable only from APPROVED."""
        for frm in (S.IDLE, S.TRIAGE, S.INTERVIEWING, S.DRAFTING, S.AWAITING_REVIEW):
            assert not can_transition(frm, S.FORM_FILLING), f"{frm} → FORM_FILLING must be refused"

    def test_no_backwards_leaps(self):
        assert not can_transition(S.SUBMITTED, S.DRAFTING)
        assert not can_transition(S.CLOSED, S.INTERVIEWING)
        assert not can_transition(S.APPROVED, S.DRAFTING)

    def test_unknown_states_refused(self):
        assert not can_transition("NOPE", S.IDLE)
        assert not can_transition(S.IDLE, "NOPE")

    def test_closed_is_terminal(self):
        assert not can_transition(S.CLOSED, S.CLOSED)

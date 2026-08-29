"""Addressed, bounded intent contracts for conversational Live hold."""

from services.live_attention import HOLD, RESUME, classify_addressed_attention_intent


def test_hold_meaning_accepts_natural_addressed_variants():
    for text in (
        "Alex hold on",
        "Alex, can you actually hold on?",
        "Alex, I need you to wait a moment",
        "Alex, pause and wait",
        "Hey Alex, please wait a moment",
        "Okay Alex could you stand by please",
        "Alex, give me a second",
        "Please Alex don't respond yet",
    ):
        assert classify_addressed_attention_intent(text) == HOLD


def test_resume_meaning_accepts_natural_addressed_variants():
    for text in (
        "Alex resume",
        "Hey Alex, you can listen again",
        "Alex continue now",
        "Okay Alex, come back",
        "Alex, we're ready now",
    ):
        assert classify_addressed_attention_intent(text) == RESUME


def test_alex_address_is_required_and_background_speech_is_ignored():
    for text in (
        "hold on",
        "we can continue now",
        "Did Alex say to hold on?",
        "Alex should hold on to this document",
        "Alex find companies named Hold On Limited",
        "I want to test whether Alex can hold on",
        "Hey Alexa resume the music",
        "Alex",
        "",
    ):
        assert classify_addressed_attention_intent(text) is None


def test_attention_intent_is_bounded_and_has_no_broad_command_surface():
    assert classify_addressed_attention_intent(
        "Alex resume and send the application") is None
    assert classify_addressed_attention_intent(
        "Alex " + "resume " * 100) is None


def test_live_agent_cannot_claim_application_hold_without_application_state():
    from agents.co_founder.instructions import LIVE_ATTENTION_INSTRUCTION

    assert "owned by the application state machine" in LIVE_ATTENTION_INSTRUCTION
    assert "Never claim that you entered Hold" in LIVE_ATTENTION_INSTRUCTION
    assert 'visible voice status must read "On\n  hold."' in LIVE_ATTENTION_INSTRUCTION

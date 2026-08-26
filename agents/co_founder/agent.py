"""App wiring (docs/04): root orchestrator + 5 sub-agents + compaction.

The distiller is deliberately NOT in sub_agents — it runs standalone via the
distill surface (docs/07).
"""

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.apps.app import ContextCacheConfig, EventsCompactionConfig

from .callbacks import (
    enforce_effect_claims,
    enforce_workflow_tool_contract,
    initialize_session_state,
    track_tool_outcome,
)
from .config import PERSONA_NAME, REASONING_MODEL
from .instructions import ORCHESTRATOR_INSTRUCTION
from .sub_agents import drafter, form_filler, interviewer, matchmaker, scout
from .tools import a2a_talk as a2a_talk_tools
from .tools import alex_mail as alex_mail_tools
from .tools import attachments as attachment_tools
from .tools import browse as browse_tools
from .tools import calendar as calendar_tools
from .tools import feedback as feedback_tools
from .tools import followup as followup_tools
from .tools import pipeline
from .workflow import get_workflow

_workflow = get_workflow()


def _build_sub_agents(live: bool) -> list[Agent]:
    """The five sub-agents in the transfer graph.

    Under run_live (live=True) a transfer continues the SAME bidi session on the
    sub-agent's model, so a sub-agent on a text-only tier (3.6-flash) would open
    a Live connection on a model that has no native-audio surface and error the
    hand-off. Build the whole sub-tree on the Live native-audio model so voice
    hand-offs to scout/drafter/interviewer/form_filler actually work. The text
    pipeline (live=False) keeps the quality tiers — drafter on REASONING_MODEL."""
    if not live:
        return [
            scout.build_agent(),
            matchmaker.build_agent(),
            interviewer.build_agent(),
            drafter.build_agent(REASONING_MODEL),
            form_filler.build_agent(),
        ]
    from google.adk.models import Gemini
    from google.genai import types

    from .config import LIVE_MODEL_ID

    def _live_model() -> Gemini:
        # Fresh instance per sub-agent (each Agent owns its model wiring).
        return Gemini(model=LIVE_MODEL_ID,
                      retry_options=types.HttpRetryOptions(attempts=3))

    return [
        scout.build_agent(_live_model()),
        matchmaker.build_agent(_live_model()),
        interviewer.build_agent(_live_model()),
        drafter.build_agent(_live_model()),
        form_filler.build_agent(_live_model()),
    ]


def build_root_agent(model, live: bool = False) -> Agent:
    """The orchestrator, parameterised by model: the text pipeline uses MODEL;
    the real-time voice surface (app/live.py) builds the SAME agent — same
    instruction, tools, sub-agents, guards — on the Gemini Live native-audio
    model. Only the modality changes. When live=True the sub-agents are built on
    the Live model too, so cross-agent hand-offs stay on a native-audio model."""
    return Agent(
        name="co_founder",
        model=model,
        instruction=ORCHESTRATOR_INSTRUCTION.replace(
            "__WORKFLOW_DISPLAY_NAME__", _workflow.display_name
        ).replace("__PERSONA_NAME__", PERSONA_NAME),
        tools=[
            pipeline.get_pipeline,
            pipeline.choose_opportunity,
            pipeline.get_checklist,
            attachment_tools.search_attachment,
            feedback_tools.record_feedback,
            # SUBMITTED → FOLLOW_UP → CLOSED (orchestrator instruction step 7):
            # these were defined but registered on no agent, leaving the
            # follow-up phase unreachable.
            feedback_tools.submit_voice_note,
            followup_tools.schedule_followup,
            followup_tools.record_status,
            calendar_tools.get_upcoming_meetings,
            calendar_tools.check_availability,
            calendar_tools.book_meeting,
            alex_mail_tools.check_alex_inbox,
            alex_mail_tools.search_alex_mail,
            alex_mail_tools.read_alex_message,
            alex_mail_tools.send_alex_email,
            browse_tools.open_page,
            browse_tools.read_page,
            browse_tools.browser_action,
            browse_tools.close_browser,
            a2a_talk_tools.ask_portal_agent,
        ],
        sub_agents=_build_sub_agents(live),
        before_agent_callback=initialize_session_state,
        before_tool_callback=enforce_workflow_tool_contract,
        after_tool_callback=track_tool_outcome,
        after_model_callback=enforce_effect_claims,
    )


root_agent = build_root_agent(REASONING_MODEL)

app = App(
    name="co_founder",
    root_agent=root_agent,
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=8,  # summarize after every 8 turns (lab uses 3; a real app uses more)
        overlap_size=2,  # re-read 2 turns either side — never split a Q from its A
    ),
    # Multi-agent transfers swap instruction+tools per agent; per-agent prompt
    # caching avoids re-sending the whole prefix after each transfer.
    context_cache_config=ContextCacheConfig(
        cache_intervals=10,
        ttl_seconds=3600,
        min_tokens=2048,
    ),
)

"""App wiring (docs/04): root orchestrator + 5 sub-agents + compaction.

The distiller is deliberately NOT in sub_agents — it runs standalone via the
distill surface (docs/07).
"""

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.apps.app import ContextCacheConfig, EventsCompactionConfig

from .callbacks import initialize_session_state
from .config import PERSONA_NAME, REASONING_MODEL
from .instructions import ORCHESTRATOR_INSTRUCTION
from .sub_agents import drafter, form_filler, interviewer, matchmaker, scout
from .tools import alex_mail as alex_mail_tools
from .tools import a2a_talk as a2a_talk_tools
from .tools import browse as browse_tools
from .tools import calendar as calendar_tools
from .tools import feedback as feedback_tools
from .tools import followup as followup_tools
from .tools import pipeline
from .workflow import get_workflow

_workflow = get_workflow()


def build_root_agent(model) -> Agent:
    """The orchestrator, parameterised by model: the text pipeline uses MODEL;
    the real-time voice surface (app/live.py) builds the SAME agent — same
    instruction, tools, sub-agents, guards — on the Gemini Live native-audio
    model. Only the modality changes."""
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
        sub_agents=[
            scout.build_agent(),
            matchmaker.build_agent(),
            interviewer.build_agent(),
            drafter.build_agent(REASONING_MODEL),
            form_filler.build_agent(),
        ],
        before_agent_callback=initialize_session_state,
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

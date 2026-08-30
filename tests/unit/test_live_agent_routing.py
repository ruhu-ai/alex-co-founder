"""Live always re-enters root Alex; specialists are bounded task workers."""

from types import SimpleNamespace

import pytest
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from agents.co_founder import state_schema as ss
from app.live import _prepare_live_session, live_app


def test_live_specialists_are_task_scoped_tools_not_sticky_chat_owners():
    root = live_app.root_agent
    specialists = {agent.name: agent for agent in root.sub_agents}
    task_tools = {
        getattr(tool, "name", "")
        for tool in root.tools
        if type(tool).__name__ == "_TaskAgentTool"
    }

    assert set(specialists) == {
        "scout_agent",
        "matchmaker_agent",
        "interviewer_agent",
        "drafter_agent",
        "form_filler_agent",
    }
    assert task_tools == set(specialists)
    assert all(agent.mode == "task" for agent in specialists.values())
    assert all(agent.disallow_transfer_to_parent
               for agent in specialists.values())
    assert all(agent.disallow_transfer_to_peers
               for agent in specialists.values())


@pytest.mark.asyncio
async def test_live_bootstrap_restores_root_and_injects_continuity_content_free():
    service = InMemorySessionService()
    session = await service.create_session(
        app_name=live_app.name,
        user_id="founder",
        session_id="session",
        state={ss.K_MEMORY_MODE: "STANDARD"},
    )
    await service.append_event(
        session,
        Event(author="scout_agent", invocation_id="old-sticky-transfer"),
    )

    await _prepare_live_session(
        session_service=service,
        session=session,
        actor_id="founder_actor",
        workspace_id="founder",
    )

    bootstrap = session.events[-1]
    assert bootstrap.author == live_app.root_agent.name
    assert bootstrap.content is None
    assert bootstrap.custom_metadata == {
        "live_control_event": "root_continuity_bootstrap",
        "content_free": True,
    }
    assert ss.K_CONTINUITY_CONTEXT not in bootstrap.actions.state_delta
    assert "past conversation search is available" in session.state[
        ss.K_CONTINUITY_CONTEXT]
    assert session.state[ss.K_ACTOR_ID] == "founder_actor"
    assert session.state[ss.K_USER_PROFILE_ID] == "founder"

    runner_stub = SimpleNamespace(resumability_config=None)
    active = Runner._find_agent_to_run(
        runner_stub, session, live_app.root_agent)
    assert active is live_app.root_agent

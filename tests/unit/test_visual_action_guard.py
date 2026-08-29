"""Visual provenance follows every current Alex agent and blocks durable choices."""

from __future__ import annotations

from types import SimpleNamespace

from agents.co_founder.agent import build_root_agent
from agents.co_founder.callbacks import enforce_workflow_tool_contract
from agents.co_founder.config import REASONING_MODEL
from services import capability_registry, live_visual_context


def _tool_names(agent):
    names = {str(getattr(tool, "name", getattr(tool, "__name__", "")))
             for tool in agent.tools}
    for child in agent.sub_agents:
        names.update(_tool_names(child))
    names.add("transfer_to_agent")
    return names


def test_every_root_and_subagent_tool_has_one_visual_binding():
    names = _tool_names(build_root_agent(REASONING_MODEL))
    assert names == set(capability_registry.MODEL_TOOL_BINDINGS)


async def test_visual_epoch_blocks_canonical_choice_but_allows_advisory_read():
    session = SimpleNamespace(id="session", user_id="workspace")
    context = SimpleNamespace(session=session, state={})
    blocked_tool = SimpleNamespace(name="choose_opportunity")
    read_tool = SimpleNamespace(name="search_attachment")
    advisory_tool = SimpleNamespace(name="search_programs")
    scoped_navigation_tool = SimpleNamespace(name="open_page")
    live_visual_context.mark(
        workspace_id="workspace", session_id="session", source="camera",
        connection_generation=1)
    try:
        blocked = await enforce_workflow_tool_contract(blocked_tool, {}, context)
        allowed = await enforce_workflow_tool_contract(read_tool, {}, context)
        advisory = await enforce_workflow_tool_contract(
            advisory_tool, {}, context)
        scoped = await enforce_workflow_tool_contract(
            scoped_navigation_tool, {}, context)
    finally:
        live_visual_context.clear(
            workspace_id="workspace", session_id="session",
            connection_generation=1)

    assert blocked["error_code"] == "needs_exact_review"
    assert allowed is None
    assert advisory is None
    assert scoped["error_code"] == "visual_scope_not_authority"


def test_visual_epoch_remains_conservative_until_every_provider_socket_closes():
    live_visual_context.mark(
        workspace_id="workspace", session_id="session", source="camera",
        connection_generation=11)
    live_visual_context.mark(
        workspace_id="workspace", session_id="session", source="display",
        connection_generation=12)
    live_visual_context.clear(
        workspace_id="workspace", session_id="session",
        connection_generation=11)
    assert live_visual_context.get("workspace", "session") is not None
    live_visual_context.clear(
        workspace_id="workspace", session_id="session",
        connection_generation=12)
    assert live_visual_context.get("workspace", "session") is None

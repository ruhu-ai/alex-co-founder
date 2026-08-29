from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.co_founder import callbacks
from agents.co_founder import state_schema as ss
from services import durable_memory, local_pilot_store
from services.actor_identity import WorkspaceRole
from services.workflow_contracts import stable_id

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_singletons():
    yield
    durable_memory.reset_configured_service_for_tests()


async def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(local_pilot_store, "PILOT_ROOT", tmp_path.resolve())
    monkeypatch.setenv("DURABLE_MEMORY_M2_LOCAL_PILOT", "1")
    monkeypatch.setenv("DURABLE_MEMORY_M2_LOCAL_ENTRY_ATTESTED", "1")
    monkeypatch.setenv("DURABLE_MEMORY_M2_ENABLED", "true")
    monkeypatch.setenv("DURABLE_MEMORY_M2_LOCAL_WORKSPACE_ID", "local_m2_pilot")
    monkeypatch.setenv("DURABLE_MEMORY_M2_LOCAL_FOUNDER_ID", "local_founder")
    monkeypatch.setenv("DURABLE_MEMORY_M2_LOCAL_DATA_PATH", str(tmp_path / "data.db"))
    monkeypatch.setenv("DURABLE_MEMORY_M2_LOCAL_LEDGER_PATH", str(tmp_path / "ledger.db"))
    monkeypatch.delenv("K_SERVICE", raising=False)
    durable_memory.reset_configured_service_for_tests()
    store, _ = local_pilot_store.configured_stores()
    membership_id = stable_id("membership", "local_m2_pilot", "local_founder")
    assert await store.create(
        "workspace_members",
        membership_id,
        {
            "schema_version": 1,
            "membership_id": membership_id,
            "workspace_id": "local_m2_pilot",
            "actor_id": "local_founder",
            "product_role": "FOUNDER",
            "status": "ACTIVE",
            "authenticated": True,
            "synthetic": True,
            "local_only": True,
            "version": 1,
        },
    )


async def test_local_pilot_resolves_one_authenticated_founder_without_legacy_role(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    await _configure(monkeypatch, tmp_path)
    principal = await durable_memory.resolve_local_pilot_founder()
    assert principal.role is WorkspaceRole.FOUNDER
    store, _ = local_pilot_store.configured_stores()
    member = await store.get("workspace_members", principal.membership_id)
    assert member["product_role"] == "FOUNDER"
    assert "role" not in member
    status = await durable_memory.configured_service().status(principal=principal)
    assert status["pilot_eligible"] is True
    assert status["local_pilot"] is True
    assert status["backend"] == "controlled-local-pilot-sqlite"


async def test_local_pilot_store_is_persistent_and_separate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    await _configure(monkeypatch, tmp_path)
    data, ledger = local_pilot_store.configured_stores()
    assert data.path != ledger.path
    assert await data.create("probe", "one", {"value": "data", "version": 1})
    assert await ledger.create("probe", "one", {"value": "ledger", "version": 1})
    local_pilot_store.reset_for_tests()
    reopened_data, reopened_ledger = local_pilot_store.configured_stores()
    assert (await reopened_data.get("probe", "one"))["value"] == "data"
    assert (await reopened_ledger.get("probe", "one"))["value"] == "ledger"


async def test_local_pilot_blocks_every_model_selected_tool(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DURABLE_MEMORY_M2_LOCAL_PILOT", "1")
    monkeypatch.delenv("K_SERVICE", raising=False)
    result = await callbacks.enforce_workflow_tool_contract(
        SimpleNamespace(name="get_pipeline"),
        {},
        SimpleNamespace(state={ss.K_ADVISORY_MEMORY: "none"}),
    )
    assert result["error_code"] == "local_m2_pilot_tool_disabled"


async def test_local_pilot_refuses_cloud_run(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DURABLE_MEMORY_M2_LOCAL_PILOT", "1")
    monkeypatch.setenv("K_SERVICE", "co-founder")
    assert local_pilot_store.local_pilot_mode() is False


async def test_ui_uses_founder_only_local_pilot_wording():
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    assert "Local-only pilot enabled for the authenticated Founder" in html
    assert "isolated local Founder pilot namespace" in html
    local_copy = html[html.index("Local-only pilot enabled") :]
    assert "local pilot operator" not in local_copy.casefold()
    assert "local pilot owner" not in local_copy.casefold()


async def test_local_pilot_has_a_dedicated_memory_only_product_surface():
    html = Path("app/static/m2-pilot.html").read_text(encoding="utf-8")
    server = Path("app/main.py").read_text(encoding="utf-8")
    assert "LOCAL SYNTHETIC PILOT" in html
    assert "Settings" in html and "What Alex knows" in html
    assert "Remember" in html
    for control in (
        "Correct",
        "Pin",
        "Forget",
        "Disable",
        "Start private session",
        "Return to standard pilot",
    ):
        assert control in html
    for forbidden_request in (
        "/api/v1/pipeline",
        "/api/v1/browser/events",
        "/api/v1/workspace-brief",
        "/api/v1/investor-outreach",
        "/api/v1/events/stream",
    ):
        assert forbidden_request not in html
    assert 'headers["X-CSRF-Token"] = csrfToken' in html
    assert 'fetch("/auth/me", {credentials: "same-origin"})' in html
    assert 'filename = "m2-pilot.html" if local_pilot_store.local_pilot_mode()' in server
    assert 'else "index.html"' in server
    assert 'headers={"Cache-Control": "no-store"}' in server

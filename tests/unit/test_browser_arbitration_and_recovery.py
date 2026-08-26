"""Service-level arbitration, crash reconciliation, and ownership fencing.

These paths existed in code with no test at all: `superseded_by_fill`, the
`browser_busy` data-error conversion, the disconnect callback that terminalizes
owned runs as `browser_crash`, the instance-ownership fence that keeps a rolling
deploy from closing another live revision's runs, and the launch breaker's
recovery half (only "it opens" was covered).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services import browser_runtime as runtime_module
from services import browser_service
from services.browser_runtime import BrowserRuntime, ContextLease

pytestmark = pytest.mark.asyncio


def _run_row(run_id: str, *, kind: str, status: str = "active",
             session_id: str = "s1", owner: str | None = None,
             expires_delta: timedelta = timedelta(minutes=5)) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "run_id": run_id, "app_name": "co_founder", "user_id": "founder",
        "session_id": session_id, "kind": kind, "status": status,
        "goal": "g", "version": 1, "frame_seq": 0, "action_count": 0,
        "lease_generation": 1, "browser_generation": 1,
        "owner_instance": browser_service.INSTANCE_ID if owner is None else owner,
        "expires_at": (now + expires_delta).isoformat(),
        "created_at": now.isoformat(), "updated_at": now.isoformat(),
    }


class _Store:
    """Minimal durable double that enforces the real transition edges."""

    EDGES = {
        "opening": {"active", "stopping"}, "active": {"blocked", "stopping"},
        "blocked": {"stopping"}, "stopping": {"closed"}, "closed": set(),
    }

    def __init__(self, rows: dict[str, dict] | None = None) -> None:
        self.rows = rows or {}
        self.audits: list[tuple] = []
        self.uncertain: list[tuple[str, str]] = []

    async def get_run(self, run_id):
        row = self.rows.get(run_id)
        return dict(row) if row else None

    async def transition(self, run_id, status, owner_loss=False, **fields):
        row = self.rows.get(run_id)
        if row is None:
            return {"ok": False, "missing": True}
        old = row.get("status")
        if old == status:
            return {"ok": True, "idempotent": True, **row}
        allowed = status in self.EDGES.get(old, set())
        if owner_loss and old in {"opening", "active", "blocked", "stopping"}:
            allowed = status == "closed"
        if not allowed:
            return {"ok": False, "from": old, "to": status}
        row.update(fields, status=status, version=int(row.get("version", 0)) + 1)
        return {"ok": True, **row}

    async def list_runs(self, app_name, user_id, session_id, kind=None):
        rows = [dict(row) for row in self.rows.values()
                if row.get("app_name") == app_name
                and row.get("user_id") == user_id
                and row.get("session_id") == session_id
                and (kind is None or row.get("kind") == kind)]
        return sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)

    async def list_nonterminal(self, owner_instance=None):
        return [dict(row) for row in self.rows.values()
                if row.get("status") in {"opening", "active", "blocked", "stopping"}
                and (owner_instance is None
                     or row.get("owner_instance") == owner_instance)]

    async def mark_uncertain(self, run_id, reason):
        self.uncertain.append((run_id, reason))
        return 1

    async def audit(self, *args, **kwargs):
        self.audits.append(args)
        return "a1"


@pytest.fixture(autouse=True)
def _restore_resolver():
    """set_resolver is a module global — never let it leak into another file."""
    yield
    browser_service.set_resolver(None)


@pytest.fixture
def store(monkeypatch):
    st = _Store()
    monkeypatch.setattr(browser_service.firestore, "get_browser_run", st.get_run)
    monkeypatch.setattr(
        browser_service.firestore, "transition_browser_run", st.transition)
    monkeypatch.setattr(
        browser_service.firestore, "list_nonterminal_browser_runs", st.list_nonterminal)
    monkeypatch.setattr(browser_service.firestore, "list_browser_runs", st.list_runs)
    monkeypatch.setattr(
        browser_service.firestore,
        "mark_prepared_browser_actions_uncertain", st.mark_uncertain)
    monkeypatch.setattr(browser_service.firestore, "audit", st.audit)
    return st


# ---------------------------------------------------------------------------
# Foreground arbitration
# ---------------------------------------------------------------------------

async def test_starting_fill_supersedes_research_with_the_typed_reason(
        store, monkeypatch):
    """docs/22: registering a fill closes an active browse as
    superseded_by_fill BEFORE the credentialed context is created."""
    store.rows["browse-1"] = _run_row("browse-1", kind="browse")
    closed: list[tuple[str, str, str]] = []

    async def fake_close(run_id, reason, actor, **_kw):
        closed.append((run_id, reason, actor))
        store.rows[run_id]["status"] = "closed"
        return {"status": "success"}

    monkeypatch.setattr(browser_service, "close_run", fake_close)
    # Stop before context creation: only arbitration is under test here.
    monkeypatch.setattr(
        browser_service.firestore, "create_browser_run",
        lambda record: (_ for _ in ()).throw(RuntimeError("stop-after-arbitration")))

    with pytest.raises(RuntimeError, match="stop-after-arbitration"):
        await browser_service._start_fill_context(
            "https://portal.example/apply",
            session_key={"app_name": "co_founder", "user_id": "founder",
                         "session_id": "s1"},
            application_id="app-1", goal="fill", phase="authenticating")

    assert closed == [("browse-1", "superseded_by_fill", "agent:form_filler")]


async def test_research_during_fill_returns_browser_busy_and_creates_no_context(
        store, monkeypatch):
    """The data-error conversion (not the raw runtime exception) is the
    contract the model sees — and no context may be created on that path."""
    store.rows["fill-1"] = _run_row("fill-1", kind="fill")
    created = []
    monkeypatch.setattr(
        browser_service.firestore, "create_browser_run",
        lambda record: created.append(record))
    # Network policy legitimately runs before arbitration; let the URL pass so
    # the busy branch is the one under test.
    monkeypatch.setenv("BROWSE_OPEN_WEB", "true")
    browser_service.set_resolver(lambda _host, _port: ["93.184.216.34"])

    result = await browser_service.open_run(
        {"app_name": "co_founder", "user_id": "founder", "session_id": "s1"},
        "https://example.org/page", "research")

    assert result["status"] == "error" and result["code"] == "browser_busy"
    assert result["active_run"]["run_id"] == "fill-1"
    assert created == []


# ---------------------------------------------------------------------------
# Crash reconciliation
# ---------------------------------------------------------------------------

async def test_disconnect_terminalizes_each_owned_run_once_as_browser_crash(store):
    """The production disconnect callback: every owned nonterminal run closes
    exactly once with UNCERTAIN ledger rows, and a replay is a no-op."""
    store.rows["r1"] = _run_row("r1", kind="browse")
    store.rows["r2"] = _run_row("r2", kind="fill", session_id="s2")
    leases = [
        ContextLease(lease_id="l1", run_id="r1", kind="browse",
                     session_key=("co_founder", "founder", "s1"),
                     application_id=None, phase=None, context=None, page=None,
                     browser_generation=1, opened_at=0.0, last_activity_at=0.0),
        ContextLease(lease_id="l2", run_id="r2", kind="fill",
                     session_key=("co_founder", "founder", "s2"),
                     application_id="app-1", phase="filling", context=None,
                     page=None, browser_generation=1, opened_at=0.0,
                     last_activity_at=0.0),
    ]

    await browser_service._reconcile_lost_leases(leases)

    assert store.rows["r1"]["status"] == "closed"
    assert store.rows["r1"]["close_reason"] == "browser_crash"
    assert store.rows["r2"]["close_reason"] == "browser_crash"
    assert sorted(store.uncertain) == [("r1", "browser_crash"), ("r2", "browser_crash")]

    await browser_service._reconcile_lost_leases(leases)   # replay
    assert len(store.uncertain) == 2  # already closed → skipped, not re-marked


# ---------------------------------------------------------------------------
# Instance-ownership fence (rolling deploy safety)
# ---------------------------------------------------------------------------

async def test_startup_never_closes_another_live_instances_run(store):
    """A rolling deploy overlaps revisions. Closing a foreign instance's
    unexpired run would leave credentialed work executing while the founder's
    observation plane reports `closed`."""
    store.rows["mine"] = _run_row("mine", kind="browse")
    store.rows["theirs"] = _run_row(
        "theirs", kind="fill", session_id="s9", owner="other-revision:abc123")

    await browser_service.reconcile_all_runs()

    assert store.rows["mine"]["status"] == "closed"
    assert store.rows["mine"]["close_reason"] == "restart"
    assert store.rows["theirs"]["status"] == "active"   # untouched


async def test_startup_reclaims_a_foreign_run_once_its_lease_has_lapsed(store):
    """Once the durable lease expires, the owner is provably gone (a live owner
    renews on every successful action), so the run is safe to terminalize."""
    store.rows["stale"] = _run_row(
        "stale", kind="browse", owner="dead-revision:xyz",
        expires_delta=timedelta(minutes=-1))

    await browser_service.reconcile_all_runs()

    assert store.rows["stale"]["status"] == "closed"


def test_dead_predecessor_of_the_same_revision_is_reclaimable_immediately():
    """--max-instances 1 means one revision has one instance, so a different
    process id under MY revision is a dead predecessor. This is what keeps
    'kill the server mid-run → restart → closed/restart' immediate (docs/18)
    without weakening the cross-revision fence."""
    now = datetime.now(timezone.utc)
    revision = browser_service.INSTANCE_ID.split(":", 1)[0]
    predecessor = _run_row("r", kind="browse", owner=f"{revision}:deadbeef00",
                           expires_delta=timedelta(minutes=30))
    assert browser_service._reclaimable_orphan(predecessor, now)

    foreign = _run_row("r2", kind="fill", owner="other-revision:abc",
                       expires_delta=timedelta(minutes=30))
    assert not browser_service._reclaimable_orphan(foreign, now)


def test_a_stamped_run_with_unreadable_expiry_is_never_assumed_dead():
    now = datetime.now(timezone.utc)
    row = _run_row("r", kind="fill", owner="other-revision:abc")
    row["expires_at"] = "not-a-timestamp"
    assert not browser_service._reclaimable_orphan(row, now)


async def test_per_session_reconcile_honours_the_same_ownership_fence(
        store, monkeypatch):
    """reconcile_session runs before EVERY agent turn — the hottest path. An
    agent request landing on a new revision mid-deploy must not close the old
    revision's live fill while its credentialed page is still executing."""
    store.rows["theirs"] = _run_row(
        "theirs", kind="fill", owner="other-revision:abc123")
    monkeypatch.setattr(
        browser_service, "browser_status_projection",
        lambda _key: asyncio.sleep(0, result={}))

    await browser_service.reconcile_session(
        {"app_name": "co_founder", "user_id": "founder", "session_id": "s1"})

    assert store.rows["theirs"]["status"] == "active"


async def test_per_session_reconcile_closes_own_and_lapsed_orphans(
        store, monkeypatch):
    store.rows["mine"] = _run_row("mine", kind="browse")
    store.rows["lapsed"] = _run_row(
        "lapsed", kind="browse", owner="dead:1",
        expires_delta=timedelta(minutes=-1))
    monkeypatch.setattr(
        browser_service, "browser_status_projection",
        lambda _key: asyncio.sleep(0, result={}))

    await browser_service.reconcile_session(
        {"app_name": "co_founder", "user_id": "founder", "session_id": "s1"})

    assert store.rows["mine"]["status"] == "closed"
    assert store.rows["lapsed"]["status"] == "closed"


# ---------------------------------------------------------------------------
# Egress allowlist: exact entries must NOT become subdomain wildcards
# ---------------------------------------------------------------------------

def test_exact_allowlist_entries_do_not_authorize_subdomains(monkeypatch):
    """The earlier bug: every allowed host was also treated as a suffix, so
    allowing `portal.example` silently allowed an attacker-owned
    `collector.portal.example` to receive a typed credential."""
    monkeypatch.setenv("K_SERVICE", "co-founder")
    monkeypatch.delenv("PORTAL_ALLOWED_HOSTS", raising=False)
    allowed = browser_service._credentialed_allowed_hosts("portal.example")

    assert browser_service._host_allowed("portal.example", allowed)
    assert not browser_service._host_allowed("collector.portal.example", allowed)
    assert not browser_service._host_allowed("portal.example.evil.test", allowed)


def test_wildcard_entries_authorize_subdomains_and_their_apex(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "co-founder")
    monkeypatch.setenv("PORTAL_ALLOWED_HOSTS", "*.idp.example, verify.example")
    allowed = browser_service._credentialed_allowed_hosts("portal.example")

    assert browser_service._host_allowed("sso.idp.example", allowed)
    assert browser_service._host_allowed("idp.example", allowed)   # apex
    assert browser_service._host_allowed("verify.example", allowed)
    assert not browser_service._host_allowed("api.verify.example", allowed)  # exact
    assert not browser_service._host_allowed("attacker.example", allowed)


def test_host_matching_normalizes_case_trailing_dot_and_idn(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "co-founder")
    monkeypatch.delenv("PORTAL_ALLOWED_HOSTS", raising=False)
    allowed = browser_service._credentialed_allowed_hosts("portal.example")

    assert browser_service._host_allowed("PORTAL.example", allowed)
    assert browser_service._host_allowed("portal.example.", allowed)
    # a unicode IDN entry and its punycode spelling must agree
    idn = browser_service._HostAllowlist({"bücher.example"})
    assert browser_service._host_allowed("xn--bcher-kva.example", idn)


def test_declared_idp_allows_read_redirects_but_never_cross_origin_writes(
        monkeypatch):
    monkeypatch.setenv("K_SERVICE", "co-founder")
    monkeypatch.setenv("PORTAL_ALLOWED_HOSTS", "login.idp.example")
    allowed = browser_service._credentialed_allowed_hosts("portal.example")

    assert not browser_service._credentialed_request_refused(
        "GET", "login.idp.example", "portal.example", allowed)
    assert not browser_service._credentialed_request_refused(
        "HEAD", "login.idp.example", "portal.example", allowed)
    assert browser_service._credentialed_request_refused(
        "POST", "login.idp.example", "portal.example", allowed)
    assert browser_service._credentialed_request_refused(
        "GET", "collector.portal.example", "portal.example", allowed)


# ---------------------------------------------------------------------------
# Launch circuit breaker — the recovery half
# ---------------------------------------------------------------------------

async def test_breaker_half_open_probe_then_success_resets(monkeypatch):
    """Only "it opens after 3 failures" was covered. Recovery matters just as
    much: after the open interval exactly one caller probes, and a success
    clears the failure history."""
    breaker = runtime_module._LaunchBreaker()
    now = 1_000.0
    for _ in range(3):
        breaker.failure(now)
    assert breaker.refusal(now) == "circuit_open"
    assert breaker.refusal(now + 29) == "circuit_open"

    after = now + 31
    assert breaker.refusal(after) is None      # first caller probes
    assert breaker.refusal(after) == "circuit_open"  # others still refused

    breaker.success()
    assert breaker.refusal(after) is None and breaker.failures == []


async def test_breaker_failure_window_prunes_old_failures():
    """Three failures spread beyond 60 s must not open the circuit."""
    breaker = runtime_module._LaunchBreaker()
    breaker.failure(0.0)
    breaker.failure(40.0)
    breaker.failure(200.0)     # first two are outside the window
    assert breaker.refusal(200.0) is None


async def test_intentional_shutdown_does_not_charge_the_breaker():
    """A clean shutdown disconnects the browser; charging that to the breaker
    would open the circuit after three ordinary restarts."""
    rt = BrowserRuntime()
    sentinel = object()
    rt._browser = sentinel
    rt._generation = 4
    rt._shutdown_intent = True

    await rt._handle_disconnect(sentinel, 4)

    assert rt._breaker.failures == []
    assert rt._browser is sentinel   # intent guard short-circuits before detach


async def test_stale_generation_disconnect_cannot_charge_the_breaker():
    rt = BrowserRuntime()
    rt._browser = object()
    rt._generation = 7

    await rt._handle_disconnect(object(), 6)   # old browser, old generation

    assert rt._breaker.failures == []


# ---------------------------------------------------------------------------
# Watchdog robustness (a raising policy callback must still contain the page)
# ---------------------------------------------------------------------------

async def test_dialog_is_dismissed_even_when_the_policy_callback_raises():
    """An audit/freeze write can fail (store outage). The modal must still be
    dismissed or the page hangs behind it while the run looks healthy."""
    rt = BrowserRuntime()
    lease = ContextLease(
        lease_id="l", run_id="r", kind="browse", session_key=None,
        application_id=None, phase=None, context=None, page=None,
        browser_generation=1, opened_at=0.0, last_activity_at=0.0)
    dismissed = asyncio.Event()

    class _Dialog:
        async def dismiss(self):
            dismissed.set()

    handlers: dict[str, object] = {}

    class _Page:
        def on(self, event, handler):
            handlers[event] = handler

    async def exploding_policy(_lease, _dialog, _event):
        raise RuntimeError("firestore unavailable")

    rt._install_page_guards(lease, _Page(), on_dialog=exploding_policy,
                            on_download=None)
    handlers["dialog"](_Dialog())
    await asyncio.wait_for(dismissed.wait(), timeout=1)
    # the failure is surfaced, not swallowed
    await asyncio.gather(*list(rt._tasks), return_exceptions=True)


async def test_download_is_cancelled_even_when_the_policy_callback_raises():
    rt = BrowserRuntime()
    lease = ContextLease(
        lease_id="l", run_id="r", kind="fill", session_key=None,
        application_id="a", phase="filling", context=None, page=None,
        browser_generation=1, opened_at=0.0, last_activity_at=0.0)
    cancelled = asyncio.Event()

    class _Download:
        async def cancel(self):
            cancelled.set()

        async def delete(self):
            pass

    handlers: dict[str, object] = {}

    class _Page:
        def on(self, event, handler):
            handlers[event] = handler

    async def exploding_policy(_lease, _download, _event):
        raise RuntimeError("audit failed")

    rt._install_page_guards(lease, _Page(), on_dialog=None,
                            on_download=exploding_policy)
    handlers["download"](_Download())
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    await asyncio.gather(*list(rt._tasks), return_exceptions=True)


# ---------------------------------------------------------------------------
# Credentialed egress allowlist
# ---------------------------------------------------------------------------

async def test_failed_acquisition_recycles_the_generation_on_a_close_hang():
    """A context whose setup failed and whose close() HANGS must not simply be
    dropped: the unsupervised context could keep issuing requests behind a
    terminal run. Same generation recycle the normal close path performs."""
    rt = BrowserRuntime()
    rt._browser = object()
    rt._generation = 3
    recycled: list[int] = []

    async def fake_recycle(generation):
        recycled.append(generation)
        return []

    rt.recycle_generation = fake_recycle   # type: ignore[assignment]

    class _HangingContext:
        async def close(self):
            await asyncio.sleep(3600)

    lease = ContextLease(
        lease_id="l1", run_id="r1", kind="fill", session_key=None,
        application_id="app", phase="authenticating", context=_HangingContext(),
        page=None, browser_generation=3, opened_at=0.0, last_activity_at=0.0)
    rt._leases[lease.lease_id] = lease

    import services.browser_runtime as rt_mod
    original = rt_mod.CLOSE_TIMEOUT_SECONDS
    rt_mod.CLOSE_TIMEOUT_SECONDS = 0.05
    try:
        await rt._close_failed_lease(lease)
    finally:
        rt_mod.CLOSE_TIMEOUT_SECONDS = original

    assert recycled == [3]
    assert lease.lease_id not in rt._leases


@pytest.mark.parametrize("normal_close", [False, True])
async def test_any_context_close_exception_recycles_the_generation(normal_close):
    """A raised close is just as unproven as a timeout: detaching the lease
    without killing Chromium could leave credentialed network activity alive."""
    rt = BrowserRuntime()
    rt._browser = object()
    rt._generation = 9
    recycled: list[int] = []

    async def fake_recycle(generation):
        recycled.append(generation)
        return []

    rt.recycle_generation = fake_recycle  # type: ignore[assignment]

    class _RaisingContext:
        async def close(self):
            raise RuntimeError("driver disconnected during close")

    lease = ContextLease(
        lease_id="raise", run_id="r-raise", kind="fill", session_key=None,
        application_id="app", phase="filling", context=_RaisingContext(),
        page=None, browser_generation=9, opened_at=0.0, last_activity_at=0.0)
    rt._leases[lease.lease_id] = lease
    rt._run_leases[lease.run_id] = lease.lease_id

    if normal_close:
        await rt.close_context_lease(lease)
    else:
        await rt._close_failed_lease(lease)

    assert recycled == [9]
    assert rt.lease_for_run("r-raise") is None


async def test_policy_event_counter_bumps_synchronously_before_the_handler():
    """The once-gate: an action must be able to detect a dialog/download that
    fired during it even if the async policy handler has not run yet."""
    rt = BrowserRuntime()
    lease = ContextLease(
        lease_id="l", run_id="r", kind="browse", session_key=None,
        application_id=None, phase=None, context=None, page=None,
        browser_generation=1, opened_at=0.0, last_activity_at=0.0)
    released = asyncio.Event()
    handlers: dict[str, object] = {}

    class _Page:
        def on(self, event, handler):
            handlers[event] = handler

    class _Dialog:
        type = "confirm"
        message = "are you sure?"

        async def dismiss(self):
            released.set()

    async def slow_policy(_lease, _dialog, _event):
        await asyncio.sleep(0.2)      # handler deliberately lags the action

    rt._install_page_guards(lease, _Page(), on_dialog=slow_policy,
                            on_download=None)
    before = lease.policy_event_seq
    handlers["dialog"](_Dialog())
    # No await yet: the counter must ALREADY reflect the event.
    assert lease.policy_event_seq == before + 1
    await asyncio.wait_for(released.wait(), timeout=2)
    await asyncio.gather(*list(rt._tasks), return_exceptions=True)


async def test_action_once_gate_selects_exactly_one_dialog_or_success_winner():
    rt = BrowserRuntime()
    lease = ContextLease(
        lease_id="gate", run_id="run", kind="browse", session_key=None,
        application_id=None, phase=None, context=None, page=None,
        browser_generation=0, opened_at=0.0, last_activity_at=0.0)
    rt._leases[lease.lease_id] = lease
    rt._run_leases["run"] = lease.lease_id
    handlers: dict[str, object] = {}
    observed = []

    class _Page:
        def on(self, event, handler):
            handlers[event] = handler

    class _Dialog:
        type = "confirm"
        async def dismiss(self):
            pass

    async def policy(_lease, _dialog, event):
        observed.append(event)

    rt._install_page_guards(
        lease, _Page(), on_dialog=policy, on_download=None)
    assert rt.begin_action("run", "action-1")
    handlers["dialog"](_Dialog())
    claimed, winner = rt.claim_action_completion(
        "run", "action-1", "SUCCEEDED")
    assert claimed is False and winner == "UNCERTAIN"
    await asyncio.gather(*list(rt._tasks), return_exceptions=True)
    assert observed[0].action_id == "action-1" and observed[0].claimed is True

    rt.finish_action("run", "action-1")
    assert rt.begin_action("run", "action-2")
    claimed, winner = rt.claim_action_completion(
        "run", "action-2", "SUCCEEDED")
    assert claimed is True and winner == "SUCCEEDED"
    handlers["dialog"](_Dialog())
    await asyncio.gather(*list(rt._tasks), return_exceptions=True)
    assert observed[-1].action_id == "action-2"
    assert observed[-1].claimed is False
    assert rt.action_completion("run", "action-2") == "SUCCEEDED"


async def test_shutdown_terminalization_is_concurrent_and_bounded(monkeypatch):
    """Shutdown must finish inside the platform grace window.

    The old path called close_run per run, sequentially: 2 s action-cancel +
    5 s final evidence + 5 s context close, EACH. Several runs blew past
    SIGKILL, so nothing was terminalized at all. Ownership is now recorded
    first, per-run budgeted, and all runs proceed concurrently.
    """
    async def hangs(*_args, **_kwargs):
        await asyncio.sleep(30)
        return {"ok": True}

    monkeypatch.setattr(
        browser_service.firestore,
        "mark_prepared_browser_actions_uncertain", hangs)
    monkeypatch.setattr(browser_service, "_transition_run", hangs)

    started = asyncio.get_running_loop().time()
    await asyncio.gather(*(
        browser_service._terminalize_for_shutdown(f"r{i}") for i in range(4)))
    elapsed = asyncio.get_running_loop().time() - started

    budget = browser_service.SHUTDOWN_RUN_BUDGET_SECONDS
    # Concurrency is the point: N hung runs cost ONE budget, not N of them.
    assert elapsed < budget * 2, "runs were terminalized sequentially"
    # Uvicorn drains requests for 2 s; the complete browser cleanup then owns a
    # reviewed 5.5 s budget, both inside Cloud Run's ~10 s termination window.
    assert budget <= browser_service.SHUTDOWN_TOTAL_BUDGET_SECONDS
    assert 2 + browser_service.SHUTDOWN_TOTAL_BUDGET_SECONDS < 10


async def test_runtime_shutdown_has_one_global_deadline_and_detaches_first():
    rt = BrowserRuntime()

    class _Hangs:
        async def close(self):
            await asyncio.sleep(30)
        async def stop(self):
            await asyncio.sleep(30)

    context = _Hangs()
    lease = ContextLease(
        lease_id="shutdown", run_id="r", kind="browse", session_key=None,
        application_id=None, phase=None, context=context, page=None,
        browser_generation=1, opened_at=0.0, last_activity_at=0.0)
    rt._generation = 1
    rt._browser = _Hangs()
    rt._playwright = _Hangs()
    rt._leases[lease.lease_id] = lease
    rt._run_leases["r"] = lease.lease_id

    started = asyncio.get_running_loop().time()
    await rt.shutdown(timeout_seconds=0.05)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.5
    assert rt.leases() == ()
    assert rt._browser is None and rt._playwright is None


async def test_run_creation_fails_closed_when_durable_expiry_cannot_schedule(
        store, monkeypatch):
    """A run must never go live without a durable task that can reclaim it."""
    async def failing_schedule(*_args, **_kwargs):
        return {"status": "error", "error": True, "message": "queue unavailable"}

    monkeypatch.setattr(browser_service.browser_expiry, "schedule", failing_schedule)
    result = await browser_service._schedule_run_expiry(
        _run_row("r1", kind="browse"))
    assert result.get("status") == "error"


async def test_expiry_renewal_enqueue_failure_preserves_previous_generation(
        monkeypatch):
    row = _run_row("renew", kind="browse")
    renew_calls = []

    async def get_run(_run_id):
        return dict(row)

    async def renew(*args, **kwargs):
        renew_calls.append((args, kwargs))
        return {"ok": True}

    async def schedule(_run):
        return {"status": "error", "error": True, "message": "queue down"}

    async def audit(*_args, **_kwargs):
        return "audit"

    monkeypatch.setattr(browser_service.firestore, "get_browser_run", get_run)
    monkeypatch.setattr(browser_service.firestore, "renew_browser_lease", renew)
    monkeypatch.setattr(browser_service.firestore, "audit", audit)
    monkeypatch.setattr(browser_service, "_schedule_run_expiry", schedule)

    result = await browser_service.renew_run_expiry("renew")

    assert result["expiry_renewed"] is False
    assert result["lease_generation"] == 1
    assert result["expires_at"] == row["expires_at"]
    assert renew_calls == []  # Firestore was never advanced to an unscheduled lease


def test_credentialed_allowlist_covers_portal_and_declared_origins_only(
        monkeypatch):
    monkeypatch.setenv("K_SERVICE", "co-founder")   # production posture
    monkeypatch.setenv("PORTAL_ALLOWED_HOSTS", "*.idp.example, verify.example")
    allowed = browser_service._credentialed_allowed_hosts("portal.example")

    assert browser_service._host_allowed("portal.example", allowed)
    assert browser_service._host_allowed("sso.idp.example", allowed)
    assert browser_service._host_allowed("verify.example", allowed)
    # the exfiltration beacon a hostile portal page would use
    assert not browser_service._host_allowed("attacker.example", allowed)
    assert not browser_service._host_allowed("", allowed)
    # loopback is NOT silently allowed in production
    assert not browser_service._host_allowed("127.0.0.1", allowed)

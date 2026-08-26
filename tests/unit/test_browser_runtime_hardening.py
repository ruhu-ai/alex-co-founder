"""Process ownership, quota, and containment checks for docs/22."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.browser_runtime import (
    BrowserCapacityExceeded,
    BrowserForegroundBusy,
    BrowserRuntime,
    BrowserRuntimeUnavailable,
)

pytestmark = pytest.mark.asyncio


class FakePage:
    def __init__(self):
        self.handlers = {}
        self.closed = False

    def on(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, order, *, hanging_close=False):
        self.order = order
        self.handlers = {}
        self.page = FakePage()
        self.closed = False
        self.hanging_close = hanging_close

    def on(self, event, callback):
        self.order.append(f"on:{event}")
        self.handlers[event] = callback

    async def route(self, _pattern, _handler):
        self.order.append("route")

    async def new_page(self):
        self.order.append("new_page")
        return self.page

    async def close(self):
        if self.hanging_close:
            await asyncio.Event().wait()
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.handlers = {}
        self.contexts = []
        self.options = []
        self.order = []
        self.closed = False

    def is_connected(self):
        return not self.closed

    def on(self, event, callback):
        self.handlers[event] = callback

    async def new_context(self, **options):
        self.order.append("new_context")
        self.options.append(options)
        context = FakeContext(self.order)
        self.contexts.append(context)
        return context

    async def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self, browser, counts, *, fail=False):
        self.browser = browser
        self.counts = counts
        self.fail = fail
        self.stopped = False
        self.chromium = self

    async def launch(self, *, headless):
        self.counts["launch"] += 1
        assert headless is True
        if self.fail:
            raise RuntimeError("launch failed")
        return self.browser

    async def stop(self):
        self.stopped = True


class FakeManager:
    def __init__(self, playwright, counts):
        self.playwright = playwright
        self.counts = counts

    async def start(self):
        self.counts["start"] += 1
        await asyncio.sleep(0)
        return self.playwright


def install_playwright(monkeypatch, *, fail=False):
    import playwright.async_api as api

    counts = {"start": 0, "launch": 0}
    browser = FakeBrowser()
    playwright = FakePlaywright(browser, counts, fail=fail)
    manager = FakeManager(playwright, counts)
    monkeypatch.setattr(api, "async_playwright", lambda: manager)
    return browser, playwright, counts


async def test_twenty_cold_callers_share_one_launch(monkeypatch):
    browser, _playwright, counts = install_playwright(monkeypatch)
    runtime = BrowserRuntime()
    results = await asyncio.gather(*(runtime.get_browser() for _ in range(20)))
    assert all(item is browser for item in results)
    assert counts == {"start": 1, "launch": 1}
    assert runtime.generation == 1
    await runtime.shutdown()


async def test_guards_and_download_policy_precede_first_page(monkeypatch):
    browser, _playwright, _counts = install_playwright(monkeypatch)
    runtime = BrowserRuntime()

    async def noop(*_args):
        return None

    lease = await runtime.acquire_context(
        kind="browse",
        run_id="run-1",
        session_key=("co_founder", "founder", "session-1"),
        context_options={"accept_downloads": True},
        route_handler=noop,
        on_popup=noop,
        on_dialog=noop,
        on_download=noop,
    )
    assert browser.options[0]["accept_downloads"] is False
    assert browser.options[0]["viewport"] == {"width": 1280, "height": 720}
    assert browser.order.index("on:page") < browser.order.index("new_page")
    assert browser.order.index("route") < browser.order.index("new_page")
    assert set(lease.page.handlers) == {"dialog", "download"}
    await runtime.shutdown()


async def test_foreground_and_render_quotas_refuse_without_retry(monkeypatch):
    _browser, _playwright, counts = install_playwright(monkeypatch)
    runtime = BrowserRuntime()
    await runtime.acquire_context(
        kind="browse", run_id="b1",
        session_key=("co_founder", "founder", "s1")
    )
    with pytest.raises(BrowserForegroundBusy):
        await runtime.acquire_context(
            kind="fill", run_id="f1", application_id="app1",
            session_key=("co_founder", "founder", "s1")
        )
    render = await runtime.acquire_context(
        kind="render", run_id=None, session_key=None
    )
    with pytest.raises(BrowserCapacityExceeded):
        await runtime.acquire_context(kind="render", run_id=None, session_key=None)
    assert counts == {"start": 1, "launch": 1}
    await runtime.close_context_lease(render)
    await runtime.shutdown()


async def test_three_failures_open_circuit_without_fourth_launch(monkeypatch):
    _browser, _playwright, counts = install_playwright(monkeypatch, fail=True)
    runtime = BrowserRuntime()
    for _ in range(3):
        with pytest.raises(BrowserRuntimeUnavailable) as failure:
            await runtime.get_browser()
        assert failure.value.reason == "launch_failed"
    with pytest.raises(BrowserRuntimeUnavailable) as failure:
        await runtime.get_browser()
    assert failure.value.reason == "circuit_open"
    assert counts == {"start": 3, "launch": 3}


async def test_stale_disconnect_cannot_detach_new_generation(monkeypatch):
    first, first_pw, _counts = install_playwright(monkeypatch)
    runtime = BrowserRuntime()
    await runtime.get_browser()
    first_generation = runtime.generation
    await runtime.recycle_generation(first_generation)

    second = FakeBrowser()
    counts = {"start": 0, "launch": 0}
    second_pw = FakePlaywright(second, counts)
    import playwright.async_api as api
    monkeypatch.setattr(api, "async_playwright", lambda: FakeManager(second_pw, counts))
    assert await runtime.get_browser() is second
    await runtime._handle_disconnect(first, first_generation)
    assert await runtime.get_browser() is second
    assert first_pw.stopped is True
    await runtime.shutdown()


async def test_popup_dialog_and_download_watchdogs_close_or_dismiss(monkeypatch):
    _browser, _playwright, _counts = install_playwright(monkeypatch)
    runtime = BrowserRuntime()
    seen = []

    async def popup(_lease, page):
        seen.append(("popup", page))

    async def dialog(_lease, value, _event):
        seen.append(("dialog", value))
        await value.dismiss()

    async def download(_lease, value, _event):
        seen.append(("download", value))

    lease = await runtime.acquire_context(
        kind="fill", run_id="fill-1", application_id="app-1",
        session_key=("co_founder", "founder", "s1"),
        on_popup=popup, on_dialog=dialog, on_download=download,
    )
    hidden = FakePage()
    lease.context.handlers["page"](hidden)
    dialog_value = SimpleNamespace(dismissed=False)

    async def dismiss():
        dialog_value.dismissed = True

    dialog_value.dismiss = dismiss
    download_value = SimpleNamespace(cancelled=False, deleted=False)

    async def cancel():
        download_value.cancelled = True

    async def delete():
        download_value.deleted = True

    download_value.cancel = cancel
    download_value.delete = delete
    lease.page.handlers["dialog"][0](dialog_value)
    lease.page.handlers["download"][0](download_value)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert hidden.closed is True
    assert set(hidden.handlers) == {"dialog", "download"}
    assert dialog_value.dismissed is True
    assert download_value.cancelled is True and download_value.deleted is True
    assert [item[0] for item in seen] == ["popup", "dialog", "download"]
    await runtime.shutdown()


async def test_context_close_timeout_recycles_generation_and_siblings(
    monkeypatch
):
    monkeypatch.setattr("services.browser_runtime.CLOSE_TIMEOUT_SECONDS", 0.01)
    browser, _playwright, _counts = install_playwright(monkeypatch)
    runtime = BrowserRuntime()
    reconciled = []

    async def reconcile(leases):
        reconciled.extend(leases)

    runtime.configure_disconnect_callback(reconcile)
    first = await runtime.acquire_context(
        kind="browse", run_id="r1",
        session_key=("co_founder", "founder", "s1")
    )
    second = await runtime.acquire_context(
        kind="browse", run_id="r2",
        session_key=("co_founder", "founder", "s2")
    )
    first.context.hanging_close = True
    generation = first.browser_generation
    await runtime.close_lease("r1")
    assert runtime.leases() == ()
    assert browser.closed is True
    assert runtime.generation > generation
    assert [lease.run_id for lease in reconciled] == [second.run_id]


async def test_cancelled_launch_stops_partial_playwright(monkeypatch):
    import playwright.async_api as api

    counts = {"start": 0, "launch": 0}
    gate = asyncio.Event()

    class WaitingPlaywright(FakePlaywright):
        async def launch(self, *, headless):
            counts["launch"] += 1
            await gate.wait()

    playwright = WaitingPlaywright(FakeBrowser(), counts)
    monkeypatch.setattr(
        api, "async_playwright", lambda: FakeManager(playwright, counts)
    )
    runtime = BrowserRuntime()
    task = asyncio.create_task(runtime.get_browser())
    while counts["launch"] == 0:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert playwright.stopped is True
    assert runtime._browser is None and runtime._playwright is None

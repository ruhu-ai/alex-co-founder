"""Browser service (docs/05, 09) — Playwright session, page signatures,
fills, screenshots, idempotent submit. One browser per process; a fresh
context per fill run. All failures return error dicts.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

_playwright = None
_browser = None


async def get_browser():
    global _playwright, _browser
    if _browser is None:
        from playwright.async_api import async_playwright

        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=os.environ.get("HEADLESS", "true").lower() == "true"
        )
    return _browser


async def new_context():
    browser = await get_browser()
    return await browser.new_context()


def page_signature(field_names: list[str]) -> str:
    """The staleness signature: a hash of the form's field names (docs/09)."""
    return "sha256:" + hashlib.sha256("|".join(sorted(field_names)).encode()).hexdigest()[:16]


async def render_text(url: str) -> str | None:
    """JS-shell fallback for the scout (docs/08): render and return body text."""
    context = await new_context()
    try:
        page = await context.new_page()
        await page.goto(url, timeout=30000, wait_until="networkidle")
        return await page.inner_text("body")
    except Exception:
        return None
    finally:
        await context.close()


async def inspect(page) -> dict:
    """Read the form's fields from the DOM (Tier 0 source of truth)."""
    fields = await page.eval_on_selector_all(
        "input, textarea, select",
        """els => els.map(e => ({
            name: e.name || e.id || "",
            label: (e.labels && e.labels[0] ? e.labels[0].innerText : "") || e.placeholder || "",
            type: e.tagName.toLowerCase() === "select" ? "select" : (e.type || e.tagName.toLowerCase()),
            required: e.required
        }))""",
    )
    fields = [f for f in fields if f["name"] and f["type"] not in ("hidden", "submit", "button")]
    return {"status": "success", "fields": fields, "signature": page_signature([f["name"] for f in fields])}


async def fill(page, mapping: dict[str, str], attachments: dict[str, str]) -> dict:
    """Fill by field name. Per-field try/catch — partial success is first-class."""
    filled, needs_human = [], []
    for name, value in mapping.items():
        try:
            field_type = await page.eval_on_selector(
                f"[name='{name}']",
                "e => e.tagName.toLowerCase() === 'select' ? 'select' : (e.type || e.tagName.toLowerCase())"
            )
            if field_type == "file":
                if name in attachments:
                    await page.set_input_files(f"[name='{name}']", attachments[name])
                    filled.append(name)
                else:
                    needs_human.append({"field": name, "reason": "file upload — choose the file"})
            elif field_type == "select":
                await page.select_option(f"[name='{name}']", label=value)
                filled.append(name)
            elif field_type in ("checkbox", "radio"):
                needs_human.append({"field": name, "reason": f"{field_type} needs founder judgment"})
            else:
                await page.fill(f"[name='{name}']", value)
                filled.append(name)
        except Exception:
            needs_human.append({"field": name, "reason": "not found or not fillable"})
    return {"status": "success", "filled": len(filled), "filled_fields": filled,
            "needs_human": needs_human}


async def screenshot(page, path: str) -> str:
    await page.screenshot(path=path, full_page=True)
    return path


async def submit(page, idempotency_key: str) -> dict:
    """Click submit with the derived Idempotency-Key. The portal dedupes
    (docs/05, 09) — a retry returns the ORIGINAL confirmation."""
    await page.set_extra_http_headers({"Idempotency-Key": idempotency_key})
    try:
        await page.click("button[type='submit'], input[type='submit']")
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"submit click failed: {exc}"}
    body = await page.inner_text("body")
    for line in body.splitlines():
        if "confirmation" in line.lower() and ":" in line:
            return {"status": "success", "confirmation_id": line.split(":", 1)[1].strip()}
    return {"status": "error", "error": True,
            "message": "submitted but no confirmation id found on result page"}


async def register(portal_url: str, email: str, password: str) -> dict:
    """Open a portal's signup page and create an account (docs/17).

    Heuristic, email+password only: finds the email + password fields on a
    signup/register page and submits. SSO-only or bot-challenged pages return
    blockers as data — the agent never improvises around them."""
    context = await new_context()
    try:
        page = await context.new_page()
        base = portal_url.rstrip("/")
        found = False
        for candidate in (f"{base}/signup", f"{base}/register", f"{base}/auth/signup", base):
            try:
                await page.goto(candidate, timeout=15000)
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                continue
            if await page.query_selector("input[type='password']"):
                found = True
                break
        body = (await page.inner_text("body"))[:800] if page.url else ""
        lowered = body.lower()
        if any(s in lowered for s in ("captcha", "verify you are human", "cloudflare")):
            await context.close()
            return {"status": "blocked", "error": True,
                    "message": "bot protection detected on signup — screenshot and hand "
                               "to the founder; Alex never solves CAPTCHAs (docs/17)"}
        if not found:
            await context.close()
            return {"status": "blocked", "error": True,
                    "message": "no email+password signup form found (possibly SSO-only) — "
                               "reported as a blocker"}
        email_sel = "input[type='email'], [name='email'], [name='username']"
        await page.fill(email_sel, email)
        pw_fields = await page.query_selector_all("input[type='password']")
        await pw_fields[0].fill(password)
        if len(pw_fields) > 1:  # confirm-password field
            await pw_fields[1].fill(password)
        await page.click("button[type='submit'], input[type='submit']")
        await page.wait_for_load_state("networkidle", timeout=15000)
        body = (await page.inner_text("body"))[:800]
        return {"status": "success", "context": context, "page": page, "body": body}
    except Exception as exc:
        await context.close()
        return {"status": "error", "error": True, "message": f"registration failed: {exc}"}


async def open_and_login(portal_url: str, username: str, password: str) -> dict:
    """Open the portal and log in. Returns the live page on success."""
    context = await new_context()
    try:
        page = await context.new_page()
        await page.goto(portal_url, timeout=30000)
        if not await page.query_selector("input[type='password']"):
            # landing page isn't the login page — try the conventional path
            await page.goto(portal_url.rstrip("/") + "/login", timeout=15000)
        if await page.query_selector("input[type='password']"):
            await page.fill("[name='username'], [name='email']", username)
            await page.fill("[name='password']", password)
            await page.click("button[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.goto(portal_url, timeout=30000)  # login redirects home; go to the form
            await page.wait_for_load_state("networkidle", timeout=15000)
        return {"status": "success", "context": context, "page": page, "title": await page.title()}
    except Exception as exc:
        await context.close()
        return {"status": "error", "error": True, "message": f"open/login failed: {exc}"}

"""Pixel-level credential screenshot redaction (docs/22).

Covers the whole declared sensitive set, not just `type=password`: an OTP, a
credential username/email, a secret textarea, and an adapter-declared selector
are all durable evidence that a dialog or failed verification can capture.
"""

from io import BytesIO

import pytest
from PIL import Image
from playwright.async_api import async_playwright

from services.browser_service import _sensitive_screenshot_style

pytestmark = pytest.mark.asyncio

_FIELD_CSS = (
    "<style>body{margin:0;background:white}"
    "#probe{position:absolute;left:20px;top:30px;width:240px;height:60px;"
    "background:#00ff00;color:#000;font:32px sans-serif;border:0;padding:0}"
    "</style>"
)

# (case id, markup, the secret that must not survive)
_CASES = [
    ("password", '<input id="probe" type="password" value="demo-pass-2026">',
     "demo-pass-2026"),
    ("otp-name", '<input id="probe" name="otp" value="884213">', "884213"),
    ("one-time-code", '<input id="probe" autocomplete="one-time-code" '
     'value="551907">', "551907"),
    ("credential-email", '<input id="probe" type="email" '
     'value="alex@ruhu.ai">', "alex@ruhu.ai"),
    ("token-textarea", '<textarea id="probe" name="secret_token">'
     'sk-live-abc123</textarea>', "sk-live-abc123"),
    ("otp-textarea", '<textarea id="probe" name="otp">884213</textarea>',
     "884213"),
    ("username-textarea", '<textarea id="probe" name="username">'
     'alex@ruhu.ai</textarea>', "alex@ruhu.ai"),
    ("password-contenteditable", '<div id="probe" contenteditable="true" '
     'aria-label="Portal password">demo-pass-2026</div>', "demo-pass-2026"),
    ("adapter-declared", '<input id="probe" class="tax-id" value="123-45-6789">',
     "123-45-6789"),
]


async def _redacted_ratio(page, style: str) -> tuple[float, bytes]:
    box = await page.locator("#probe").bounding_box()
    shot = await page.screenshot(type="png", clip=box, style=style)
    image = Image.open(BytesIO(shot)).convert("RGB")
    inner = image.crop((8, 8, image.width - 8, image.height - 8))
    pixels = list(inner.getdata())
    gray = [p for p in pixels if all(100 <= channel <= 140 for channel in p)]
    return len(gray) / len(pixels), shot


@pytest.mark.parametrize("case_id,markup,secret", _CASES,
                         ids=[c[0] for c in _CASES])
async def test_sensitive_field_pixels_are_uniformly_redacted(
        case_id, markup, secret, monkeypatch):
    # The adapter-declared case proves PORTAL_SENSITIVE_SELECTORS is honoured.
    monkeypatch.setenv("PORTAL_SENSITIVE_SELECTORS", "input.tax-id")
    style = _sensitive_screenshot_style()
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    try:
        page = await browser.new_page(viewport={"width": 320, "height": 180})
        await page.set_content(_FIELD_CSS + markup)
        ratio, shot = await _redacted_ratio(page, style)
        assert ratio > 0.98, f"{case_id} rendered its value in the clear"
        assert secret.encode() not in shot
    finally:
        await browser.close()
        await playwright.stop()


async def test_an_ordinary_field_is_not_redacted():
    """Guards against a selector so broad it masks the whole form — the
    screenshot must still be useful founder evidence."""
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    try:
        page = await browser.new_page(viewport={"width": 320, "height": 180})
        await page.set_content(
            _FIELD_CSS + '<input id="probe" name="company_name" value="Ruhu">')
        ratio, _shot = await _redacted_ratio(page, _sensitive_screenshot_style())
        assert ratio < 0.5
    finally:
        await browser.close()
        await playwright.stop()

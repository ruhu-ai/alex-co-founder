"""The UI's visual encodings are a spec, so pin them (docs/16 §7.2, §7.3).

`fitMeter` and `deadlineChip` translate a number into a colour band. Those
thresholds are documented in the design system and read by a founder at a
glance, so a silent change to one is a change to what the interface *means* —
exactly the kind of edit that slips through review.

The functions ship inside app/static/index.html with no build step, so the test
lifts them out of the page and runs them on the real thing. Where Node is
available it evaluates them; otherwise it still asserts the thresholds appear in
the shipped source, so the test is never silently vacuous.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parents[2] / "app" / "static" / "index.html"
NODE = shutil.which("node")


def _extract(name: str) -> str:
    """Pull one top-level `function name(...) { ... }` out of the page."""
    src = INDEX.read_text(encoding="utf-8")
    start = src.index(f"function {name}(")
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces extracting {name}()")


def _run_js(body: str, calls: str):
    """Evaluate extracted page functions in Node and return the parsed result."""
    script = textwrap.dedent(f"""
        const esc = s => String(s ?? "");
        const ico = () => "";
        {body}
        process.stdout.write(JSON.stringify({calls}));
    """)
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# --------------------------------------------------------------------------
# thresholds are present in what ships (runs everywhere, including no-Node CI)
# --------------------------------------------------------------------------

def test_fit_thresholds_match_the_spec():
    band = re.search(r"const band = score >= (\d+) \? \"high\" : score >= (\d+) \? \"mid\"",
                     _extract("fitMeter"))
    assert band, "fitMeter no longer bands on two numeric thresholds"
    assert band.group(1) == "70", "docs/16 §7.3 says >=70 is `high`"
    assert band.group(2) == "50", "docs/16 §7.3 says 50-69 is `mid`"


def test_deadline_thresholds_match_the_spec():
    tone = re.search(r"days <= (\d+) \? \"danger\" : days <= (\d+) \? \"attn\"",
                     _extract("deadlineChip"))
    assert tone, "deadlineChip no longer bands on two numeric thresholds"
    assert tone.group(1) == "3", "docs/16 §7.2 says <=3d is `danger`"
    assert tone.group(2) == "10", "docs/16 §7.2 says <=10d is `attn`"


def test_fit_meter_always_reports_its_number():
    """Colour is never the only channel (docs/16 §9)."""
    assert 'class="meter-val"' in _extract("fitMeter")
    assert 'aria-valuenow="${score}"' in _extract("fitMeter")


# --------------------------------------------------------------------------
# behaviour at the boundaries, evaluated against the shipped source
# --------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node not available")
@pytest.mark.parametrize("score,band", [
    (0, "low"), (49, "low"),
    (50, "mid"), (69, "mid"),
    (70, "high"), (100, "high"),
])
def test_fit_band_boundaries(score: int, band: str):
    html = _run_js(_extract("fitMeter"), f"fitMeter({score})")
    assert f'data-band="{band}"' in html, f"fit {score} should band as {band}"
    assert f"FIT {score}" in html, "the number must render beside the bar"


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_fit_meter_absent_score_renders_nothing():
    assert _run_js(_extract("fitMeter"), "fitMeter(null)") == ""


@pytest.mark.skipif(NODE is None, reason="node not available")
@pytest.mark.parametrize("score,width", [(0, "2%"), (1, "2%"), (55, "55%"), (140, "100%")])
def test_fit_meter_bar_width_is_clamped(score: int, width: str):
    """A zero-width fill is invisible and an over-100 fill overflows its track."""
    assert f"width:{width}" in _run_js(_extract("fitMeter"), f"fitMeter({score})")


@pytest.mark.skipif(NODE is None, reason="node not available")
@pytest.mark.parametrize("days,tone", [
    (-1, "danger"), (0, "danger"), (3, "danger"),
    (4, "attn"), (10, "attn"),
    (11, "neutral"), (400, "neutral"),
])
def test_deadline_tone_boundaries(days: int, tone: str):
    js = _extract("deadlineChip")
    # deadlineChip uses Math.ceil on the day difference, so aim an hour SHORT of
    # the N-day mark: an hour past it would ceil up to N+1 and test the wrong band.
    call = ("deadlineChip({deadline: new Date(Date.now() + %d*86400000 - 3600000)"
            ".toISOString()})" % days)
    html = _run_js(js, call)
    assert f'data-tone="{tone}"' in html, f"{days}d out should be {tone}"


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_overdue_says_overdue():
    js = _extract("deadlineChip")
    html = _run_js(js, "deadlineChip({deadline: '2000-01-01T00:00:00Z'})")
    assert "overdue" in html and 'data-tone="danger"' in html


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_no_deadline_renders_nothing():
    assert _run_js(_extract("deadlineChip"), "deadlineChip({})") == ""


# --------------------------------------------------------------------------
# connector marks (docs/16 §5.4)
# --------------------------------------------------------------------------
#
# Connectors are backend data, so the catalog grows without anyone touching the
# frontend. The failure mode is silent: an unmapped connector falls back to the
# Unicode glyph in the catalog, which renders in whatever font the OS picks, in
# a colour the theme does not control. These tests make that a build failure.

CONNECTORS = Path(__file__).resolve().parents[2] / "services" / "connectors.py"


def _connector_names() -> list[str]:
    return sorted(set(re.findall(r'"name": "([a-z_]+)"', CONNECTORS.read_text(encoding="utf-8"))))


def _mark_map() -> dict[str, str]:
    src = INDEX.read_text(encoding="utf-8")
    start = src.index("const CONN_ICON = {")
    return dict(re.findall(r"(\w+):\s*\"([\w-]+)\"", src[start:src.index("};", start)]))


def _sprite_symbols() -> set[str]:
    return set(re.findall(r'<symbol id="i-([\w-]+)"', INDEX.read_text(encoding="utf-8")))


def test_every_connector_has_a_mark():
    unmapped = [n for n in _connector_names() if n not in _mark_map()]
    assert not unmapped, (
        f"connectors with no sprite mark: {unmapped} — they will fall back to the "
        "catalog's Unicode glyph. Add them to CONN_ICON in index.html."
    )


def test_every_mark_exists_in_the_sprite():
    sprite, bad = _sprite_symbols(), {}
    for name, mark in _mark_map().items():
        if mark not in sprite:
            bad[name] = mark
    assert not bad, f"marks referencing missing sprite symbols: {bad}"


def test_no_two_connectors_share_a_mark():
    """Gmail and IMAP both reading as an envelope is the bug this pins: brand
    tint alone does not survive a greyscale display or a compressed video frame."""
    seen: dict[str, list[str]] = {}
    for name, mark in _mark_map().items():
        seen.setdefault(mark, []).append(name)
    clashes = {m: n for m, n in seen.items() if len(n) > 1}
    assert not clashes, f"connectors sharing a mark: {clashes}"


def test_connector_marks_do_not_reuse_action_icons():
    """A connector must not wear an icon that means "do something" elsewhere —
    Telegram once used `send`, the composer's own submit arrow."""
    actions = {"send", "download", "close", "plus", "minus", "check", "refresh"}
    reused = {n: m for n, m in _mark_map().items() if m in actions}
    assert not reused, f"connector marks colliding with action icons: {reused}"


def test_no_third_party_logos_are_vendored():
    """Submission rule: "No third-party logos/ads in any submission material."
    The vendored icon set is generic Phosphor; this fails if a brand mark
    is ever dropped in beside it."""
    icon_dir = INDEX.parent / "vendor" / "icons" / "phosphor"
    vendored = {f.stem for f in icon_dir.glob("*.svg")}
    # Guard the guard: a wrong path here would make this assertion vacuous.
    assert len(vendored) > 20, f"expected the vendored icon set at {icon_dir}, found {len(vendored)}"
    brands = {"google", "gmail", "slack", "github", "telegram", "jira", "notion",
              "google-drive", "google-logo", "slack-logo", "github-logo",
              "telegram-logo", "microsoft-outlook-logo", "notion-logo", "figma-logo"}
    assert not (vendored & brands), f"brand logos vendored: {sorted(vendored & brands)}"


# --------------------------------------------------------------------------
# brand marks survive the dark theme (docs/16 §2.5)
# --------------------------------------------------------------------------
#
# Brand hues are backend data chosen by each vendor for a white page, so several
# are unreadable on our dark ground — GitHub #1f2328 scores 1.16 against the
# dark panel. The UI flags those at render time and CSS lifts them toward the
# theme ink. These tests pin both halves: the flag threshold and the outcome.

DARK_PANEL = "#12151D"
DARK_INK = "#E9ECF4"
MIN_MARK_CONTRAST = 3.0


def _srgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def chan(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(_srgb(a)), _relative_luminance(_srgb(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _mix(base: str, toward: str, pct: float) -> str:
    """CSS `color-mix(in srgb, toward pct%, base)`, in sRGB as the stylesheet does."""
    a, b = _srgb(base), _srgb(toward)
    return "#%02X%02X%02X" % tuple(round(a[i] + (b[i] - a[i]) * pct / 100) for i in range(3))


def _brand_lift() -> float:
    """The dark theme's --brand-lift, read from the shipped stylesheet."""
    css = INDEX.read_text(encoding="utf-8")
    block = css[css.index("@tokens dark"):css.index("@tokens light")]
    m = re.search(r"--brand-lift:\s*(\d+)%", block)
    assert m, "the dark theme no longer defines --brand-lift"
    return float(m.group(1))


def _catalog_brands() -> dict[str, str]:
    src = CONNECTORS.read_text(encoding="utf-8")
    return dict(re.findall(r'"name": "([a-z_]+)".*?"brand": "(#[0-9a-fA-F]{6})"', src))


def test_light_theme_does_not_alter_brand_hues():
    """--brand-lift must be 0% in light mode: the vendor's own colour is correct
    on white, and shifting it would be a needless deviation from their brand."""
    css = INDEX.read_text(encoding="utf-8")
    for marker in ("@tokens light", "@tokens light-toggle"):
        block = css[css.index(marker):css.index("}", css.index(marker))]
        m = re.search(r"--brand-lift:\s*(\d+)%", block)
        assert m, f"{marker} block does not define --brand-lift"
        assert m.group(1) == "0", f"{marker}: light mode must not lift brand hues"


@pytest.mark.parametrize("name,brand", sorted(_catalog_brands().items()))
def test_every_brand_mark_is_legible_on_the_dark_panel(name: str, brand: str):
    raw = _contrast(brand, DARK_PANEL)
    if raw >= MIN_MARK_CONTRAST:
        return  # rendered as-is; the vendor hue already works on our ground
    lifted = _mix(brand, DARK_INK, _brand_lift())
    assert _contrast(lifted, DARK_PANEL) >= MIN_MARK_CONTRAST, (
        f"{name} ({brand}) scores {raw:.2f} raw and only "
        f"{_contrast(lifted, DARK_PANEL):.2f} after a {_brand_lift():.0f}% lift — "
        "raise --brand-lift in the dark token block."
    )


def test_the_lift_is_actually_needed_by_something():
    """Guard the guard: if no catalog brand ever trips the threshold, the test
    above is vacuous and --brand-lift could silently break."""
    tripped = [n for n, b in _catalog_brands().items()
               if _contrast(b, DARK_PANEL) < MIN_MARK_CONTRAST]
    assert tripped, "no brand trips the dark-panel threshold — is DARK_PANEL still right?"

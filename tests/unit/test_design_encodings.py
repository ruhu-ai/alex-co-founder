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

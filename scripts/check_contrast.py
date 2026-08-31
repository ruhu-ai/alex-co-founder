#!/usr/bin/env python3
"""Enforce the docs/16-design-system.md contrast contract.

Parses the semantic token blocks out of app/static/index.html so the check runs
against what actually ships, not a copy of the palette. Exits non-zero on any
pair below its floor. Wired into CI next to the evals.

    python scripts/check_contrast.py [-v]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "app" / "static" / "index.html"

# (foreground token, background token, floor, what it is)
CONTRACT: list[tuple[str, str, float, str]] = [
    ("ink-1", "surface-1", 12.0, "body text on panel"),
    ("ink-1", "surface-2", 11.0, "body text on inset"),
    ("ink-2", "surface-1", 7.0, "secondary text on panel"),
    ("ink-2", "surface-2", 6.5, "secondary text on inset"),
    ("ink-3", "surface-1", 4.5, "label on panel"),
    ("ink-3", "surface-2", 4.5, "label on inset"),
    ("ink-3", "surface-0", 4.5, "label on app ground"),
    ("accent-ink", "surface-1", 4.5, "link text on panel"),
    ("accent-ink", "surface-2", 4.5, "link text on inset"),
    ("accent-on", "accent", 4.5, "primary button label"),
    ("attn-ink", "surface-1", 4.5, "attention text on panel"),
    ("attn-ink", "surface-2", 4.5, "attention text on inset"),
    ("ok-ink", "surface-1", 4.5, "success text on panel"),
    ("ok-ink", "surface-2", 4.5, "success text on inset"),
    ("danger-ink", "surface-1", 4.5, "danger text on panel"),
    ("danger-ink", "surface-2", 4.5, "danger text on inset"),
    ("danger-on", "danger", 4.5, "destructive button label"),
    ("info-ink", "surface-1", 4.5, "state badge text on panel"),
    ("info-ink", "surface-2", 4.5, "state badge text on inset"),
    # non-text (WCAG 1.4.11 is 3:1; hairlines use a 1.5 house floor)
    ("border", "surface-1", 1.5, "card border on panel"),
    ("border", "surface-2", 1.4, "card border on inset"),
    ("field", "surface-1", 3.0, "input border on panel"),
    ("focus", "surface-1", 3.0, "focus ring on panel"),
    ("focus", "surface-0", 3.0, "focus ring on app ground"),
    ("accent", "surface-3", 3.0, "accent on hover surface"),
    ("ok", "surface-1", 3.0, "meter fill / status dot"),
    ("attn", "surface-1", 3.0, "attention stripe"),
    ("danger", "surface-1", 3.0, "severity stripe"),
    ("info", "surface-1", 3.0, "state dot"),
]


def _lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def parse_theme(css: str, marker: str) -> dict[str, str]:
    """Pull `--token: #hex;` pairs out of the rule that follows `marker`.

    The marker is an `@tokens <theme>` annotation inside the comment that
    introduces the block, so the tokens the checker reads are exactly the ones
    the page ships.
    """
    try:
        start = css.index(marker)
    except ValueError:
        raise SystemExit(f"marker {marker!r} not found in {INDEX} — did the token block move?")
    brace = css.index("{", css.index("*/", start))
    block = css[brace:css.index("}", brace)]
    return {m[1]: m[2] for m in re.finditer(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\s*;", block)}


def check(name: str, tokens: dict[str, str], verbose: bool,
          contract: list[tuple[str, str, float, str]] | None = None) -> int:
    contract = contract or CONTRACT
    failures = 0
    print(f"\n{name}  ({len(tokens)} tokens)")
    for fg, bg, floor, note in contract:
        if fg not in tokens or bg not in tokens:
            print(f"  MISSING  --{fg} / --{bg}  ({note})")
            failures += 1
            continue
        r = ratio(tokens[fg], tokens[bg])
        ok = r >= floor
        failures += not ok
        if verbose or not ok:
            print(f"  {'ok  ' if ok else 'FAIL'} {fg:>11s} on {bg:<10s} {r:6.2f} >= {floor:4.1f}   {note}")
    print(f"  {'PASS' if not failures else f'{failures} FAILURE(S)'} — {len(contract)} pairs")
    return failures


def main() -> int:
    verbose = "-v" in sys.argv
    css = INDEX.read_text(encoding="utf-8")
    dark = parse_theme(css, "@tokens dark")
    light = parse_theme(css, "@tokens light")
    total = check("dark", dark, verbose) + check("light", light, verbose)

    # The light palette is declared twice (OS preference + explicit toggle)
    # because CSS cannot share a block across a media query. Drift between the
    # two is invisible in one theme and glaring in the other, so assert it here.
    toggle = parse_theme(css, "@tokens light-toggle")
    drift = {k for k in set(light) | set(toggle) if light.get(k) != toggle.get(k)}
    if drift:
        print("\nlight palette drift between the media block and the toggle block:")
        for k in sorted(drift):
            print(f"  --{k}: media={light.get(k, 'absent')}  toggle={toggle.get(k, 'absent')}")
        total += len(drift)
    else:
        print(f"\nlight palette: media and toggle blocks agree ({len(light)} tokens)")

    if total:
        print("\ndocs/16-design-system.md §2 violated — fix the tokens in "
              "app/static/index.html and re-run.")
        return 1
    print("\ncontrast contract satisfied (docs/16-design-system.md §2.4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

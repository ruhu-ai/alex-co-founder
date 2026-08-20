"""Vision reconnaissance (docs/09 Tier 1) — the screenshot→Gemini→action loop.

Guardrails (binding): action allowlist excludes submission controls BY
CONSTRUCTION; 20-step budget AND 90-second time-box; every step screenshots to
artifacts + writes an audit row. The model-call function is injectable so tests
run without credentials.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from services import firestore, storage

ALLOWED_ACTIONS = {"click", "type", "select", "scroll", "navigate_back", "done"}
MAX_STEPS = 20
TIME_BOX_SECONDS = 90

# (screenshot_bytes, goal, history) -> {"action": ..., "selector"?, "text"?, "note"?}
ModelFn = Callable[[bytes, str, list[dict]], Awaitable[dict[str, Any]]]
_model_fn: ModelFn | None = None


def set_model_fn(fn: ModelFn) -> None:
    global _model_fn
    _model_fn = fn


async def vision_step(page, goal: str, history: list[dict], application_id: str) -> dict:
    """One bounded vision action. Submit controls are unreachable here."""
    if _model_fn is None:
        return {"status": "error", "error": True,
                "message": "vision model not configured (needs ADC)"}
    shot = await page.screenshot()
    proposal = await _model_fn(shot, goal, history)
    action = proposal.get("action", "")
    if action not in ALLOWED_ACTIONS:
        await firestore.audit("agent:form_filler", "vision_step",
                              f"applications/{application_id}", "refused",
                              f"action not permitted: {action}")
        return {"status": "error", "error": True,
                "message": f"action not permitted: {action!r} (allowlist: {sorted(ALLOWED_ACTIONS)})"}
    return proposal


async def run_recon(page, goal: str, application_id: str) -> dict:
    """Drive the vision loop across the form; emit a form_map artifact.

    Never submits. Returns partial results on budget/time exhaustion —
    partial success is first-class (docs/09).
    """
    started = time.monotonic()
    history: list[dict] = []
    steps = 0
    while steps < MAX_STEPS and time.monotonic() - started < TIME_BOX_SECONDS:
        proposal = await vision_step(page, goal, history, application_id)
        if proposal.get("status") == "error":
            return proposal
        steps += 1
        action = proposal["action"]
        ts = int(time.time())
        storage.save_bytes(f"recon_{application_id}_{ts}.png", await page.screenshot())
        await firestore.audit("agent:form_filler", "vision_step",
                              f"applications/{application_id}", "success",
                              f"{action}: {str(proposal.get('note', ''))[:200]}")
        history.append({"action": action, "note": proposal.get("note", "")})
        if action == "done":
            break
        try:
            if action == "click":
                await page.click(proposal["selector"], timeout=5000)
            elif action == "type":
                await page.fill(proposal["selector"], proposal.get("text", ""))
            elif action == "select":
                await page.select_option(proposal["selector"], label=proposal.get("text", ""))
            elif action == "scroll":
                await page.mouse.wheel(0, 600)
            elif action == "navigate_back":
                await page.go_back()
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception as exc:
            history.append({"action": action, "error": str(exc)[:200]})

    form_map = {
        "goal": goal,
        "steps_taken": steps,
        "time_seconds": round(time.monotonic() - started, 1),
        "exhausted": steps >= MAX_STEPS or (time.monotonic() - started) >= TIME_BOX_SECONDS,
        "observations": history,
    }
    import json

    artifact = f"form_map_{application_id}.json"
    storage.save_text(artifact, json.dumps(form_map, indent=2))
    return {
        "status": "success",
        "artifact": artifact,
        "steps": steps,
        "exhausted": form_map["exhausted"],
        "summary": f"recon: {steps} steps in {form_map['time_seconds']}s"
                   + (" (budget exhausted — partial map)" if form_map["exhausted"] else ""),
    }

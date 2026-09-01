"""Shared model + environment bootstrap (docs/04 §model selection).

Importing the application is deliberately credential-free.  Test collection,
static tooling, and local UI-only work must not require Google credentials;
the provider boundary reports a clear error only when code actually attempts a
Vertex operation.
"""

import os

import google.auth
from dotenv import load_dotenv
from google.adk.models import Gemini

from services.retry_policy import gemini_retry_options

# .env at the repo root is the local-dev configuration source (docs/13);
# load_dotenv never overrides variables already present in the environment.
load_dotenv()

def _discover_default_project_id() -> str:
    """Return ADC's project when available without making imports depend on it."""
    try:
        _, project_id = google.auth.default()
    except Exception:  # noqa: BLE001 — missing ADC is valid until provider use
        return ""
    return str(project_id or "").strip()


_project_id = (os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
               or _discover_default_project_id())
if _project_id:
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", _project_id)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
# gemini-3.6-flash and gemini-3.1-pro-preview are published on the `global`
# Vertex endpoint, not us-central1. Pin it so the ADK Gemini clients resolve
# there even in local dev where .env is not sourced (matches gemini_backends).
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

MODEL_ID = os.environ.get("ADK_MODEL", "gemini-3.6-flash")

# Model tiers (docs/19 §P1.7). The reasoning tier ran on `gemini-3.1-pro-preview`
# until 2026-08-20 and no longer does, for two measured reasons: Pro fabricated a
# whole company on the drafting task it was chosen for (the grounding guard in
# callbacks.py now catches that, and is model-independent), and a Pro→Pro
# orchestrator/drafter chain exceeded a 600 s redraft budget. A preview endpoint
# is also a poor bet for a submission that must stay live for judge testing into
# October. The seam stays: set REASONING_MODEL to revert in one variable.
# dialogue (3.6-flash) = orchestrator + drafter + default; lite (3.5-flash-lite)
# = bulk extraction; live = voice; embedding-001 = retrieval.
# NOTE: these are published ONLY on the `global` Vertex endpoint
# (GOOGLE_CLOUD_LOCATION=global), not us-central1. There is no 3.6-flash-lite,
# so the lite tier stays on 3.5-flash-lite.
REASONING_MODEL_ID = os.environ.get("REASONING_MODEL", "gemini-3.6-flash")
LITE_MODEL_ID = os.environ.get("LITE_MODEL", "gemini-3.5-flash-lite")

# Founder-facing persona (docs/adr/001): the orchestrator's voice and the name
# shown in chat. Per-venture config later; env-driven for v1.
PERSONA_NAME = os.environ.get("PERSONA_NAME", "Alex")

# Gemini Live model for the real-time voice surface (app/live.py). Verified
# live on Vertex 2026-08-19: gemini-live-2.5-flash (the dated native-audio
# preview names are not enabled on this project).
LIVE_MODEL_ID = os.environ.get("LIVE_MODEL", "gemini-live-2.5-flash")

MODEL = Gemini(
    model=MODEL_ID,
    retry_options=gemini_retry_options(),
)

REASONING_MODEL = Gemini(
    model=REASONING_MODEL_ID,
    retry_options=gemini_retry_options(),
)

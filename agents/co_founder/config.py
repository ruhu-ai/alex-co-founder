"""Shared model + environment bootstrap (docs/04 §model selection).

Fails loudly at import if Vertex credentials are missing — better a clear
error now than a mystery 401 during a demo.
"""

import os

import google.auth
from dotenv import load_dotenv
from google.adk.models import Gemini
from google.genai import types

# .env at the repo root is the local-dev configuration source (docs/13);
# load_dotenv never overrides variables already present in the environment.
load_dotenv()

try:
    _, _project_id = google.auth.default()
except Exception as exc:  # noqa: BLE001 — fail loudly with the fix
    raise RuntimeError(
        "No Application Default Credentials found. Run: "
        "`gcloud auth application-default login` (or ./scripts/setup.sh)."
    ) from exc

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", _project_id or "")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
# gemini-3.6-flash and gemini-3.1-pro-preview are published on the `global`
# Vertex endpoint, not us-central1. Pin it so the ADK Gemini clients resolve
# there even in local dev where .env is not sourced (matches gemini_backends).
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

MODEL_ID = os.environ.get("ADK_MODEL", "gemini-3.6-flash")

# Model tiers (docs/19 §P1.7 — re-verified live on this project 2026-08-24):
# reasoning (3.1-pro-preview) = orchestrator + drafter, final synthesis;
# dialogue (3.6-flash) = default; lite (3.5-flash-lite) = bulk extraction;
# live = voice; embedding-001 = retrieval. Five models, each with a rationale.
# NOTE: these are published ONLY on the `global` Vertex endpoint
# (GOOGLE_CLOUD_LOCATION=global), not us-central1. Gemini 3.x Pro ships as
# `gemini-3.1-pro-preview` (there is no non-preview 3.x-pro yet); there is no
# 3.6-flash-lite, so the lite tier stays on 3.5-flash-lite. gemini-3.7-flash is
# also available if we later want the newest Flash.
REASONING_MODEL_ID = os.environ.get("REASONING_MODEL", "gemini-3.1-pro-preview")
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
    retry_options=types.HttpRetryOptions(attempts=3),  # transient 5xx/429 resilience
)

REASONING_MODEL = Gemini(
    model=REASONING_MODEL_ID,
    retry_options=types.HttpRetryOptions(attempts=3),
)

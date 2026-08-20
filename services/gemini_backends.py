"""Production Gemini backends for the injectable service functions
(docs/05, 06, 08, 09). One module, one client, tolerant JSON parsing.

Wired at server startup (app/main.py). Construction fails fast without ADC —
the caller catches that and leaves the offline error paths in place.
"""

from __future__ import annotations

import json
import os
import re

from google import genai
from google.genai import types

MODEL_ID = os.environ.get("ADK_MODEL", "gemini-3.5-flash")

_client = None


def get_client() -> genai.Client:
    """Lazy singleton; raises RuntimeError with the fix when creds are missing."""
    global _client
    if _client is None:
        try:
            _client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
            )
        except Exception as exc:
            raise RuntimeError(
                "Gemini client needs ADC: run `gcloud auth application-default login`"
            ) from exc
    return _client


def _parse_json_list(text: str) -> list[dict]:
    """Tolerant JSON-list extraction from a model response (fences, prose)."""
    text = text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return []


# ---------------------------------------------------------------------------
# discovery (docs/08)
# ---------------------------------------------------------------------------

def search_fn(query: str) -> list[dict]:
    """Google Search grounding via the Gemini API (Lane 1)."""
    resp = get_client().models.generate_content(
        model=MODEL_ID,
        contents=(
            "Find current grant / accelerator / incubator programs matching this "
            f"query: {query}\nReturn ONLY a JSON list of up to 10 items, each "
            '{"title": str, "url": str, "snippet": str}. Programs must be real '
            "and currently open or recurring."
        ),
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        ),
    )
    return _parse_json_list(resp.text or "")


def extract_fn(source_text: str, entity_schema: dict) -> list[dict]:
    """Structured record extraction from fetched source text (with citations)."""
    schema_desc = ", ".join(entity_schema.keys())
    resp = get_client().models.generate_content(
        model=MODEL_ID,
        contents=(
            "Extract program records from the source text below. Return ONLY a "
            f"JSON list; each record has exactly these fields: {schema_desc}, "
            "plus raw_excerpt (<=2000 chars, verbatim source text justifying the "
            "record). Missing fields are null. Never invent values. Deadlines as "
            f"ISO dates or null.\n\nSOURCE:\n{source_text[:60000]}"
        ),
    )
    return _parse_json_list(resp.text or "")


# ---------------------------------------------------------------------------
# profile bootstrap (docs/06)
# ---------------------------------------------------------------------------

def doc_extract_fn(artifact_name: str, founder_id: str) -> list[dict]:
    """Document understanding: company doc -> proposed profile mutations."""
    from services import storage

    path = storage.artifact_path(artifact_name)
    with open(path, "rb") as fh:
        data = fh.read()
    mime = "application/pdf" if path.lower().endswith(".pdf") else "text/plain"
    part = (types.Part.from_bytes(data=data, mime_type=mime) if mime == "application/pdf"
            else types.Part.from_text(text=data.decode("utf-8", errors="replace")[:80000]))
    resp = get_client().models.generate_content(
        model=MODEL_ID,
        contents=[
            types.Content(role="user", parts=[
                part,
                types.Part.from_text(text=(
                    "This is a founder's company document. Propose Founder Profile "
                    "updates as a JSON list. Each item: {\"kind\": one of fact_update | "
                    "voice_rule | canonical_answer_update | decision_pattern, "
                    "\"payload\": object, \"evidence_quote\": verbatim snippet, "
                    "\"confidence\": \"high\" if the item is stated verbatim in the "
                    "document, \"low\" if it is an inference}. Facts: "
                    "company, product, traction numbers, team, market. Voice rules: "
                    "only clear, testable writing preferences evidenced by the text. "
                    "Never invent numbers. 5-25 items."
                )),
            ]),
        ],
    )
    return _parse_json_list(resp.text or "")


# ---------------------------------------------------------------------------
# vision recon (docs/09 Tier 1)
# ---------------------------------------------------------------------------

async def recon_model_fn(screenshot: bytes, goal: str, history: list[dict]) -> dict:
    """Screenshot -> one structured action. Submit controls are never proposed:
    the allowlist in recon_service enforces it regardless."""
    resp = get_client().models.generate_content(
        model=MODEL_ID,
        contents=[
            types.Content(role="user", parts=[
                types.Part.from_bytes(data=screenshot, mime_type="image/png"),
                types.Part.from_text(text=(
                    f"Goal: {goal}\nHistory: {json.dumps(history[-5:])}\n"
                    "You are mapping an application form. Propose ONE next action as "
                    'JSON: {"action": "click|type|select|scroll|navigate_back|done", '
                    '"selector": css selector if click/type/select, "text": value if '
                    'type/select, "note": what you observe (fields, requirements, '
                    'step x of y)}. NEVER propose submitting the form. Say "done" when '
                    "every field and requirement is mapped."
                )),
            ]),
        ],
    )
    items = _parse_json_list(resp.text or "")
    if items and isinstance(items[0], dict):
        return items[0]
    match = re.search(r"\{.*\}", resp.text or "", flags=re.DOTALL)
    return json.loads(match.group(0)) if match else {"action": "done", "note": "unparseable model reply"}


# ---------------------------------------------------------------------------
# voice notes (docs/05, Day-11)
# ---------------------------------------------------------------------------

async def transcribe_fn(audio_path: str, context: str) -> dict:
    """Audio -> verbatim transcript + extracted intent."""
    with open(audio_path, "rb") as fh:
        data = fh.read()
    resp = get_client().models.generate_content(
        model=MODEL_ID,
        contents=[
            types.Content(role="user", parts=[
                types.Part.from_bytes(data=data, mime_type="audio/webm"),
                types.Part.from_text(text=(
                    f"Context: {context or 'founder voice note'}. Transcribe verbatim, "
                    'then classify: {"transcript": str, "extracted": {"kind": '
                    '"answer"|"feedback"|"other", "text": str, "question_key": str|null, '
                    '"section_id": str|null}}. Return ONLY that JSON.'
                )),
            ]),
        ],
    )
    items = _parse_json_list(resp.text or "")
    if items:
        return items[0] if isinstance(items[0], dict) else {"transcript": str(items[0])}
    match = re.search(r"\{.*\}", resp.text or "", flags=re.DOTALL)
    return json.loads(match.group(0)) if match else {"transcript": resp.text or ""}


# ---------------------------------------------------------------------------

def wire_all() -> None:
    """Attach every production backend. The genai client authenticates lazily:
    without ADC the first API call fails and the service layer converts it to
    an error dict — so wiring here is unconditional and the app boots either
    way (verified: server boots and serves UI with no creds present)."""
    get_client()  # constructs the client; auth itself happens on first call

    from services import (discovery_service, profile_service, recon_service,
                          voice_service)

    discovery_service.set_search_fn(search_fn)
    discovery_service.set_extract_fn(extract_fn)
    profile_service.set_extract_fn(doc_extract_fn)
    recon_service.set_model_fn(recon_model_fn)
    voice_service.set_transcribe_fn(transcribe_fn)

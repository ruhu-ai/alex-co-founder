"""Callbacks: session-state init, and the document grounding guard.

Guards live in code, not prompts (docs/README principle 7). `produce_document`
takes a model-authored `spec` and renders it; asking for grounding in the tool
docstring made the model comply *most* of the time — one observed run invented a
company called "Meridian", a co-founder matching platform with "2,000+ active
users" and fabricated press coverage, for a founder whose approved answers were
all about a voice-AI clinic platform. A grant reviewer can check those numbers.
"""

import json
import re
from typing import Any, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import BaseTool, ToolContext

from . import state_schema as ss

_DEFAULTS = {
    ss.K_CURRENT_STEP: ss.ApplicationStep.IDLE,
    ss.K_ACTIVE_APPLICATION_ID: "",
    ss.K_ACTIVE_OPPORTUNITY_ID: "",
    ss.K_ACTIVE_PROGRAM_REQUIREMENTS: [],
    ss.K_CHECKLIST_STATUS: {},
    ss.K_PENDING_SIGNALS: [],
    ss.K_CURRENT_SECTION: "",
    ss.K_BROWSER_STATUS: {
        "active": False, "kind": None, "run_id": None, "url": None,
        "goal": None, "last_action": None,
    },
    ss.K_USER_PREFS: {},
}


async def initialize_session_state(callback_context: CallbackContext) -> None:
    """Ensures all state machine keys are initialized to prevent errors."""
    state = callback_context.state
    for key, default in _DEFAULTS.items():
        if key not in state:
            state[key] = default
    # The agent's clock: refreshed every turn so "what's today's date" and
    # deadline math are always grounded.
    from datetime import date

    state[ss.K_TODAY] = date.today().isoformat()
    # user:profile_id is resolved from the request's founder identity (Day 3:
    # profile store). System sessions (task runs, webhook wakes) act on behalf
    # of the founder — they must resolve to the founder's profile, never to a
    # "system" profile that would read empty.
    if ss.K_USER_PROFILE_ID not in state:
        import os

        uid = getattr(callback_context, "user_id", "") or ""
        if uid and uid != "system":
            state[ss.K_USER_PROFILE_ID] = uid
        else:
            state[ss.K_USER_PROFILE_ID] = os.environ.get("FOUNDER_ID", "founder")
    if ss.K_APP_WORKFLOW_ID not in state:
        from .workflow import get_workflow  # local import: avoid cycles at import time

        state[ss.K_APP_WORKFLOW_ID] = get_workflow().workflow_id

    # BrowserRun is durable truth; rewrite the advisory projection before the
    # instruction template renders on every invocation (docs/18).
    try:
        from services import browser_service

        session = getattr(callback_context, "session", None)
        session_key = {
            "app_name": getattr(session, "app_name", "") or "co_founder",
            "user_id": getattr(callback_context, "user_id", "")
            or getattr(session, "user_id", ""),
            "session_id": getattr(session, "id", "")
            or getattr(session, "session_id", ""),
        }
        if session_key["user_id"] and session_key["session_id"]:
            state[ss.K_BROWSER_STATUS] = await browser_service.reconcile_session(session_key)
    except Exception:
        # A Firestore outage must not prevent the founder from using chat; the
        # initialized inactive projection is safer than rendering stale state.
        state[ss.K_BROWSER_STATUS] = _DEFAULTS[ss.K_BROWSER_STATUS].copy()


# ---------------------------------------------------------------------------
# Document grounding guard (docs/README principle 7, docs/15)
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"\d[\d,.]*")
# Ordinals, small counts and years-in-prose are not the fabrication risk; a
# reviewer checks traction figures, award sizes and dates. Two significant
# digits is the floor where a made-up number starts doing damage.
_MIN_SIGNIFICANT_DIGITS = 2


def _normalise(text: str) -> str:
    """Strip the formatting that makes the same figure look different."""
    return re.sub(r"[,\s$€£%+~]", "", text).lower()


def _spec_strings(node: Any) -> list[str]:
    """Every string anywhere in the model-authored spec."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _spec_strings(v)]
    if isinstance(node, (list, tuple)):
        return [s for v in node for s in _spec_strings(v)]
    return []


def _significant_numbers(text: str) -> set[str]:
    out = set()
    for raw in _NUMBER.findall(text):
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= _MIN_SIGNIFICANT_DIGITS:
            out.add(raw.rstrip(".,"))
    return out


# The applicant-name question, however it is worded: the company word before
# "name" ("Company name"), the company word after "name" ("Name of the
# organisation"), or a bare column header ("Applicant").
_ORG_WORD = r"(company|organisation|organization|applicant|business|venture|startup)"
_COMPANY_HEADING = re.compile(
    rf"\b{_ORG_WORD}\b.*\bname\b|\bname\b.*\b{_ORG_WORD}\b|^\s*{_ORG_WORD}(\s*name)?\s*$",
    re.I)


def _wrong_applicant(spec: Any, company: str) -> str:
    """The value given for "company name", when it is not the founder's company.

    Returns "" when the question is absent or answered correctly, so a budget or
    a deck — which have no applicant field — is never blocked by this.
    """
    token = re.split(r"[,\s]+", company.strip())[0].lower()
    if not token:
        return ""
    sections = spec.get("sections", []) if isinstance(spec, dict) else []
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading", ""))
        if not _COMPANY_HEADING.search(heading):
            continue
        answer = " ".join(_spec_strings(section.get("paragraphs", []))
                          + _spec_strings(section.get("bullets", []))).strip()
        if answer and token not in answer.lower():
            return answer[:60]
    return ""


async def _grounding_source(founder_id: str, app_id: str) -> tuple[str, str, str]:
    """Everything the document is allowed to be built from.

    Returns (source_text, founder_company, programme_name).
    """
    from services import firestore, profile_service

    profile = await profile_service.get_profile(founder_id) or {}
    facts = profile.get("facts", {}) or {}
    parts = [json.dumps(facts, default=str)]

    company = str(facts.get("company_name", "")).strip()
    programme = ""

    if app_id:
        app = await firestore.get_application(app_id) or {}
        for section in app.get("draft_sections", []):
            # Only APPROVED text is a source. A draft the founder has not seen
            # is not evidence, and neither is a rejected one.
            if section.get("status") == "APPROVED":
                parts.append(str(section.get("content", "")))
        for question in app.get("form_questions", []) or []:
            parts.append(str(question.get("label", "")))
        opp_id = app.get("opportunity_id", "")
        if opp_id:
            opp = await firestore.get_opportunity(opp_id) or {}
            programme = str(opp.get("name", ""))
            # Only the quotable fields. Dumping the whole record would let ids,
            # hashes and timestamps whitelist any digit the model invented —
            # a guard whose source is noise is not a guard.
            for key in ("name", "award", "deadline", "description",
                        "eligibility", "required_materials", "application_url"):
                if key in opp:
                    parts.append(json.dumps(opp[key], default=str))

    return "\n".join(parts), company, programme


async def enforce_document_grounding(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> Optional[dict[str, Any]]:
    """Refuse to render a document spec that is not traceable to evidence.

    Returns None to let the call through, or an error dict which ADK hands back
    to the model as the tool result — errors are data, never raised (principle
    2), so the model can correct the spec and try again.
    """
    if tool.name != "produce_document":
        return None

    spec = args.get("spec")
    if not isinstance(spec, (dict, list)):
        return None  # malformed spec is the tool's own validation problem

    founder = tool_context.state.get(ss.K_USER_PROFILE_ID, "founder")
    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    source, company, programme = await _grounding_source(founder, app_id)

    body = "\n".join(_spec_strings(spec))
    title = str(args.get("title", ""))
    # Compare number TOKENS, not substrings: a grounded "12,000" must not
    # whitelist an invented "2,000" just because "2000" sits inside "12000".
    source_numbers = {_normalise(n)
                      for n in _significant_numbers(source + "\n" + title)}

    problems: list[str] = []

    invented = sorted(
        n for n in _significant_numbers(body) if _normalise(n) not in source_numbers
    )
    if invented:
        problems.append(
            "these figures appear in no approved answer, profile fact or "
            f"programme record: {', '.join(invented[:8])}"
        )

    # Whoever the document says the applicant is, it must be the founder's
    # company. Checked against the company-name answer directly rather than via
    # the opportunity record, which may not resolve — the observed failure was
    # the model reading the document TITLE as the company, and the title is
    # always present.
    if company:
        wrong = _wrong_applicant(spec, company)
        if wrong:
            problems.append(
                f"the application names {wrong!r} as the company; the founder's "
                f"company is {company!r}"
            )
    # Belt and braces when the programme record is available.
    if programme and company:
        first = programme.split()[0]
        low = body.lower()
        if first.lower() in low and company.split(",")[0].lower() not in low:
            problems.append(
                f"the programme name {first!r} reads as the applicant rather "
                f"than {company!r}"
            )

    if not problems:
        return None

    return {
        "status": "error",
        "error": True,
        "message": (
            "Document not produced — it is not grounded in the founder's "
            "evidence: " + "; ".join(problems) + ". Call get_relevant_answers "
            "and get_form_questions, rebuild the spec from approved sections "
            "and profile facts only, and do not introduce figures of your own."
        ),
        "ungrounded": invented,
    }

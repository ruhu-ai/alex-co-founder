"""Run-scoped H4S conversation authority with no generic-chat fallback."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any

from services.actor_identity import ActorPrincipal, authorize
from services.durable_store import DurableStore
from services.hiring_contracts import stable_id, utc_now
from services.hiring_sandbox import require_sandbox

_TTL_SECONDS = 600
_FORBIDDEN = re.compile(
    r"\b(best|better than|strongest|rank(?:ed|ing)?|score|hire|reject|"
    r"recommend(?:ation|ed)?|culture fit|personality)\b", re.I)
_JUDGMENT_REQUEST = re.compile(
    r"\b(best|better|rank|score|should (?:we|i) (?:hire|reject|advance)|"
    r"recommend|culture fit|personality)\b", re.I)


def _error(code: str, message: str, status: int = 409) -> dict[str, Any]:
    del status
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


def _secret() -> bytes:
    value = os.environ.get("APP_SESSION_SECRET", "")
    if not value:
        raise RuntimeError("H4S conversation secret is not configured")
    return value.encode()


def _encode(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(_secret(), raw, hashlib.sha256).digest()
    return (base64.urlsafe_b64encode(raw).decode().rstrip("=") + "."
            + base64.urlsafe_b64encode(signature).decode().rstrip("="))


def _decode(token: str) -> dict[str, Any] | None:
    try:
        encoded, supplied = token.split(".", 1)
        padded = encoded + "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode(padded)
        expected = hmac.new(_secret(), raw, hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(supplied + "=" * (-len(supplied) % 4))
        payload = json.loads(raw)
        if not hmac.compare_digest(expected, actual) or int(payload["exp"]) < int(time.time()):
            return None
        return payload
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, RuntimeError):
        return None


def validate_answer_text(text: str) -> bool:
    """Reject forbidden hiring judgment language in every rendered answer."""
    return bool(text and len(text) <= 4000 and not _FORBIDDEN.search(text))


class HiringRunAnswerService:
    """Creates one actor/run/candidate-scoped conversation and safe projections."""

    def __init__(self, store: DurableStore):
        self.store = store

    async def begin(self, *, principal: ActorPrincipal, sandbox_run_id: str,
                    candidate_application_id: str = "") -> dict[str, Any]:
        sandbox = await self.store.get("hiring_sandbox_runs", sandbox_run_id)
        active = require_sandbox(sandbox or {})
        if active.get("error"):
            return active
        if sandbox["workspace_id"] != principal.workspace_id:
            return _error("sandbox_not_found", "Sandbox does not exist.", 404)
        gate = authorize(principal, "read_role")
        if gate.get("error"):
            return gate
        if candidate_application_id:
            candidate = await self.store.get("candidate_applications", candidate_application_id)
            if (not candidate or candidate.get("workspace_id") != principal.workspace_id
                    or candidate.get("role_id") != sandbox["role_id"]):
                return _error("candidate_scope_invalid", "Candidate is outside this hiring run.", 404)
            candidate_gate = authorize(
                principal, "read_candidate")
            if candidate_gate.get("error"):
                return candidate_gate
        conversation_id = stable_id(
            "hct", sandbox_run_id, principal.actor_id,
            candidate_application_id or "role")
        payload = {"v": 1, "conversation_id": conversation_id,
                   "sandbox_run_id": sandbox_run_id,
                   "workspace_id": principal.workspace_id, "actor_id": principal.actor_id,
                   "candidate_application_id": candidate_application_id,
                   "exp": int(time.time()) + _TTL_SECONDS}
        return {"status": "success", "conversation_id": conversation_id,
                "conversation_token": _encode(payload), "expires_in_seconds": _TTL_SECONDS}

    async def answer(self, *, principal: ActorPrincipal, conversation_token: str,
                     question: str, client_turn_id: str = "") -> dict[str, Any]:
        scope = _decode(conversation_token)
        if (not scope or scope.get("workspace_id") != principal.workspace_id
                or scope.get("actor_id") != principal.actor_id):
            return _error("conversation_scope_invalid",
                          "Conversation scope is invalid or expired.", 403)
        sandbox = await self.store.get("hiring_sandbox_runs", scope["sandbox_run_id"])
        active = require_sandbox(sandbox or {})
        if active.get("error"):
            return active
        gate = authorize(principal, "read_role")
        if gate.get("error"):
            return gate
        question = (question or "").strip()
        if not question or len(question) > 2000:
            return _error("conversation_question_invalid", "Question is invalid.", 400)
        role = await self.store.get("hiring_roles", str(sandbox["role_id"])) or {}
        candidate_id = str(scope.get("candidate_application_id") or "")
        candidate = None
        if candidate_id:
            # The token proves which candidate was selected, never that the
            # actor may still read it. Membership can be revoked mid-token, so
            # every turn re-runs the current membership gate.
            candidate_gate = authorize(
                principal, "read_candidate")
            if candidate_gate.get("error"):
                return candidate_gate
            candidate = await self.store.get("candidate_applications", candidate_id)
            if (not candidate
                    or candidate.get("workspace_id") != principal.workspace_id
                    or candidate.get("role_id") != sandbox["role_id"]):
                return _error("candidate_scope_invalid",
                              "Candidate is outside this hiring run.", 404)
        lower = question.casefold()
        refs = [f"hiring_sandbox_runs/{sandbox['sandbox_run_id']}"]
        if any(word in lower for word in ("can you", "what can", "capability")):
            answer = ("In this synthetic sandbox, I can explain committed hiring "
                      "records, prepare drafts, and open an exact approval review. "
                      "I cannot choose a candidate, send without approval, access "
                      "ordinary connector accounts, or attend an interview.")
            intent = "EXPLAIN"
        elif any(word in lower for word in ("where", "status", "next")):
            candidate_state = str((candidate or {}).get("candidate_state")
                                  or "no candidate selected")
            answer = (f"This sandbox is active for the role. The selected candidate "
                      f"state is {candidate_state}. Check the cited timeline for the "
                      "next founder approval or durable wait.")
            intent = "STATUS"
            if candidate_id:
                refs.append(f"candidate_applications/{candidate_id}")
        else:
            answer = ("I can answer from this selected synthetic Hiring Run only. "
                      "Ask about its status, committed evidence, approvals, or process "
                      "retrospective; I will cite the durable records I use.")
            intent = "OUT_OF_SCOPE"
        if not validate_answer_text(answer):
            return _error("forbidden_hiring_output", "Answer failed hiring safety validation.", 500)
        # A turn is idempotent per client request, not per question text.
        # Keying on the question alone replayed the first answer forever, so a
        # repeated "where are we?" kept reporting pre-decision state. When the
        # caller supplies no request id, fall back to a fingerprint of the
        # records this answer was derived from: a genuine retry against
        # unchanged state still dedupes, but changed state yields a new turn.
        question_hash = hashlib.sha256(question.encode()).hexdigest()
        turn_key = client_turn_id or ":".join((
            question_hash,
            str(sandbox.get("version", "")),
            str(role.get("version", "")),
            str((candidate or {}).get("version", "")),
        ))
        turn_id = stable_id("hcturn", scope["conversation_id"], turn_key)
        row = {
            "schema_version": 1, "turn_id": turn_id, "conversation_id": scope["conversation_id"],
            "sandbox_run_id": sandbox["sandbox_run_id"], "workspace_id": principal.workspace_id,
            "actor_id": principal.actor_id, "candidate_application_id": candidate_id,
            "question_hash": "sha256:" + question_hash,
            "client_turn_id": client_turn_id or None,
            "answer": answer, "record_refs": refs, "intent": intent,
            "synthetic": True, "fixture_id": sandbox["fixture_id"],
            "synthetic_namespace": sandbox["synthetic_namespace"],
            "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
        }
        created = await self.store.create("hiring_conversation_turns", turn_id, row)
        stored = row if created else await self.store.get("hiring_conversation_turns", turn_id)
        return {"status": "success", "duplicate": not created, "answer": stored["answer"],
                "intent": stored["intent"], "record_refs": stored["record_refs"]}


class HiringCandidateConversationService:
    """Candidate-scoped, evidence-only Founder discussion without judgment.

    The service is deterministic and re-authorizes every turn.  It projects
    only committed candidate/application and criterion-coverage records; it
    never reveals identity, opens a provider, changes candidate state, or asks
    a model to score, rank, recommend, advance, or reject anyone.
    """

    def __init__(self, store: DurableStore):
        self.store = store

    async def begin(self, *, principal: ActorPrincipal,
                    candidate_application_id: str) -> dict[str, Any]:
        gate = authorize(principal, "read_candidate")
        if gate.get("error"):
            return gate
        candidate = await self.store.get(
            "candidate_applications", candidate_application_id)
        if (not candidate
                or candidate.get("workspace_id") != principal.workspace_id):
            return _error("candidate_scope_invalid",
                          "Candidate is unavailable in this workspace.", 404)
        role = await self.store.get("hiring_roles", str(candidate.get("role_id") or ""))
        if not role or role.get("workspace_id") != principal.workspace_id:
            return _error("candidate_scope_invalid",
                          "Candidate is unavailable in this workspace.", 404)
        conversation_id = stable_id(
            "hct", candidate_application_id, principal.actor_id, "evidence")
        payload = {
            "v": 1, "kind": "CANDIDATE_EVIDENCE",
            "conversation_id": conversation_id,
            "workspace_id": principal.workspace_id,
            "actor_id": principal.actor_id,
            "role_id": candidate["role_id"],
            "candidate_application_id": candidate_application_id,
            "exp": int(time.time()) + _TTL_SECONDS,
        }
        return {"status": "success", "conversation_id": conversation_id,
                "conversation_token": _encode(payload),
                "expires_in_seconds": _TTL_SECONDS,
                "scope": {"role_id": candidate["role_id"],
                          "candidate_code": candidate.get("candidate_code"),
                          "identity_visible": False}}

    async def answer(self, *, principal: ActorPrincipal,
                     conversation_token: str, question: str,
                     client_turn_id: str = "") -> dict[str, Any]:
        scope = _decode(conversation_token)
        if (not scope or scope.get("kind") != "CANDIDATE_EVIDENCE"
                or scope.get("workspace_id") != principal.workspace_id
                or scope.get("actor_id") != principal.actor_id):
            return _error("conversation_scope_invalid",
                          "Candidate conversation scope is invalid or expired.", 403)
        gate = authorize(principal, "read_candidate")
        if gate.get("error"):
            return gate
        candidate_id = str(scope.get("candidate_application_id") or "")
        candidate = await self.store.get("candidate_applications", candidate_id)
        if (not candidate
                or candidate.get("workspace_id") != principal.workspace_id
                or candidate.get("role_id") != scope.get("role_id")):
            return _error("candidate_scope_invalid",
                          "Candidate is unavailable in this workspace.", 404)
        question = (question or "").strip()
        if not question or len(question) > 2000:
            return _error("conversation_question_invalid", "Question is invalid.", 400)
        assessment = await self.store.get(
            "candidate_assessments", str(candidate.get("current_assessment_id") or ""))
        criteria = list((assessment or {}).get("criteria") or [])
        refs = [f"candidate_applications/{candidate_id}"]
        if assessment:
            refs.append(f"candidate_assessments/{assessment.get('assessment_id')}")
        if _JUDGMENT_REQUEST.search(question):
            answer = (
                "That asks for a hiring judgment. I can summarize committed "
                "candidate-provided information by approved criterion, but only "
                "the Founder can decide a next phase or decline an application.")
            intent = "JUDGMENT_REFUSED"
        elif any(word in question.casefold() for word in (
                "evidence", "present", "missing", "unclear", "criterion", "criteria")):
            labels = {"SUPPORTED": "present", "UNKNOWN": "missing",
                      "PARTIAL": "unclear", "CONTRADICTED": "unclear"}
            counts = {"present": 0, "missing": 0, "unclear": 0}
            for criterion in criteria:
                counts[labels.get(str(criterion.get("status")), "unclear")] += 1
            if criteria:
                answer = (
                    "The committed criterion map shows "
                    f"{counts['present']} present, {counts['missing']} missing, and "
                    f"{counts['unclear']} unclear. Open Evidence for citations, "
                    "unknowns, and contradictions; these are coverage labels, not "
                    "a hiring judgment.")
            else:
                answer = (
                    "No committed criterion map is available yet. The application "
                    "remains received for Founder review; absence of a map is not "
                    "negative evidence.")
            intent = "EVIDENCE_COVERAGE"
        elif any(word in question.casefold() for word in (
                "status", "where", "next", "phase")):
            answer = (
                f"This application is {candidate.get('candidate_state', 'RECEIVED')}. "
                "The next phase changes only when the Founder uses the explicit "
                "decision control; this conversation cannot change it.")
            intent = "STATUS"
        else:
            answer = (
                "I can discuss this candidate's committed criterion coverage, "
                "citations, unknowns, contradictions, and durable timeline. "
                "Identity stays hidden here and this conversation cannot change "
                "the application or perform an external action.")
            intent = "SCOPE"
        if not validate_answer_text(answer):
            return _error("forbidden_hiring_output",
                          "Answer failed hiring safety validation.", 500)
        turn_key = client_turn_id or ":".join((
            hashlib.sha256(question.encode()).hexdigest(),
            str(candidate.get("version", "")),
            str((assessment or {}).get("version", "")),
        ))
        turn_id = stable_id("hcturn", scope["conversation_id"], turn_key)
        row = {
            "schema_version": 1, "turn_id": turn_id,
            "conversation_id": scope["conversation_id"],
            "conversation_kind": "CANDIDATE_EVIDENCE",
            "sandbox_run_id": None, "workspace_id": principal.workspace_id,
            "actor_id": principal.actor_id,
            "role_id": candidate["role_id"],
            "candidate_application_id": candidate_id,
            "question_hash": "sha256:" + hashlib.sha256(question.encode()).hexdigest(),
            "client_turn_id": client_turn_id or None,
            "answer": answer, "record_refs": refs, "intent": intent,
            "synthetic": candidate.get("synthetic") is True,
            "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
        }
        created = await self.store.create("hiring_conversation_turns", turn_id, row)
        stored = row if created else await self.store.get(
            "hiring_conversation_turns", turn_id)
        return {"status": "success", "duplicate": not created,
                "answer": stored["answer"], "intent": stored["intent"],
                "record_refs": stored["record_refs"]}

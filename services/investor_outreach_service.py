"""Production-bounded investor outreach vertical on the generic runtime.

Research is advisory and source-cited. Drafts are immutable and recipient
bound. Email is the only external effect and crosses the platform consequence
boundary only after an exact human approval. Replies correlate by the provider
thread created by that send, never by subject similarity or active session.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from services import alex_mailbox, capability_registry, discovery_service
from services.actor_identity import ActorPrincipal
from services.canonical import canonical_hash
from services.consequence_service import ConsequenceService
from services.durable_store import AtomicMutation, DurableStore, production_store
from services.platform_approval_service import PlatformApprovalService
from services.workflow_contracts import RunKind, stable_id, utc_now
from services.workflow_runtime import WorkflowRuntime

SearchFn = Callable[[str, int], Awaitable[dict[str, Any]]]
SendFn = Callable[..., Awaitable[dict[str, Any]]]

_EMAIL = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,190}$")
_STATES = {
    "SCOPING", "SOURCING", "QUALIFYING", "DRAFTING_OUTREACH",
    "AWAITING_SEND_APPROVAL", "SENT", "WAITING_FOR_REPLY",
    "MEETING_PREP", "CLOSED",
}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


def _clean(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


class InvestorOutreachService:
    def __init__(self, store: DurableStore | None = None, *,
                 search_fn: SearchFn | None = None,
                 send_fn: SendFn | None = None):
        self.store = store or production_store()
        self.runtime = WorkflowRuntime(self.store)
        self.search_fn = search_fn or discovery_service.search_programs
        self.send_fn = send_fn or alex_mailbox.send_prepared_message

    def _enabled(self) -> dict[str, Any] | None:
        try:
            for capability_id in (
                    "investor.search", "investor.rank", "outreach.draft",
                    "reply.correlate", "meeting_brief.compose"):
                capability_registry.require_static(capability_id)
            capability_registry.require_external_action("send_email", "alex_mail")
        except ValueError:
            return _error("workflow_disabled", "Investor outreach is disabled.")
        return None

    async def prepare_start_creation(
            self, *, principal: ActorPrincipal, objective: str,
            origin_session_id: str, client_request_id: str,
            artifact_refs: list[str] | None = None,
            max_candidates: int = 20) -> dict[str, Any]:
        """Build the create-only authority rows for command transaction T8."""
        if (disabled := self._enabled()) is not None:
            return disabled
        objective = _clean(objective, 800)
        refs = [str(value) for value in (artifact_refs or [])]
        if (not objective or not origin_session_id or not client_request_id
                or not 1 <= max_candidates <= 50
                or len(refs) > 8
                or any(not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", ref)
                       for ref in refs)):
            return _error("outreach_contract_invalid",
                          "Objective, session, references, or limit is invalid.")
        workspace_id = principal.workspace_id
        outreach_id = stable_id(
            "outreach", workspace_id, client_request_id)
        existing = await self.store.get("investor_outreach", outreach_id)
        request_hash = canonical_hash({
            "objective": objective, "artifact_refs": refs,
            "max_candidates": max_candidates,
        }, domain="investor-outreach-request")
        if existing:
            if (existing.get("workspace_id") != workspace_id
                    or existing.get("request_hash") != request_hash):
                return _error("idempotency_conflict",
                              "Outreach request key names different work.")
            run = await self.store.get("workflow_runs", existing["run_id"])
            if not run:
                return _error("outreach_run_missing",
                              "Investor outreach has no durable run.")
            return {"status": "success", "duplicate": True,
                    "outreach": existing, "run": run, "mutations": ()}
        journey_id = stable_id("journey", workspace_id, client_request_id)
        run_creation = await self.runtime.prepare_run_creation(
            workspace_id=workspace_id, journey_id=journey_id,
            run_kind=RunKind.INVESTOR_OUTREACH,
            workflow_kind="investor_outreach:v1",
            idempotency_key=client_request_id, domain_ref=outreach_id,
            originating_actor_id=principal.actor_id,
            origin_session_id=origin_session_id,
            budgets={"max_steps": 30, "max_model_calls": 20,
                     "max_provider_calls": 10})
        if run_creation.get("error"):
            return run_creation
        now = utc_now()
        row = {
            "schema_version": 1, "outreach_id": outreach_id,
            "workspace_id": workspace_id, "run_id": run_creation["run_id"],
            "journey_id": journey_id, "origin_session_id": origin_session_id,
            "originating_actor_id": principal.actor_id,
            "objective": objective, "request_hash": request_hash,
            "artifact_refs": refs, "max_candidates": max_candidates,
            "domain_state": "SCOPING", "candidate_count": 0,
            "draft_count": 0, "sent_count": 0, "reply_count": 0,
            "created_at": now, "updated_at": now, "version": 1,
        }
        return {
            "status": "success", "duplicate": False,
            "outreach": row, "run": run_creation["run_record"],
            "mutations": (*run_creation["mutations"], AtomicMutation(
                "investor_outreach", outreach_id, None, record=row)),
        }

    async def _ensure_steps(self, run_id: str, outreach_id: str) -> dict[str, Any]:
        for step_key in (
                "research", "rank", "draft", "human_approval", "send",
                "wait_reply", "meeting_brief"):
            step = await self.runtime.create_step(
                run_id, step_key=step_key,
                idempotency_key=f"{outreach_id}:{step_key}")
            if step.get("error"):
                return step
        return {"status": "success"}

    async def recover_start(self, *, workspace_id: str,
                            outreach_id: str) -> dict[str, Any]:
        """Finish idempotent scaffolding after a post-commit process death."""
        row = await self._outreach(workspace_id, outreach_id)
        if not row:
            return _error("outreach_not_found", "Investor outreach does not exist.")
        run = await self.store.get("workflow_runs", str(row.get("run_id") or ""))
        if not run or run.get("workspace_id") != workspace_id:
            return _error("outreach_run_missing",
                          "Investor outreach has no durable run.")
        steps = await self._ensure_steps(run["run_id"], outreach_id)
        if steps.get("error"):
            return steps
        await self.runtime.publish_created_run(run)
        return {"status": "success", "outreach": row, "run": run}

    async def start(self, *, principal: ActorPrincipal, objective: str,
                    origin_session_id: str, client_request_id: str,
                    artifact_refs: list[str] | None = None,
                    max_candidates: int = 20) -> dict[str, Any]:
        """Create or recover the reviewed outreach run and its static steps."""
        prepared = await self.prepare_start_creation(
            principal=principal, objective=objective,
            origin_session_id=origin_session_id,
            client_request_id=client_request_id,
            artifact_refs=artifact_refs, max_candidates=max_candidates)
        if prepared.get("error"):
            return prepared
        created = not bool(prepared.get("duplicate"))
        if created:
            committed = await self.store.atomic_compare_and_set(
                prepared["mutations"])
            if not committed:
                prepared = await self.prepare_start_creation(
                    principal=principal, objective=objective,
                    origin_session_id=origin_session_id,
                    client_request_id=client_request_id,
                    artifact_refs=artifact_refs,
                    max_candidates=max_candidates)
                if prepared.get("error") or not prepared.get("duplicate"):
                    return (_error("idempotency_conflict",
                                   "Outreach creation raced.")
                            if not prepared.get("error") else prepared)
                created = False
            else:
                prepared = {
                    **prepared,
                    "outreach": committed[(
                        "investor_outreach",
                        prepared["outreach"]["outreach_id"])],
                    "run": committed[(
                        "workflow_runs", prepared["run"]["run_id"])],
                }
        row = prepared["outreach"]
        run = prepared["run"]
        steps = await self._ensure_steps(run["run_id"], row["outreach_id"])
        if steps.get("error"):
            return steps
        await self.runtime.publish_created_run(run)
        return {"status": "success", "duplicate": not created,
                "outreach": row, "run": run}

    async def _outreach(self, workspace_id: str,
                        outreach_id: str) -> dict[str, Any] | None:
        row = await self.store.get("investor_outreach", outreach_id)
        return row if row and row.get("workspace_id") == workspace_id else None

    async def _set_state(self, row: dict[str, Any], next_state: str,
                         **updates: Any) -> dict[str, Any]:
        if next_state not in _STATES:
            return _error("outreach_state_invalid", "Outreach state is invalid.")
        committed = await self.store.compare_and_set(
            "investor_outreach", row["outreach_id"], int(row["version"]), {
                **updates, "domain_state": next_state,
                "updated_at": utc_now(),
            })
        if not committed:
            return _error("concurrency_conflict", "Outreach changed concurrently.")
        return committed

    async def _complete_step(self, run_id: str, step_key: str,
                             result_ref: str) -> dict[str, Any]:
        step_id = stable_id("step", run_id, step_key)
        step = await self.store.get("workflow_steps", step_id)
        if not step:
            return _error("step_not_found", "Outreach step does not exist.")
        if step.get("status") == "COMPLETE":
            return {"status": "success", "duplicate": True, **step}
        lease_owner = f"investor:{uuid.uuid4().hex}"
        claimed = await self.runtime.claim_step(
            step_id, lease_owner=lease_owner, lease_seconds=120,
            workload={"principal_kind": "INTERNAL_SERVICE",
                      "service": "investor_outreach"})
        if claimed.get("error"):
            return claimed
        return await self.runtime.complete_step(
            step_id, lease_owner=lease_owner,
            generation=int(claimed["attempt_generation"]),
            result_ref=result_ref)

    async def research(self, *, workspace_id: str,
                       outreach_id: str) -> dict[str, Any]:
        if (disabled := self._enabled()) is not None:
            return disabled
        outreach = await self._outreach(workspace_id, outreach_id)
        if not outreach:
            return _error("outreach_not_found", "Investor outreach does not exist.")
        existing = await self.store.list(
            "investor_candidates",
            filters={"workspace_id": workspace_id, "outreach_id": outreach_id},
            limit=100)
        if existing and outreach.get("domain_state") not in {"SCOPING", "SOURCING"}:
            return {"status": "success", "duplicate": True,
                    "candidates": existing, "outreach": outreach}
        await self._set_state(outreach, "SOURCING")
        search = await self.search_fn(
            f"{outreach['objective']} investors venture capital portfolio",
            int(outreach["max_candidates"]))
        if search.get("error"):
            return search
        results = list(search.get("results") or [])[:int(outreach["max_candidates"])]
        candidates: list[dict[str, Any]] = []
        for index, item in enumerate(results):
            url = _clean(item.get("url"), 1000)
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            name = _clean(item.get("title") or parsed.netloc, 200)
            email = _clean(item.get("email"), 320).lower()
            if email and not _EMAIL.fullmatch(email):
                email = ""
            candidate_id = stable_id("investor", outreach_id, url)
            row = {
                "schema_version": 1, "candidate_id": candidate_id,
                "workspace_id": workspace_id, "outreach_id": outreach_id,
                "run_id": outreach["run_id"], "display_name": name,
                "recipient_email": email or None,
                "source_url": url, "source_title": name,
                "source_excerpt": _clean(item.get("snippet"), 500),
                "source_trust": "UNTRUSTED_EXTERNAL",
                "source_rank": index + 1, "score": None,
                "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
            }
            await self.store.create("investor_candidates", candidate_id, row)
            candidates.append(await self.store.get(
                "investor_candidates", candidate_id) or row)
        current = await self._outreach(workspace_id, outreach_id)
        advanced = await self._set_state(
            current, "QUALIFYING", candidate_count=len(candidates))
        await self._complete_step(
            outreach["run_id"], "research", f"candidate-set:{outreach_id}")
        return {"status": "success", "duplicate": False,
                "candidates": candidates, "outreach": advanced}

    async def rank(self, *, workspace_id: str,
                   outreach_id: str) -> dict[str, Any]:
        if (disabled := self._enabled()) is not None:
            return disabled
        outreach = await self._outreach(workspace_id, outreach_id)
        if not outreach:
            return _error("outreach_not_found", "Investor outreach does not exist.")
        candidates = await self.store.list(
            "investor_candidates",
            filters={"workspace_id": workspace_id, "outreach_id": outreach_id},
            limit=100)
        if not candidates:
            return _error("candidate_set_missing", "Research produced no candidates.")
        terms = {word for word in re.findall(r"[a-z0-9]{3,}",
                                             outreach["objective"].lower())}
        ranked = []
        for row in candidates:
            text = f"{row.get('display_name', '')} {row.get('source_excerpt', '')}".lower()
            overlap = sum(1 for term in terms if term in text)
            score = max(0, 100 - int(row.get("source_rank") or 1) * 2 + overlap * 3)
            current = await self.store.get("investor_candidates", row["candidate_id"])
            if current:
                await self.store.compare_and_set(
                    "investor_candidates", row["candidate_id"],
                    int(current["version"]), {
                        "score": score,
                        "score_evidence_refs": [f"url:{row['source_url']}"],
                        "score_model": "deterministic_overlap_v1",
                        "updated_at": utc_now(),
                    })
            ranked.append({**row, "score": score})
        ranked.sort(key=lambda item: (-int(item["score"]), item["candidate_id"]))
        current = await self._outreach(workspace_id, outreach_id)
        advanced = await self._set_state(current, "DRAFTING_OUTREACH")
        await self._complete_step(
            outreach["run_id"], "rank", f"ranked-set:{outreach_id}")
        return {"status": "success", "ranked": ranked, "outreach": advanced}

    async def draft(self, *, workspace_id: str, outreach_id: str,
                    limit: int = 5) -> dict[str, Any]:
        if (disabled := self._enabled()) is not None:
            return disabled
        outreach = await self._outreach(workspace_id, outreach_id)
        if not outreach or not 1 <= limit <= 20:
            return _error("outreach_contract_invalid", "Draft request is invalid.")
        candidates = await self.store.list(
            "investor_candidates",
            filters={"workspace_id": workspace_id, "outreach_id": outreach_id},
            limit=100)
        candidates = [row for row in candidates if row.get("score") is not None]
        candidates.sort(key=lambda item: (-int(item["score"]), item["candidate_id"]))
        drafts = []
        for candidate in candidates[:limit]:
            draft_id = stable_id("draft", outreach_id, candidate["candidate_id"])
            subject = "Introduction: Ruhu AI"
            body = (
                f"Hello {_clean(candidate['display_name'], 120)},\n\n"
                "I’m building Ruhu AI, an AI co-founder platform for durable, "
                "human-controlled company operations. I’m reaching out because "
                f"your published investment profile may align with this objective: "
                f"{_clean(outreach['objective'], 300)}.\n\n"
                "If this is relevant, I’d value a short conversation.\n\n"
                "Best,\nThe Ruhu AI team")
            content_hash = canonical_hash(
                {"to": candidate.get("recipient_email"), "subject": subject,
                 "body": body}, domain="investor-outreach-draft")
            row = {
                "schema_version": 1, "draft_id": draft_id,
                "workspace_id": workspace_id, "outreach_id": outreach_id,
                "run_id": outreach["run_id"],
                "candidate_id": candidate["candidate_id"],
                "recipient_email": candidate.get("recipient_email"),
                "subject": subject, "body": body, "content_hash": content_hash,
                "evidence_refs": [f"candidate:{candidate['candidate_id']}",
                                  f"url:{candidate['source_url']}"],
                "status": "DRAFTED", "approval_id": None, "action_id": None,
                "provider_effect_id": None, "provider_thread_id": None,
                "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
            }
            await self.store.create("outreach_drafts", draft_id, row)
            drafts.append(await self.store.get("outreach_drafts", draft_id) or row)
        current = await self._outreach(workspace_id, outreach_id)
        advanced = await self._set_state(
            current, "AWAITING_SEND_APPROVAL", draft_count=len(drafts))
        await self._complete_step(
            outreach["run_id"], "draft", f"draft-set:{outreach_id}")
        return {"status": "success", "drafts": drafts, "outreach": advanced}

    async def request_send_approval(
            self, *, principal: ActorPrincipal, draft_id: str,
            client_request_id: str) -> dict[str, Any]:
        if (disabled := self._enabled()) is not None:
            return disabled
        draft = await self.store.get("outreach_drafts", draft_id)
        if (not draft or draft.get("workspace_id") != principal.workspace_id
                or draft.get("status") not in {"DRAFTED", "AWAITING_APPROVAL"}
                or not _EMAIL.fullmatch(str(draft.get("recipient_email") or ""))):
            return _error("draft_not_sendable", "Draft has no exact recipient.")
        existing_approval = (await self.store.get(
            "approvals", str(draft.get("approval_id") or ""))
            if draft.get("approval_id") else None)
        if (existing_approval
                and existing_approval.get("status") in {
                    "PENDING", "GRANTED", "CLAIMED"}):
            return {"status": "success", "duplicate": True,
                    "approval": existing_approval}
        run = await self.store.get("workflow_runs", draft["run_id"])
        if not run or run.get("runtime_status") in {
                "CANCELLING", "CANCELLED", "SUCCEEDED", "FAILED", "REJECTED"}:
            return _error("run_fenced", "Outreach run no longer accepts approval.")
        step_id = stable_id("step", draft["run_id"], "send")
        approval = await PlatformApprovalService(self.store).request(
            workspace_id=principal.workspace_id,
            requested_by_actor_id=principal.actor_id,
            run_id=draft["run_id"], plan_hash=run["plan_hash"],
            step_id=step_id, capability_id="external.send_email",
            capability_version="1.0.0", action_kind="send_email",
            target={"recipient_email": draft["recipient_email"],
                    "draft_id": draft_id, "outreach_id": draft["outreach_id"]},
            payload={"subject": draft["subject"], "body": draft["body"]},
            policy_id="exact_human_approval.v1", policy_version="1",
            domain_ref=draft["outreach_id"], domain_version=1,
            connector_id="alex_mail", connector_binding_version="gmail-v1",
            client_request_id=client_request_id,
            origin_session_id=(await self._outreach(
                principal.workspace_id, draft["outreach_id"]))["origin_session_id"],
            legacy_target=f"email:{draft['outreach_id']}",
            legacy_gate="send_email",
            presentation_details={"to": draft["recipient_email"],
                                  "subject": draft["subject"],
                                  "body": draft["body"]},
            approval_domain="INVESTOR_OUTREACH")
        if approval.get("error"):
            return approval
        current = await self.store.get("outreach_drafts", draft_id)
        await self.store.compare_and_set(
            "outreach_drafts", draft_id, int(current["version"]), {
                "approval_id": approval["approval"]["approval_id"],
                "status": "AWAITING_APPROVAL", "updated_at": utc_now()})
        wait = await self.runtime.create_wait(
            draft["run_id"], wait_kind="FOUNDER_APPROVAL",
            correlation_key=approval["approval"]["approval_id"],
            origin_session_id=(await self._outreach(
                principal.workspace_id, draft["outreach_id"]))["origin_session_id"])
        return {"status": "success", "approval": approval["approval"],
                "wait": wait}

    async def send_approved(self, *, workspace_id: str,
                            draft_id: str) -> dict[str, Any]:
        if (disabled := self._enabled()) is not None:
            return disabled
        draft = await self.store.get("outreach_drafts", draft_id)
        if not draft or draft.get("workspace_id") != workspace_id:
            return _error("draft_not_found", "Outreach draft does not exist.")
        if draft.get("status") == "SENT":
            return {"status": "success", "duplicate": True, "draft": draft}
        approval_id = str(draft.get("approval_id") or "")
        if not approval_id:
            return _error("approval_missing", "Draft has no approval request.")
        service = ConsequenceService(self.store)
        target = {"recipient_email": draft["recipient_email"],
                  "draft_id": draft_id, "outreach_id": draft["outreach_id"]}
        payload = {"subject": draft["subject"], "body": draft["body"]}
        prepared = await service.prepare(
            workspace_id=workspace_id, approval_id=approval_id,
            run_id=draft["run_id"],
            step_id=stable_id("step", draft["run_id"], "send"),
            connector_id="alex_mail", action_kind="send_email",
            idempotency_key=f"investor-send:{draft_id}:{draft['content_hash']}",
            target=target, payload=payload)
        if prepared.get("error"):
            return prepared
        if prepared.get("duplicate"):
            action = prepared["action"]
            if action.get("status") == "SUCCEEDED":
                return await self._apply_send_receipt(draft, action)
            return {"status": "error", "error": True,
                    "error_code": ("reconciliation_required"
                                   if action.get("status") == "UNCERTAIN"
                                   else "action_in_progress"),
                    "action": action}
        started = await service.start(
            workspace_id=workspace_id, action_id=prepared["action"]["action_id"],
            lease_owner=prepared["lease_owner"],
            workload_principal="worker:investor-outreach")
        if started.get("error"):
            return started
        await service.mark_provider_attempted(
            workspace_id=workspace_id, action_id=started["action"]["action_id"],
            lease_owner=prepared["lease_owner"])
        provider = await self.send_fn(
            workspace_id=workspace_id, to=draft["recipient_email"],
            subject=draft["subject"], body=draft["body"],
            provider_request_id=started["provider_request_id"])
        if provider.get("error"):
            settled = await service.settle(
                workspace_id=workspace_id, action_id=started["action"]["action_id"],
                lease_owner=prepared["lease_owner"], status="UNCERTAIN",
                result_ref={"rfc822_message_id": provider.get(
                    "rfc822_message_id", "")},
                error_code=str(provider.get("error_code") or "provider_unavailable"),
                uncertainty_reason=str(provider.get(
                    "uncertainty_reason") or "provider_outcome_unconfirmed"))
            return {**settled, "status": "error", "error": True,
                    "error_code": "provider_outcome_uncertain"}
        settled = await service.settle(
            workspace_id=workspace_id, action_id=started["action"]["action_id"],
            lease_owner=prepared["lease_owner"], status="SUCCEEDED",
            provider_effect_id=str(provider.get("provider_effect_id") or ""),
            result_ref={"provider_thread_id": provider.get("provider_thread_id", ""),
                        "rfc822_message_id": provider.get("rfc822_message_id", "")})
        if settled.get("error"):
            return settled
        action = settled["action"]
        return await self._apply_send_receipt(draft, action)

    async def _apply_send_receipt(self, draft: dict[str, Any],
                                  action: dict[str, Any]) -> dict[str, Any]:
        result_ref = dict(action.get("result_ref") or {})
        current = await self.store.get("outreach_drafts", draft["draft_id"])
        newly_sent = bool(current and current.get("status") != "SENT")
        if current and current.get("status") != "SENT":
            await self.store.compare_and_set(
                "outreach_drafts", draft["draft_id"], int(current["version"]), {
                    "status": "SENT", "action_id": action["action_id"],
                    "provider_effect_id": action.get("provider_effect_id"),
                    "provider_thread_id": result_ref.get("provider_thread_id"),
                    "rfc822_message_id": result_ref.get("rfc822_message_id"),
                    "sent_at": utc_now(), "updated_at": utc_now()})
        approval_waits = await self.store.list(
            "waits", filters={"run_id": draft["run_id"],
                              "correlation_key": draft["approval_id"]}, limit=2)
        for wait in approval_waits:
            if wait.get("status") == "OPEN":
                await self.runtime.resolve_wait(
                    wait["wait_id"], event_id=f"approval-consumed:{action['action_id']}",
                    expected_generation=int(wait.get("generation") or 1))
        outreach = await self._outreach(draft["workspace_id"], draft["outreach_id"])
        if outreach.get("domain_state") in {"AWAITING_SEND_APPROVAL", "SENT"}:
            sent = await self.store.list(
                "outreach_drafts",
                filters={"workspace_id": draft["workspace_id"],
                         "outreach_id": draft["outreach_id"],
                         "status": "SENT"}, limit=100)
            outreach = await self._set_state(
                outreach, "WAITING_FOR_REPLY", sent_count=len(sent))
        for step_key, result in (
                ("human_approval", f"approval:{draft['approval_id']}"),
                ("send", f"action:{action['action_id']}")):
            completed = await self._complete_step(
                draft["run_id"], step_key, result)
            if completed.get("error"):
                return {**_error(
                    "action_projection_reconciliation_required",
                    "The email receipt is durable but the run projection needs repair."),
                    "action": action,
                    "draft": await self.store.get(
                        "outreach_drafts", draft["draft_id"])}
        correlation = str(result_ref.get("provider_thread_id")
                          or result_ref.get("rfc822_message_id") or action["action_id"])
        wait = await self.runtime.create_wait(
            draft["run_id"], wait_kind="EMAIL_REPLY",
            correlation_key=correlation,
            origin_session_id=outreach["origin_session_id"])
        return {"status": "success", "duplicate": not newly_sent,
                "draft": await self.store.get("outreach_drafts", draft["draft_id"]),
                "action": action, "reply_wait": wait}

    async def record_reply(self, *, workspace_id: str,
                           provider_thread_id: str, provider_message_id: str,
                           sender: str, subject: str, excerpt: str) -> dict[str, Any]:
        if (disabled := self._enabled()) is not None:
            return disabled
        if not provider_thread_id or not provider_message_id:
            return _error("reply_contract_invalid", "Reply identifiers are required.")
        drafts = await self.store.list(
            "outreach_drafts", filters={"workspace_id": workspace_id,
                                        "provider_thread_id": provider_thread_id},
            limit=2)
        if len(drafts) != 1:
            return _error("reply_unmatched" if not drafts else "reply_ambiguous",
                          "Reply does not identify exactly one sent draft.")
        draft = drafts[0]
        reply_id = stable_id("reply", workspace_id, provider_message_id)
        row = {
            "schema_version": 1, "reply_id": reply_id,
            "workspace_id": workspace_id, "outreach_id": draft["outreach_id"],
            "run_id": draft["run_id"], "draft_id": draft["draft_id"],
            "provider_thread_id": provider_thread_id,
            "provider_message_id": provider_message_id,
            "sender": _clean(sender, 200), "subject": _clean(subject, 200),
            "excerpt": _clean(excerpt, 1000),
            "trust_class": "UNTRUSTED_EXTERNAL",
            "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
        }
        created = await self.store.create("investor_replies", reply_id, row)
        waits = await self.store.list(
            "waits", filters={"run_id": draft["run_id"],
                              "correlation_key": provider_thread_id}, limit=2)
        if len(waits) != 1:
            return _error("reply_wait_missing", "Reply wait is not uniquely correlated.")
        resolved = await self.runtime.resolve_wait(
            waits[0]["wait_id"], event_id=f"reply:{reply_id}",
            expected_generation=int(waits[0].get("generation") or 1))
        if resolved.get("error"):
            return resolved
        outreach = await self._outreach(workspace_id, draft["outreach_id"])
        if outreach.get("domain_state") == "WAITING_FOR_REPLY":
            outreach = await self._set_state(
                outreach, "MEETING_PREP",
                reply_count=int(outreach.get("reply_count") or 0) + 1)
        completed = await self._complete_step(
            draft["run_id"], "wait_reply", f"reply:{reply_id}")
        if completed.get("error"):
            return completed
        return {"status": "success", "duplicate": not created,
                "reply": await self.store.get("investor_replies", reply_id),
                "outreach": outreach}

    async def create_meeting_brief(self, *, workspace_id: str,
                                   outreach_id: str) -> dict[str, Any]:
        if (disabled := self._enabled()) is not None:
            return disabled
        outreach = await self._outreach(workspace_id, outreach_id)
        if not outreach:
            return _error("outreach_not_found", "Investor outreach does not exist.")
        replies = await self.store.list(
            "investor_replies",
            filters={"workspace_id": workspace_id, "outreach_id": outreach_id},
            limit=20)
        if not replies:
            return _error("reply_missing", "No correlated reply supports a brief.")
        reply = sorted(replies, key=lambda row: row["created_at"])[-1]
        draft = await self.store.get("outreach_drafts", reply["draft_id"])
        candidate = await self.store.get(
            "investor_candidates", draft["candidate_id"])
        brief_id = stable_id("brief", outreach_id, reply["reply_id"])
        row = {
            "schema_version": 1, "brief_id": brief_id,
            "workspace_id": workspace_id, "outreach_id": outreach_id,
            "run_id": outreach["run_id"],
            "title": f"Meeting brief: {_clean(candidate['display_name'], 160)}",
            "summary": ("Prepare to discuss Ruhu AI and the founder’s objective. "
                        f"The correlated reply subject was: {_clean(reply['subject'], 200)}"),
            "reply_excerpt": reply["excerpt"],
            "evidence_refs": [f"candidate:{candidate['candidate_id']}",
                              f"reply:{reply['reply_id']}",
                              f"draft:{draft['draft_id']}"],
            "claims_are_evidence_bounded": True,
            "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
        }
        created = await self.store.create("meeting_briefs", brief_id, row)
        current = await self._outreach(workspace_id, outreach_id)
        if current.get("domain_state") != "CLOSED":
            current = await self._set_state(
                current, "CLOSED", meeting_brief_id=brief_id,
                closed_at=utc_now())
        await self._complete_step(
            outreach["run_id"], "meeting_brief", f"meeting-brief:{brief_id}")
        completed = await self.runtime.succeed_run(
            outreach["run_id"], result_ref=f"meeting_brief:{brief_id}")
        return {"status": "success", "duplicate": not created,
                "brief": await self.store.get("meeting_briefs", brief_id),
                "outreach": current, "run": completed}

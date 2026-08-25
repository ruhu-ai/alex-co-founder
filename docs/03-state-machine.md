# 03 — State Machine

Two lifecycles. The **opportunity lifecycle** is data-pipeline state (Firestore
`opportunities.state`). The **application lifecycle** is the conversational workflow
the orchestrator is grounded in (session `current_step`, mirrored to Firestore
`applications.state`). Refinement of the brief: `current_step` also admits `IDLE` and
`TRIAGE` so the orchestrator has well-defined behavior when no application is active.

## Opportunity lifecycle (Firestore, driven by tools)

```
DISCOVERED ──fit≥threshold──► SHORTLISTED
     │
     └──fit<threshold────► ARCHIVED (archive_reason required)
```

## Application lifecycle (session `current_step`)

```
IDLE ──founder opens app──► TRIAGE ──founder picks opportunity──► INTERVIEWING
INTERVIEWING ──no profile gaps──► DRAFTING
DRAFTING ──all sections drafted──► AWAITING_REVIEW
AWAITING_REVIEW ──all sections APPROVED──► APPROVED
APPROVED ──founder clicks "Fill form"──► FORM_FILLING
FORM_FILLING ──fill report written──► AWAITING_SUBMIT_APPROVAL
AWAITING_SUBMIT_APPROVAL ──valid approval token──► SUBMITTED
SUBMITTED ──confirmation recorded──► FOLLOW_UP
FOLLOW_UP ──result received / deadline passed──► CLOSED
```

Backward edges (all legal, all logged):
- `AWAITING_REVIEW → DRAFTING` for a single section when feedback type is `reject`
  or `edit` (section status → CHANGES_REQUESTED; other sections unaffected).
- `TRIAGE → IDLE` when founder closes the triage view.
- `FOLLOW_UP` stays current across many wake cycles until closure.

## Transition table (binding)

| # | From → To | Actor | Guard (must be true) | Side effects |
|---|---|---|---|---|
| 1 | DISCOVERED → SHORTLISTED | matchmaker | `fit_score ≥ 70` | `fit_score`, `fit_rationale`, urgency written |
| 2 | DISCOVERED → ARCHIVED | matchmaker | `fit_score < 70` | `archive_reason` mandatory |
| 3 | TRIAGE → INTERVIEWING | orchestrator | founder confirmed choice | application doc created; checklist initialized from `required_materials` |
| 4 | INTERVIEWING → DRAFTING | interviewer | zero unresolved gaps for required sections | all `interview_qa` persisted to profile |
| 5 | DRAFTING → AWAITING_REVIEW | drafter | every section has status ≥ DRAFTED; evidence-check report persisted. No model outcome blocks this edge — only failing to read the application or persist the report does, both pre-existing `complete_drafting` failures (20) | latest evidence-check pointer written; sections presented one at a time |
| 6 | AWAITING_REVIEW → APPROVED | orchestrator | all sections status=APPROVED | `submit_idempotency_key` generated |
| 7 | APPROVED → FORM_FILLING | founder (UI) | — | portal creds fetched at execution time only |
| 8 | FORM_FILLING → AWAITING_SUBMIT_APPROVAL | form-filler | fill report written (partial OK) | approval PENDING record created; UI prompts founder |
| 9 | AWAITING_SUBMIT_APPROVAL → SUBMITTED | form-filler | **server-side approval resolved** (GRANTED, unexpired, unconsumed) | approval → CONSUMED; confirmation stored; audit row |
| 10 | SUBMITTED → FOLLOW_UP | system | `submission.confirmation_id` present | followup schedule created |
| 11 | FOLLOW_UP → CLOSED | system/founder | result recorded or deadline passed | final audit row |

**Safety gates (eval-tested, see 11):**
- G1: no `save_draft_section` while `current_step == INTERVIEWING` and gaps remain.
- G2: no `submit_form` without a valid approval token — the tool itself validates,
  so even a mis-prompted model cannot submit. Refusal returns
  `{"error": true, "message": "Submission blocked: no approval"}` and writes an
  `audit` row with `result=refused`.
- G3: no `open_portal` before transition 8 (i.e., not before APPROVED).

## Dormancy & resume contract

The agent **never polls**. It declares what it waits for in `pending_signals` and
goes quiet. Events arrive via webhooks/tasks (see 07/08) and resume the session:

| Dormant at | `pending_signals` | Wake event | `state_delta` applied |
|---|---|---|---|
| AWAITING_REVIEW | `["founder_feedback"]` | `POST /api/feedback` (UI) → webhook | feedback recorded; step stays; `pending_signals=[]` |
| AWAITING_SUBMIT_APPROVAL | `["founder_approval"]` | `POST /api/approvals/{id}/resolve` | `status=GRANTED` → agent proceeds to submit |
| SUBMITTED (awaiting confirmation) | `["portal_confirmation"]` | `POST /webhooks/portal_event` | `current_step=FOLLOW_UP` |
| FOLLOW_UP | `["deadline_tick"]` | `POST /tasks/deadline_scan` | urgency/status refreshed |

**Deliberate deviation from the ADK 2 lab:** we resume parked runs via webhook
+ `state_delta` (the reference-repo pattern), not `LongRunningFunctionTool` +
`ResumabilityConfig` (the lab's in-invocation parking). Our waits are external
events — founder feedback, approval, portal confirmation — answered by people
and webhooks, not a single long tool call returning a handle. The `state_delta`
pattern fits that shape and is the one verified in the reference repo; the lab
pattern stays on the roadmap if a future workflow has true in-invocation waits.

**Resume handler rules (from the reference pattern):**
1. Hydrate the persisted session by `(user_id, session_id)` — never start a new one.
2. Call `runner.run_async(user_id=..., session_id=..., new_message=<wake notice>,
   state_delta={...})`. The `state_delta` transition lands **before** the next
   inference call, so the model sees the new `current_step` in its instruction and
   cannot hallucinate intermediate steps.
3. The wake `new_message` is a short system-style note, e.g.
   `"Resume: portal confirmed submission C-1042."` — not a fabricated user request.
4. Containers may be cold / scaled to zero. Session store must be durable
   (SQLite locally, Cloud SQL in prod) for this to work.

## `before_agent_callback` initialization

On every new session, initialize:

```python
state.setdefault("current_step", "IDLE")
state.setdefault("active_application_id", "")
state.setdefault("active_opportunity_id", "")
state.setdefault("active_program_requirements", [])
state.setdefault("checklist_status", {})
state.setdefault("pending_signals", [])
state.setdefault("current_section", "")
state.setdefault("browser_status", {"active": False, "kind": None, "run_id": None,
                                    "url": None, "goal": None, "last_action": None})  # 18
state.setdefault("user:profile_id", founder_id)  # resolved from auth/env
state.setdefault("user:prefs", {})
state.setdefault("app:workflow_id", workflow.workflow_id)
```

`browser_status` is the exception to setdefault-only: on **every** invocation
the callback reconciles it from Firestore `browser_runs` (18) — an `active`
projection whose run is closed or absent is rewritten before instruction
rendering, so the orchestrator template never shows a stale browser.

## Checklist model

Initialized on transition 4 from the opportunity's `required_materials` plus fixed
steps: `eligibility_check`, `interview`, `draft_sections`, `review`, `form_fill`,
`approval`, `submit`, `followup`. Each item:
`{key, label, status: PENDING|IN_PROGRESS|DONE|BLOCKED}`.
`checklist_status` in session state is the compact `{key: status}` projection that
gets injected into the orchestrator instruction.

## Acceptance checks

- [ ] `state_schema.py` defines `OpportunityState`, `ApplicationStep`, `ChecklistStatus` constants; transitions enforced by a `can_transition(from, to)` helper implementing the table.
- [ ] Entering APPROVED generates `submit_idempotency_key`; calling `submit_form` twice with the same key submits once (second call returns `{"error": true, "message": "Already submitted"}`).
- [ ] Killing the server at AWAITING_REVIEW, restarting, and POSTing feedback resumes the same session with checklist and drafts intact.
- [ ] Safety gates G1–G3 hold when tested via `adk web` with adversarial prompts ("just submit it now").

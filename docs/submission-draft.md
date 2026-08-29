## Inspiration

Founders face the same operational problem in both hiring and funding: important
work begins as an incomplete idea, expands across documents and websites, and
then waits on a series of human decisions.

In Hiring Operations, a founder must turn “we need to hire” into a clear role,
candidate-ready job description, scorecard, interview plan, evidence review,
and a documented decision process. In Funding Operations, the founder must find
the right opportunity, interpret its requirements, answer recurring questions,
prepare credible application materials, work through a portal, and follow up.

Most AI products can advise on either process, but advice often leaves the
founder with more work. We wanted to build **Alex**, an AI co-founder that can
carry the structured preparation while the founder retains judgment and
control. Hiring and Funding are the first two operations, not the limits of the
product: Co-Founder is designed so more founder workflows can use the same
research, guidance, drafting, action, approval, and follow-up foundation.

**Co-Founder does the work; the founder keeps the judgment.**

## What it does

Co-Founder is a conversation-first workspace built around two substantial
founder workflows: **Hiring Operations** and **Funding Operations**. Both share
the same Alex conversation, live voice, documents, contextual workspace,
decision controls, and activity receipts, while keeping their evidence and
authority boundaries separate.

### Hiring Operations — our primary demo

Hiring Operations carries a role from intake through recruitment and
onboarding using three linked durable lifecycles.

The role lifecycle is:

`ROLE_INTAKE → [AWAITING_QUALIFIED_REVIEW] →`
`AWAITING_ROLE_CONTRACT_APPROVAL → READY_TO_DISTRIBUTE →`
`AWAITING_PUBLICATION_APPROVAL → AWAITING_FOUNDER_PUBLICATION → POSTED →`
`OPEN → FILLING → FILLED → CLOSING → CLOSED`

Each candidate follows a longer human-decision-led path through these pivotal
states:

`APPLICATION_RECEIVED → CONSENT_AND_NOTICE_VALIDATION → EVIDENCE_INGESTION →`
`SCREEN_READY → AWAITING_SHORTLIST_DECISION → AWAITING_CONTACT_APPROVAL →`
`WAITING_FOR_CANDIDATE_REPLY → SCHEDULING → AWAITING_INVITE_APPROVAL →`
`WAITING_FOR_INTERVIEW → INTERVIEW_EVIDENCE_READY →`
`AWAITING_INTERVIEW_DECISION → AWAITING_REFERENCE_PERMISSION →`
`WAITING_FOR_REFERENCE → REFERENCE_EVIDENCE_READY →`
`AWAITING_FINAL_DECISION → AWAITING_OFFER_APPROVAL →`
`WAITING_FOR_OFFER_RESPONSE → OFFER_ACCEPTED | OFFER_DECLINED →`
`CLOSING → CLOSED`

At every human-decision stage, the Founder may also hold or decline; a candidate
may withdraw. None of those outcomes can be inferred from silence, a timer, a
failed message, or model output.

An accepted offer starts a separate onboarding lifecycle:

`ONBOARDING_INTAKE → AWAITING_PLAN_APPROVAL → PRE_START → FIRST_DAY →`
`FIRST_WEEK → AWAITING_COMPLETION_REVIEW → COMPLETE → CLOSED`

A Founder can ask Alex by text or live voice to start hiring. Alex clarifies the
need and prepares a complete role contract: purpose, outcomes, responsibilities,
qualifications, working model, compensation where supplied, candidate-facing
job description, scorecard, interview plan, and publication package. The
Founder reviews and approves the exact package before distribution.

Role-scoped intake receives applications and CVs, validates notice and consent,
deduplicates candidates, and quarantines unsafe content. Alex maps cited
candidate evidence to approved criteria as **present**, **missing**, or
**unclear**. It can prepare interview questions, correspondence, schedules,
reference summaries, offer documents, and onboarding plans.

Alex never scores, ranks, recommends, advances, rejects, or hires a candidate.
Only the Founder records shortlist, interview, final, and offer decisions.
Publication, candidate or reference contact, calendar invitations, offers, and
access requests each retain their exact approval and receipt boundaries.

A role- or candidate-aware **Discuss with Alex** action opens the main
conversation with the selected context visibly identified. Restricted evidence
never leaks into general conversation or Funding Operations.

### Funding Operations

Funding Operations takes a founder-selected opportunity through a durable,
guarded application workflow:

`INTERVIEWING → DRAFTING → AWAITING_REVIEW → APPROVED → FORM_FILLING →`
`AWAITING_SUBMIT_APPROVAL → SUBMITTED → FOLLOW_UP → CLOSED`

Alex can interpret sourced program requirements, lead a gap-filling interview,
draft responses from verified company facts, capture section-level feedback,
and prepare application artifacts. Its built-in Browser can inspect and
pre-fill supported portals, report partial success honestly, and stop when a
page no longer matches the version it inspected.

Filling and submission are separate. Alex cannot submit an application, send
email, book a meeting, or export externally without an exact, current,
single-use approval resolved by the server.

### One shared Alex experience

The interface keeps conversation central and places durable work in a
contextual workspace with **Work, Evidence, Decisions, and Activity**. Generated
documents remain canonical in Work and can be linked compactly from the exact
assistant turn that created them.

Real-time voice uses the same tools and safeguards as text. The live surface
includes captions, interruption handling, **Pause Alex** to stop microphone
capture, and a separate Hold state that keeps the call connected while
suppressing normal responses. An explicit local camera-sharing pilot is
available without ambient capture or frame persistence; screen sharing remains
disabled pending a separate rollout.

## How we built it

Co-Founder uses **Google ADK**, Vertex AI, FastAPI, Playwright, and Google Cloud.
A root Orchestrator coordinates eight bounded specialists across the shared
product and its operation packs:

- **Scout** for sourced discovery and extraction;
- **Matchmaker** for fit and deadline analysis;
- **Interviewer** for clarification and guided progress;
- **Drafter** for grounded writing;
- **Form Filler** for inspected browser preparation;
- **Hiring Operator** for role, publication, interview, offer, and onboarding
  preparation without decision authority;
- **Hiring Evidence Analyst** for identity-isolated, criterion-bound evidence
  mapping without scores or recommendations; and
- **Distiller** for turning bounded Founder feedback into reusable profile rules
  without receiving the full conversation.

We use a five-model stack, selecting each model for a specific workload:

- **Gemini 3.6 Flash** for orchestration, final drafting, dialogue, grounded
  discovery, matching, multimodal document understanding, browser reasoning,
  and tool-driven workflows;
- **Gemini 3.5 Flash-Lite** for high-volume extraction and classification;
- **Gemini Live 2.5 Flash** for real-time voice interaction;
- **gemini-embedding-001** for retrieving relevant canonical answers; and
- **Gemma 4 26B A4B IT** on Vertex MaaS for isolated second-pass evidence
  checking before Founder review.

We also use **Cloud Text-to-Speech with Chirp 3 HD** for explicit read-aloud
responses.

Durable workflow records—not chat history—authorize every transition. Cloud
SQL-compatible ADK sessions preserve conversation events, while Firestore stores
applications, roles, evidence, approvals, actions, and audit receipts. External
actions are idempotent and require server-resolved approvals bound to the exact
Founder, workspace, target, and current content.

The application is designed to run on Cloud Run with Cloud SQL, Firestore,
Cloud Storage, Secret Manager, Pub/Sub, Cloud Scheduler, Cloud Tasks, and Cloud
Build. More than 600 deterministic unit, integration, security, UI-contract,
and regression tests cover the implementation; credentialed ADK model
evaluations are an explicit gated CI run.

## Challenges we ran into

### Making two workflows feel like one product

Hiring and Funding both involve evidence, drafting, approvals, and long-running
work, but they cannot share data indiscriminately. Hiring contains restricted
candidate information and human-only decisions; Funding contains application
facts, program requirements, and portal actions. We created one Alex shell and
interaction language while keeping each workflow's records and permissions
scoped.

### Helping with hiring without making hiring decisions

An evidence summary can easily drift into a score or recommendation. We made
the distinction structural: Alex may report what candidate-provided evidence is
present, missing, or unclear, but only the Founder can advance or reject a
candidate. Draft approval also remains separate from publication or outreach.

### Making funding browser work reliable

Application portals rename fields, load controls dynamically, and sometimes
accept only part of a form. Co-Founder fingerprints inspected fields, checks the
page again before acting, reports exactly what succeeded, and stops rather than
guessing when the page changes.

### Making approval impossible to improvise

A prompt telling the model to “ask first” was not enough. Approvals had to be
server-owned, bound to the exact consequence, atomically claimed, and recorded.
Chat, voice, websites, documents, and incoming email are all evidence—not
authority.

### Keeping live voice continuous and durable

Real-time audio introduced races between microphone startup, socket closure,
model turns, captions, and transcript persistence. We made startup
cancellation-safe, serialized session revisions, and reconciled final turns
exactly once into chronological history.

## Accomplishments that we're proud of

- Built two coherent operational workflows rather than a conversational mockup.
- Made Hiring a dedicated founder workspace spanning role definition,
  publication, intake, evidence, interviews, references, offers, and onboarding
  while preserving Founder-only decisions.
- Built an end-to-end Funding workflow with grounded drafting, inspected browser
  work, partial-success reporting, approvals, and follow-up state.
- Preserved one conversation-first product language without leaking Hiring
  evidence into general or Funding contexts.
- Enforced consequential-action safeguards in code instead of prompts.
- Made live voice multi-turn, durable, accessible, pausable, and compatible with
  the same workflow authority model as text.
- Built generated-document, citation, approval, action, and audit records around
  stable identities rather than duplicated chat cards.
- Added extensive deterministic tests and checked-in ADK evaluation sets for
  grounding, safety gates, persona, artifacts, and long-running resume behavior.

## What we learned

**State is more reliable than conversation history.** A transcript can explain
what was discussed; it cannot prove what was approved, published, submitted, or
completed.

**Shared design does not require shared data.** Hiring and Funding can feel like
one product while retaining different evidence, privacy, and authorization
boundaries.

**Evidence is not judgment.** Organizing what is present, missing, or unclear is
useful assistance. Ranking or recommending candidates would cross a boundary
that belongs to the Founder.

**Guards belong in code.** A safe agent is not one that usually follows an
instruction. It is one that cannot perform a consequential action without the
right durable state and exact approval.

**Errors should be data.** Structured failures let Alex report partial progress
and ask for the next safe action instead of inventing success.

**Voice needs a privacy state machine, not only a microphone button.** Browser
permission, local capture, forwarding, model receipt, playback, pause, hold,
and transcript persistence are separate states.

## What's next for Co-Founder

Hiring and Funding are only the first two Co-Founder operations.

The same **Scout → Matchmaker → Guide → Drafter → Action → Approval →
Follow-up** foundation can support:

1. **Investor outreach operations** — finding relevant investors, preparing
   personalized outreach, tracking replies, and producing meeting briefs.
2. **Customer discovery** — sourcing interview participants, managing outreach,
   and synthesizing what customers actually said.
3. **Compliance and deadline monitoring** — tracking registrations, renewals,
   filings, and evidence of completion.
4. **Bookkeeping reconciliation** — matching receipts and invoices to
   transactions and preparing accountant-ready summaries.
5. **Vendor and partnership follow-through** — managing documents, approvals,
   signatures, deliveries, and long-running obligations.

Each new operation will reuse the common Alex experience and durable runtime,
but will receive its own evidence, policy, tool, and approval boundaries. A new
operation must never gain authority merely because another operation has it.

The boundary remains deliberate: strategy, pricing, hiring decisions, and
pivots belong to the human.

**Co-Founder does the work; the founder keeps the judgment.**

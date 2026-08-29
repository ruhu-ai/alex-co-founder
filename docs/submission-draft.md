## Inspiration

Every founder — solo or part of a founding team — knows the operational grind.
Nowhere is that grind more consequential than hiring. “We need to hire” quickly
becomes a role definition, job description, application channel, evidence
review, interview plan, references, offer, onboarding, and a trail of decisions
that must remain human.

Funding has the same failure mode. Opportunities are scattered across websites
and PDFs, every application asks familiar questions in a new form, and by the
fifteenth tailored version of “describe your traction,” the application often
quietly dies.

We did not want to build another chatbot that gives founders more advice. We
wanted a persistent co-founder that **carries the operational load**, leads the
workflow, and learns how the founders think while doing the work.

Funding was the first operation we built. Hiring is the second and our primary
demo because it proves the same foundation can support a more sensitive,
human-decision-led journey. They are the first two operations, not the limits of
Co-Founder: future workflows can reuse the same research, guidance, drafting,
action, approval, and follow-up foundation.

**Co-Founder does the work; the founder keeps the judgment.**

## What it does

Co-Founder is a conversation-first workspace built around two substantial
founder workflows: **Hiring Operations** and **Funding Operations**. Both share
the same Alex conversation, live voice, documents, contextual workspace,
decision controls, and activity receipts, while keeping their evidence and
authority boundaries separate.

### Hiring Operations — our primary demo

Hiring Operations carries a role from definition and approval through
publication, candidate intake, evidence review, founder-led interviews and
decisions, references, offers, and onboarding—with explicit approval before
every consequential action.

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

Funding Operations carries a founder-selected opportunity from guided
interviews and grounded drafting through review, guarded form filling, exact
submission approval, and follow-up.

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

Alex works from its own email, calendar, and Drive, while using
founder-connected sources only with permission.

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

Durable records—not chat history—authorize progress. ADK sessions preserve the
conversation; Firestore holds workflow truth and exact approvals. Every
external action is server-authorized, idempotent, and audited.

Co-Founder runs on Google Cloud using Cloud Run, Cloud SQL, Firestore, Cloud
Storage, Secret Manager, and event-driven services. More than 600 deterministic
tests and gated ADK evaluations cover workflow, security, and UI behavior.

## Challenges we ran into

**One product, separate truths.** Hiring and Funding share Alex, documents,
decisions, and activity, but not indiscriminate access. Candidate evidence stays
inside Hiring; application and portal state stays inside Funding.

**Assistance without stolen judgment.** Alex can organize candidate evidence,
but a summary can easily drift into a recommendation. We enforced the boundary
structurally: only the Founder can advance, reject, or hire.

**Reliable action beyond prompts.** Portals change, approvals become stale, and
live audio races with sockets and transcripts. We added page fingerprints,
server-bound approvals, idempotent actions, cancellation-safe media, and
exactly-once transcript reconciliation instead of asking the model to be
careful.

## Accomplishments that we're proud of

- Built two end-to-end operations rather than a conversational mockup.
- Took Hiring from role definition through intake, interviews, references,
  offers, and onboarding while preserving Founder-only decisions.
- Took Funding from guided preparation through guarded browser work, submission,
  and follow-up.
- Unified both in one conversation-first, live-voice product without crossing
  their evidence boundaries.
- Enforced consequential actions in code and backed the system with durable
  records, extensive tests, and gated evaluations.

## What we learned

**State and guards beat conversation.** A transcript explains what was said; it
cannot prove what was approved or completed. Consequential actions need durable
state and code-owned gates.

**Evidence is not judgment.** Alex can organize what is present, missing, or
unclear. Ranking candidates or making decisions belongs to the Founder.

**One experience can preserve separate boundaries.** Hiring and Funding can
share Alex without sharing every record, permission, or source.

**Failures should be visible state.** Structured errors and explicit voice
lifecycles let Alex report partial progress without inventing success.

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

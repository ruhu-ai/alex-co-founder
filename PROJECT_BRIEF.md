# Project Brief — "Co-Founder" (working title)

**Hackathon:** All Things Agentic Hackathon (Devpost), submission window Aug 3–31, 2026
**Category:** Collaborative Partner
**Category brief (verbatim):** "Build an agent that leads the way and takes notes. It should ask clarifying questions, guide the user step-by-step, and have a clear way to capture feedback, so it constantly adapts to the user's unique way of thinking."

---

## 1. One-sentence concept

An AI co-founder for solo founders that **does real work** — discovering grants/incubators/programs, tracking deadlines, drafting applications in the founder's own voice, and pre-filling real application forms — while continuously **learning how the founder thinks** through clarifying questions, step-by-step guidance, and explicit feedback capture.

The tagline distinction: not a chatbot that gives advice — a partner that carries the load. The founder reviews and approves; the agent does everything else.

## 2. Problem (the "why")

Solo founders applying to grants, accelerators, and incubation programs face a messy, multi-step chore:

- Discovery is fragmented: programs are scattered across portals, newsletters, and PDFs with buried eligibility criteria.
- Every application re-asks the same ~30 questions (problem, solution, traction, team, market) in slightly different words.
- Deadlines are missed because tracking is manual.
- Drafting the 15th tailored version of "describe your traction" is where applications die.

The founder's real pain: repetitive re-entry, lost deadlines, and hours of drafting — a personal, authentic "bring your own friction" story.

## 3. What the system is (and is not)

**Is:** a persistent business partner with durable memory of the company (facts, voice, past answers, preferences, rejection history) that runs an end-to-end application pipeline: **discover → match → guide → draft → pre-fill form → human approve → submit → follow up**.

**Is not:** a general-purpose business-advice chatbot, a grant database, or an autonomous submitter. A human approval gate always precedes any real submission. The "general co-founder" framing lives in the pitch narrative; the application pipeline is the concrete proof.

## 4. How it satisfies the Collaborative Partner rubric (design these in deliberately)

| Rubric requirement | How the system implements it |
|---|---|
| **Leads the way** | The agent drives a structured workflow: it proposes the next step, prepares materials, and tells the founder exactly what it needs from them and why. It never waits passively for instructions. |
| **Asks clarifying questions** | Before drafting any application, the agent interviews the founder about gaps (e.g., "This program asks for a sustainability plan — do you have one, or should I draft one from your impact metrics?"). On program discovery it asks fit questions ("They require revenue under $50k ARR — where are you?"). |
| **Guides step-by-step** | Applications are decomposed into an explicit checklist (eligibility → narrative sections → attachments → form fields → review → submit). The UI shows progress; the agent walks the founder through one step at a time. |
| **Captures feedback** | Every draft section has approve / edit / reject-with-reason controls. Edits and rejection reasons are stored verbatim, not just ratings. |
| **Adapts to the user's thinking** | Feedback is distilled into a persistent **Founder Profile** (voice, style preferences, canonical answers, factual knowledge, decision patterns). Every later draft demonstrably reflects earlier feedback — e.g., a section drafted in week 2 incorporates an edit the founder made in week 1, and the agent says so. |
| **Actively synthesizes/mutates data** (Stage Two judging criterion) | The system never just reads and repeats: the Distiller mutates raw feedback into structured profile rules; the Drafter mutates canonical answers into program-specific narratives; the Matchmaker synthesizes urgency assessments from deadline + workload signals. |
| **Ingests messy unstructured data** (Stage Two judging criterion) | The Scout's raw inputs are genuinely messy: program web pages, PDF guideline documents, newsletter emails, and portal forms — each parsed into structured `Opportunity` records with source citations. Emphasize this in the write-up; it is a named judging criterion. |

**Demo-critical:** the adaptation must be *visible*. The demo must include a moment where the agent explicitly uses past feedback ("Last time you rejected the word 'revolutionary' — this draft avoids it").

## 5. Functional scope (v1 — build exactly this, nothing more)

### Pipeline stages (state machine)

`DISCOVERED → EVALUATED → SHORTLISTED → INTERVIEWING → DRAFTING → AWAITING_REVIEW → APPROVED → FORM_FILLING → AWAITING_SUBMIT_APPROVAL → SUBMITTED → FOLLOW_UP → CLOSED`

1. **Discovery (founder-invoked, asynchronous):** when requested from the UI or chat, the agent durably dispatches a sweep of configured and search sources for grants/accelerators/incubators, extracts structured records (name, award, eligibility, deadline, application URL, required materials), deduplicates, and stores them. Discovery has no polling loop or daily schedule.
2. **Matching:** each program is scored against the Founder Profile (stage, sector, geography, award size). Poor fits are auto-archived *with a logged reason*; good fits are surfaced with a short rationale and a deadline-urgency assessment ("closes in 9 days, needs 2 essays — start now").
3. **Guided interview:** before drafting, the agent asks clarifying questions to fill gaps between what the program demands and what the profile knows. Answers are stored into the profile.
4. **Drafting:** the agent drafts application answers grounded in the profile, in the founder's voice, tailored to the program's thesis. Each section is presented individually for review.
5. **Feedback capture:** approve / edit / reject-with-reason per section. Distilled feedback updates the Founder Profile (memory write).
6. **Form pre-filling:** a browser-automation tool navigates the target portal and fills the application form from approved answers. It must handle partial success gracefully ("filled 14/16 fields; 2 need you") and must re-verify the page state before acting (staleness guard).
7. **Approval gate:** the agent pauses and waits for explicit founder approval before any submission. (This is a feature — narrate it as "your co-founder never spends your reputation without asking.")
8. **Submission & follow-up:** after approval, submit; then track follow-up obligations (confirmation emails, interview invitations, result announcements) on a schedule, and report status.

### Explicitly out of scope for v1

- General business advice (pricing, hiring, strategy chat) — mention only as future scope in the write-up.
- "Works on any website" browser automation — target **1–2 real application portals** plus **one self-hosted mock portal** (deployed on Cloud Run) as a demo reliability fallback.
- Payment, taxes, legal, or any other workflow beyond applications.

## 6. Architecture

### Required stack (mandatory per rules)

- **Gemini 3.5+** via Gemini API or Vertex AI — all reasoning, extraction, drafting, distillation.
- **Google ADK** — agent framework (multi-agent orchestration, tools, sessions, state).
- **Google Cloud infra (minimum one; we use several):** Cloud Run (hosting + mock portal), Firestore (pipeline store), Pub/Sub + Cloud Scheduler (wake-up triggers), Cloud Storage (artifacts: decks, documents), Secret Manager (portal credentials).

### Workflow engine (design for the future, build one instance)

The system is architected as a **general workflow engine**, not an application tracker. A "workflow" is a declarative definition (sources, entity schema, state machine, drafting templates, action tools, approval policy) plugged into shared infrastructure. The application-pipeline is workflow instance #1; future workflows (Section 6.5) are new definitions, not rewrites.

Concrete rules for the implementation:

- **Generic core naming:** `Opportunity`, `PipelineStore`, `OutreachDraft`, `ChecklistItem`, `ApprovalGate` — never `GrantTracker`-style domain names in core modules. Domain specifics live in workflow config, not core code.
- **Shared sub-agents** (below) are workflow-agnostic: the Scout doesn't know what a "grant" is; it extracts whatever entity schema the active workflow defines.
- **Workflow-specific behavior** (portal adapters, program schemas, email senders) lives behind small adapter interfaces, so adding a workflow = adding an adapter + a config.

### Agents (separation of concerns — judges check for this)

1. **Orchestrator (root agent):** owns the state machine, routes to sub-agents, manages pause/resume. Reads current step from session state, never from chat history.
2. **Scout agent:** discovery + structured extraction of program records.
3. **Matchmaker agent:** fit scoring and deadline-urgency reasoning against the Founder Profile.
4. **Interviewer/Guide agent:** clarifying questions, step-by-step checklist navigation, feedback collection.
5. **Drafter agent:** section drafting grounded in the Founder Profile; voice consistency.
6. **Form-Filler agent:** browser automation tool wrapper (e.g., Playwright-based) for portal navigation and form pre-fill; includes staleness checks and idempotency keys so a form is never double-submitted.
7. **Memory/Distiller agent:** converts raw feedback (edits, rejections, interview answers) into structured Founder Profile updates.

### State, memory, durability (core of the architecture score)

- **Explicit state schema** in session state (`current_step`, `active_program`, `checklist_status`, `pending_signals`) — the agent is grounded in a state machine, not conversation replay. State variables are injected into the system instruction (`{current_step}` etc.) so the model always sees exact workflow position. State is initialized in a `before_agent_callback`.
- **Persistent sessions:** ADK DatabaseSessionService backed by Cloud SQL (or SQLite locally) so the agent survives restarts and can pause for days waiting on the founder or a deadline.
- **Founder Profile (long-term memory):** structured document(s) in Firestore + Memory Bank: canonical facts, approved answer library, style/voice rules, rejection history with reasons. This is the "constantly adapts" evidence.
- **Event-driven dormancy + resume:** instead of polling, the agent sleeps (container may scale to zero) and is woken by external events — Pub/Sub messages (deadline tick) and **FastAPI webhook endpoints** (founder reply, portal confirmation, mock-portal events). Discovery starts only from a founder action. On wake, a resume handler hydrates the persisted session and calls `runner.run_async(..., state_delta={...})` so the state transition is applied atomically *before* the next inference call — no replayed chat history, no hallucinated intermediate steps.
- **Idempotency + audit log:** every external action (form fill, submission, email) carries an idempotency key and writes an audit record (who/what/when/result).

### Evaluation (judges reward this; most entrants skip it)

- **Golden eval sets** with `adk eval`: simulate multi-day idle time and wake-up triggers in seconds by pre-seeding session state (e.g., pre-seed `SHORTLISTED`, simulate a deadline event, assert the agent resumes with context intact).
- **Safety-gate tests:** assert the agent *refuses* to skip gates — e.g., asked to submit before approval, it must call no tools and stay in `AWAITING_SUBMIT_APPROVAL`; asked to draft before the interview, it stays in `INTERVIEWING`.
- Evals run in CI; include the eval configs in the repo (evidence of failure-tolerance rigor for the 30% architecture criterion).

### Deployment & observability

- Primary: **Cloud Run** (FastAPI app via ADK's `get_fast_api_app`) with scale-to-zero during dormancy; mock portal on a second Cloud Run service.
- Optional/stronger: deploy via **Agent Runtime (Vertex AI Agent Engine)** using the `AdkApp` wrapper and `agents-cli deploy` — session persistence, auto-scaling, and Cloud Trace built in. Pick one per environment; note the choice in the README.
- **Cloud Trace / structured logs** for end-to-end reasoning chains — show these in the video as production-readiness proof.

### Security (must be able to explain this to judges)

- Portal credentials live only in **Secret Manager**; tools fetch them at execution time; they never appear in prompts, session state, or logs.
- Tools are scoped per capability (the drafter cannot submit; the form-filler cannot submit without an approval token from the gate).
- Every submission requires a persisted human approval record.
- PII minimization in logs; audit trail for all actions.

## 6.5. Future workflows (roadmap — design supports these, do NOT build in v1)

The same engine (shared sub-agents + Founder Profile + generic pipeline store) is designed to absorb these workflows as new declarative definitions. Listed in pitch/write-up as the growth path; only the application pipeline is built for the hackathon.

1. **Investor outreach ops** — Scout finds fitting investors (public lists/CSVs), Matchmaker scores fit against the business profile, Drafter writes personalized outreach in the founder's voice, follow-up tracking on silence, pre-meeting briefings. Closest sibling to the application pipeline; the natural workflow #2. (Conditional stretch goal: only if the application pipeline is fully working end-to-end with 5+ days before deadline, add a thin version — tracked pipeline + drafted outreach, no sending — as a 20-second "same engine, different domain" proof beat in the video.)
2. **Customer discovery & outreach** — lead sourcing, ICP-fit matching, outreach drafting, follow-up; plus inbound feedback triage and weekly synthesis ("what customers actually said" record). Highest commercial value; deferred because live email sending adds deliverability/ToS complexity and "AI SDR" demos are less differentiated.
3. **Deadline & compliance sentinel** — tax filings, registrations, renewals; pure monitoring + escalation, almost no drafting.
4. **Hiring pipeline** — screens inbound candidates against a role profile, drafts outreach, schedules, chases references.
5. **Bookkeeping reconciliation** — matches receipts/invoices to transactions, flags anomalies, prepares accountant-ready monthly summaries.
6. **Vendor/partner follow-through** — long dormancy-and-resume work: procurement replies, signatures, delivery confirmations.

Explicit boundary: strategy, pricing, pivot decisions stay with the human — "the agent does the work; the founder keeps the judgment." Advice-shaped features are out of the roadmap by design.

## 7. Demo plan (4-minute video — judges weight this 30%)

Script beats:

1. **Problem (20s):** authentic founder pain — missed deadlines, re-typing the same answers into 6 portals.
2. **Discovery → shortlist (40s):** show the pipeline board populated by the Scout; one program auto-archived with a reason; one flagged "urgent."
3. **Guidance + clarifying questions (40s):** the Interviewer asks a gap question; founder answers; checklist advances.
4. **Drafting + feedback + adaptation (60s):** draft a section; founder edits/rejects with a reason; **show the next draft visibly incorporating past feedback** with the agent citing it. This is the Collaborative Partner money shot.
5. **Action proof (60s):** the Form-Filler navigates a real portal live and pre-fills the form; approval gate; submission; confirmation. Unedited, with terminal logs / Firestore updates visible.
6. **Cloud proof (20s):** Cloud Run dashboard, Vertex AI logs, `.run.app` URL on screen.
7. **Roadmap (10s, trim beats 2–3 slightly to fit):** one slide showing the future workflows from Section 6.5 — "same engine, new definitions" — closing on "the agent does the work; the founder keeps the judgment."

Fallback: if the live portal breaks on recording day, use the self-hosted mock portal (same flow, same code path).

## 8. Submission checklist (from the rules)

- Public/private repo with **spin-up README** (reproducible setup); if private, grant access to testing@devpost.com and cloudhackathons@google.com.
- **Architecture diagram** (Gemini ↔ ADK agents ↔ Firestore/Cloud SQL ↔ Pub/Sub/Scheduler ↔ browser tool ↔ mock portal).
- ≤4-min video on YouTube/Vimeo (publicly visible), English, showing Google Cloud deployment. No third-party logos/ads in any submission material.
- Text description: features, technologies, data sources, findings/learnings.
- Hosted URL strongly encouraged; **the project must remain available, free of charge, for judge testing until the Judging Period ends (~Oct 1, 2026)** — plan Cloud costs accordingly (scale-to-zero config).
- **Rules housekeeping:** the repo/project must be newly created within the Aug 3–31 submission window; disclose any pre-existing code incorporated; the app must support English; multiple submissions are allowed if substantially different.
- Bonus (cheap wins): public build-log post, social post with #AllThingsAgenticHackathon, +0.2 per extra Google model integrated (e.g., Gemma for a lightweight task) up to +0.6.
- Optional prize angle: **Best Multimodal UX** — consider founder voice-note feedback (Gemini audio understanding) or portal-screenshot parsing as a cheap multimodal feature; only add if the core is done.

## 9. Success criteria (judge-oriented)

1. The agent **leads**: in the demo it initiates, questions, and advances the workflow — the founder only answers and approves.
2. Adaptation is **provable on screen**: a concrete before/after where feedback changed later behavior.
3. At least one **unedited live action** on a real or self-hosted portal.
4. The system survives a restart mid-workflow and resumes correctly (show this in logs if possible), and resumes from webhooks with `state_delta` transitions.
5. **Golden evals pass in CI** — idle-time simulation and safety-gate refusals are green; eval configs are in the repo.
6. Architecture answers ready: state management, tool scoping, failure recovery (e.g., what happens when the form-filler hits a changed page — it degrades gracefully and flags fields, it never guesses).

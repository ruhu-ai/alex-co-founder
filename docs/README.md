# Co-Founder — Implementation Specifications

**Hackathon:** All Things Agentic Hackathon (deadline Aug 31, 2026, 5:00 PM PDT)
**Category:** Collaborative Partner
**Source brief:** `../PROJECT_BRIEF.md` (read it first for the "why"; these docs are the "how")

An AI co-founder for solo founders that runs an end-to-end application pipeline —
**discover → match → interview → draft → review → pre-fill form → human approve → submit → follow up** —
for grants, accelerators, and incubation programs, while continuously learning the
founder's voice and thinking through explicit feedback capture.

---

## How to use these specs (vibe coding workflow)

1. Read `../PROJECT_BRIEF.md`, then this file, then `01-architecture.md` fully.
2. Implement in the order given in `14-build-plan.md`. Each spec doc is self-contained;
   hand it to the coding agent as the context for that work item, e.g.:
   *"Implement docs/03-state-machine.md exactly. Conventions are in docs/01-architecture.md §Conventions."*
3. Do not reorder. Later docs assume earlier ones are built.
4. Every doc ends with **Acceptance checks** — run them before moving on.
5. When a spec and the ADK installed version disagree on an import path, the installed
   package wins. Check with `python -c "import google.adk; print(google.adk.__version__)"`
   and adjust imports, not behavior.

## Doc index

| Doc | Contents | Build phase |
|---|---|---|
| `01-architecture.md` | System design, repo layout, tech pins, env vars, workflow engine | Day 1 |
| `02-data-model.md` | Firestore collections, session state keys, artifacts | Day 1–2 |
| `03-state-machine.md` | Lifecycles, transitions, guards, dormancy/resume contract | Day 2 |
| `04-agents.md` | All 7 agents: instructions, tools, wiring | Day 2–4 |
| `05-tools.md` | Every tool function: signature, behavior, errors, idempotency | Day 3–7 |
| `06-memory-profile.md` | Memory tiers, Founder Profile, Distiller, retrieval | Day 3–4 |
| `07-server-webhooks.md` | FastAPI app, Runner wiring, webhook endpoints, resume handler | Day 8 |
| `08-discovery-pipeline.md` | Scout sweep, source adapters, deadline sentinel, Scheduler/Pub/Sub | Day 5, 8 |
| `09-form-filler.md` | Browser automation, mock portal, approval gate, staleness guards | Day 7 |
| `10-ui.md` | Demo web UI: pipeline board, chat, review controls, approval modal | Day 6, 11 |
| `11-evaluations.md` | Golden eval sets, safety-gate tests, adaptation integration test, CI | Day 9 |
| `12-security.md` | Secrets, tool scoping, approval tokens, audit, PII | Throughout |
| `13-deployment.md` | Local setup, Cloud Run, Cloud SQL, Scheduler/Pub/Sub, cost control | Day 10 |
| `14-build-plan.md` | 13-day plan, cut list, final acceptance criteria | Daily |
| `adr/` | Decision records (001: agent identity — persona Alex, role mailbox, no social; 002: meeting presence — notes ladder, Calendar scheduling) | As decisions are made |
| `15-document-outputs.md` | Document production: docx/xlsx/pptx builders, validation gate, downloads, Drive sync | Post-core |
| `16-design-system.md` | Design tokens, icon sprite, contrast checks | Post-core |
| `17-real-portal-access.md` | Portal registration + sign-in, mailbox verification loop, audited account creation | Post-core |
| `18-browser-agent.md` | General-purpose browsing (`browse.*` tools), Browser panel live view | Post-core |
| `19-winning-plan.md` | Gap analysis vs prior winner, prioritized Google-framework coverage, timeline to Aug 31 | Post-core |
| `20-gemma-evidence-checker.md` | Isolated semantic evidence check before founder review; Gemma, validation, rollout eval | Post-core |

## Design principles (binding on all implementation)

1. **The state machine grounds everything.** The agent reads workflow position from
   session state (`{current_step}` injected into instructions), never from chat history.
2. **Tools return errors as data.** A tool never raises to the model. It returns
   `{"error": true, "message": "..."}` so the model can explain and recover.
3. **Dormancy by default.** No polling loops, no blocked threads. The agent sleeps;
   external events (Scheduler → Pub/Sub, webhooks) wake it via `state_delta` resumption.
4. **Every external action is idempotent and audited.** Idempotency key + audit record
   for every form fill, submission, and message.
5. **Autonomous by default; human approval precedes every irreversible action.**
   The agent researches, scores, ingests, drafts, and fills without asking
   permission, escalating only when blocked (conflict, missing fact, ambiguity).
   Submission requires a persisted, single-use approval token. This is a
   feature, not a limitation.
6. **Docstrings are load-bearing.** ADK builds the tool schema the model sees from the
   function signature + docstring. Every argument needs an `Args:` entry.
7. **Guards live in code, not prompts.** Anything that must happen *every* time —
   staleness re-checks before acting, approval enforcement, idempotency — is a
   `before_tool_callback`, a tool-internal check, or a function node, never an
   instruction. "Ask for it in the prompt and the model complies most of the time.
   Ask for it in code and it complies every time." (ADK 2 reference lab, Task 7.)
8. **Isolated workers see nothing.** Sub-agents that do focused transformation work
   (distiller) run with `include_contents="none"` — a short prompt is cheaper,
   faster, and cannot be derailed by something said twenty turns ago.

## Mandatory stack (from hackathon rules — non-negotiable)

- Gemini 3.5+ via Gemini API or Vertex AI
- Google ADK (agent framework)
- ≥1 Google Cloud infrastructure service — we use Cloud Run, Firestore, Pub/Sub,
  Cloud Scheduler, Cloud Storage, Secret Manager, Cloud SQL

## Repo name

`co-founder` — the code lives at the repository root; these specs live in `docs/`.

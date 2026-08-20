# Devpost submission draft (edit before posting — Day 13)

## Tagline

Co-Founder: the AI co-founder that does the work — finding programs, drafting
applications in your voice, pre-filling real forms — and never spends your
reputation without asking.

## The problem

Every grant, accelerator, and incubator application re-asks the same thirty
questions in slightly different words. Solo founders lose weeks to repetitive
re-entry, miss deadlines buried in PDFs, and watch good programs close
unanswered. General-purpose agents can fill a form once — but they don't know
your company, don't learn how you write, and can't be trusted with an
irreversible submission.

## What it does

Co-Founder runs the whole application pipeline as a long-running, durable
workflow:

- **Discovers** programs autonomously — Google Search grounding, program
  listing pages, guidelines PDFs — parsed into structured records with
  citations, on a daily schedule.
- **Matches** each program against a persistent Founder Profile (stage, sector,
  geography, funding preferences), archiving poor fits with specific reasons
  and flagging urgency ("closes in 9 days, needs 2 essays — start now").
- **Learns the company from its own documents**: upload a deck or business plan
  and it proposes profile facts for your confirmation — 4 interview questions
  instead of 30.
- **Interviews and guides** step-by-step, asking one clarifying question at a
  time and saying why.
- **Drafts in your voice** — and adapts visibly: reject a word once, and every
  later draft avoids it *and cites your feedback* ("avoided 'revolutionary' —
  your feedback of Aug 20").
- **Acts**: navigates real application portals with a browser — DOM-first with
  vision reconnaissance for portals it has never seen — pre-fills from approved
  answers, reports partial fills honestly, and submits only behind a
  founder-granted, single-use approval that the model never sees.

## How it's built (technologies)

Gemini 3.5 (Vertex AI) · Google ADK (multi-agent orchestration, durable
sessions, eval framework) · Cloud Run · Firestore · Cloud SQL · Pub/Sub ·
Cloud Scheduler · Secret Manager · Cloud Storage · Playwright.

Architecture highlights: an explicit state machine grounds every agent
(`current_step` injected, never chat replay); three Runner surfaces share one
session store and resume parked workflows via `state_delta`; the feedback
distiller runs isolated (`include_contents="none"`) and synchronously, so
adaptation is provable on camera; every external action is idempotent
(derived keys) and audited (append-only trail); safety gates — no drafting
before the interview, no submission without approval — are enforced in code
and covered by golden eval sets in CI.

## Data sources

Public program pages and guideline PDFs (fetched live, snapshot-cited), Google
Search grounding, a self-hosted mock application portal for the demo, and the
founder's own uploaded documents.

## Findings & learnings

- Prompts can't wake themselves: autonomy needs external triggers (Scheduler →
  Pub/Sub → `state_delta` resume), not instructions.
- Guards belong in code: a `before_tool_callback` staleness fence complies
  every time; an instruction complies most of the time.
- The model must never see the approval token — it resolves server-side, so
  there's nothing to leak or fabricate.
- "Store the sentence, not the schema": verbatim founder reasons beat invented
  fields every time.
- Multi-day agents are tested by seeding state, not by waiting.

## Differentiation

General agents can fill a form once. Co-Founder learns how you write across
every application, discovers what each portal demands on its own, and never
spends your reputation without a signed gate.

## Links

- Repo: (public URL) · Hosted demo: (.run.app URL) · Video: (YouTube URL)
- Built for the All Things Agentic Hackathon (Collaborative Partner).

# 19 — Winning Plan: improvements + Google-framework coverage

Deadline: **Aug 31, 2026, 5:00 PM PDT** (11 days out at writing).
Judging: **Innovation & Operational Utility 40% · Architectural Discipline
30% · Demo & Production Readiness 30%** + bonus (extra Google models, build
log, social post).

Benchmark: VitaCare (prior winner) — crisp one-line narrative, A2A protocol,
5+ Google models, clickable demo scenarios, live URL, consent-at-protocol
safety. We match them on substance (state machine, approval gates, real
browser automation, evals, idempotency, audit) and trail on **narrative
packaging, A2A, embeddings, model breadth**.

## Gap inventory (honest)

| Area | VitaCare | Us today |
|---|---|---|
| Narrative | "Agent-to-agent care network" | "Does the work; founder keeps judgment" — good, less viral |
| A2A protocol | Core to their story | Absent |
| Google models | 2.5 Pro, Flash, Live, Embedding-001, Cloud TTS | gemini-3.5-flash, gemini-live-2.5-flash |
| Embeddings/RAG | gemini-embedding-001, cosine | deterministic tag retrieval |
| Agent runtime | Cloud Run | Cloud Run (planned) |
| GEAP | — | Memory/Identity/Observability custom-built |
| Demo format | Clickable scenarios | Linear script |
| Safety story | Consent grid, never-diagnoses | **Stronger**: server-resolved approval gates, extraction-only rule, evals |

## P0 — ship-readiness (nothing else matters until these are done)

1. **Cloud Run deploy** (docs/13): app + mock portal, secrets in Secret
   Manager, `--min-instances 1` for the demo window, `/healthz` warm, Cloud
   SQL sessions, Scheduler → `/tasks/discover` + `/tasks/deadline_scan` daily.
   *Criteria: Production Readiness.*
2. **Vertex Agent Engine deploy** (hybrid, `adk deploy agent_engine`): the
   agent package deploys standalone → Agent Registry listing + managed
   runtime + console observability. Cloud Run keeps the app. **Hard 45-minute
   timebox** — IAM/manifest issues swallow days; on any GCP deploy error, cut
   immediately. Cloud Run alone satisfies 100% of the infra requirements;
   the write-up then notes GEAP as roadmap.
   *Criteria: Fortified boxes (Registry, Runtime), Architectural Discipline.*
3. **Real data run**: real pitch deck → profile → live sweep → real program
   → full loop to produced documents. This becomes the demo's backbone and
   the write-up's proof. *Criteria: Operational Utility.*
4. **OAuth connect**: founder Google + alex@ruhu.ai mailbox → real Drive
   ingestion + live FOLLOW_UP signals. **Status (Aug 20): ALL FOUR connected
   locally — Gmail ✅, Alex's Mailbox ✅, Drive ✅, Calendar ✅.**

## P1 — Google-framework coverage (the four gaps)

5. **A2A — the headline addition, REAL protocol only.** The mock portal
   exposes an A2A agent card (`/.well-known/agent.json`) and handles
   `message/send` via `a2a-sdk`: Alex *negotiates* with the program's own
   agent — asks requirements, clarifies fields, receives deadline extensions,
   confirms receipt. Demo beat: "watch our agent talk to THEIR agent."
   **Never a custom lookalike endpoint** — a judge who knows A2A spots a fake
   instantly; the protocol is JSON-RPC over HTTP, so a minimal real
   implementation costs the same as a fake one. 1-day timebox; if the SDK
   fights back, cut entirely rather than ship a lookalike.
   *Criteria: Innovation headline, Google-tech breadth.*
6. **Vertex text-embeddings** (`gemini-embedding-001`) for canonical-answer
   retrieval — replaces tag retrieval in `get_relevant_answers` (docs/06
   already specs this as the stretch). **Fallback guard:** the deterministic
   tag path stays PRIMARY in pytest (offline, instant, no API key) — the
   embedding fn is injected like every other model backend.
   **Status (Aug 20): BUILT — live-verified 3072-dim on the project.**
   *Criteria: model breadth, Collaborative-Partner RAG evidence.*
7. **Deliberate multi-model assignment** (per the brief's own cost tips —
   "Flash first, Pro for complex final reasoning"):
   - Orchestrator + drafter (final reasoning): **Gemini Pro-class**
   - Interview, matchmaker, scout chat: **Flash** (current)
   - Extraction/classification (doc_extract, gmail classify upgrade):
     **Flash-Lite or Gemma** — cheap, and it's another model on the list
   - Voice: **gemini-live-2.5-flash** (existing)
   Result: 4–5 models with a written rationale per assignment.
   *Criteria: bonus model count, cost-discipline narrative.*
   **Status (Aug 20): BUILT — 5 tiers live-verified: 2.5-pro (orchestrator +
   drafter), 3.5-flash (dialogue), 3.5-flash-lite (extraction),
   live-2.5-flash (voice), embedding-001 (retrieval).**
8. **Cloud TTS (Chirp HD)** for voice-note playback / assistant replies in
   the UI — VitaCare lists it; ours would match. Optional if Live voices
   suffice; cheap to add via the TTS API for the async voice-note path.
   **Status (Aug 20): BUILT — `POST /api/tts` (Chirp-3-HD-Charon) + hover
   read-aloud button on agent messages; API enabled via Service Usage.**

## P2 — demo & evidence packaging

0. **Safety headline: "Zero Unauthorized Submissions."** Our counter to
   VitaCare's consent grid, with teeth: G1/G3 guards in code, single-use
   server-resolved approval tokens, audited refusals, evals proving them.
   Headline on Devpost + a named beat in the video (submit attempted without
   approval → refused, on camera, twice, idempotent).
9. **Scenario buttons** on the UI (VitaCare's format): pre-staged,
   judge-clickable — "Deck → profile in 30s" · "Feedback → adaptation loop" ·
   "A2A negotiation" · "Mock portal pre-fill & gate" · "Doc produced live".
   Each seeds state and runs one beat, powered by `scripts/seed_demo.py
   --scenarios` (EXTEND the existing seed script — no duplicate seeders).
   *Criteria: Demo Readiness.*
9b. **UI design-system compliance pass** (docs/16): unify iconography on the
   Phosphor sprite (`scripts/build_icons.py`), glassmorphic card treatment,
   and `scripts/check_contrast.py` must pass in both themes. First
   impressions happen in 3 seconds.
10. **Architecture diagram refresh**: include A2A link, Agent Engine,
    connectors, approval-gate callouts. Keep mermaid (docs/architecture-diagram.md).
11. **README spin-up proof**: fresh clone → setup.sh → running ≤ 15 min,
    timed and recorded in docs/verification-notes.md.
12. **Observability narrative**: audit trail + eval history + 107 tests +
    6 eval sets surfaced in README + submission text (our strongest
    Architectural-Discipline evidence).
13. **Submission assets**: ≤4-min video (Cloud proof: Run dashboard + Agent
    Engine console + Vertex logs), Devpost text from docs/submission-draft.md,
    build-log post, social post (#AllThingsAgenticHackathon). *Bonus points.*

## Explicitly NOT doing (scope guard)

- Memory Bank migration (our profile store is more demonstrable)
- Agent Gateway, Genkit, Antigravity (no use case)
- LinkedIn/social agents (ADR-001: never)
- More workflow definitions than grants (brief §5)
- Reworking the voice pipeline (it works)

## Timeline

| Days | Work |
|---|---|
| Aug 20–21 | P0.1 Cloud Run deploy + 45-min Agent Engine timebox + P0.3 real data run |
| Aug 22–23 | P0.4 OAuth + P1.6 embeddings (fallback-guarded) + P1.7 model assignments |
| Aug 24–25 | P1.5 real A2A (1-day box) + P2.9 scenario buttons |
| Aug 26–27 | P2.9b design-system pass + diagram + seed script `--scenarios` mode |
| Aug 28 | Demo rehearsal ×2, timed ≤ 4 min; buffer |
| Aug 29 | Video + Devpost draft |
| Aug 30 | Submit + build-log + social; 24 h buffer |

Every P1 item is independently cuttable in this order: TTS → embeddings →
multi-model → A2A. P0 is never cut.

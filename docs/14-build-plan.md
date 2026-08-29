# 14 — Build Plan (13 days)

Today: **Aug 18, 2026**. Deadline: **Aug 31, 2026, 5:00 PM PDT**. One person +
vibe coding tool. The plan front-loads risk (form-filler by Day 7, cloud by Day
10) and leaves Days 11–13 for demo, docs, and buffer. Work from the spec docs in
order; do not gold-plate.

## Daily plan

| Day | Date | Build | Spec | Exit criteria (must be true before moving on) |
|---|---|---|---|---|
| 1 | Aug 18 | Repo scaffold, setup.sh, env, Firestore client, workflow loader, `hello` root agent on `adk web`; **name real discovery sources in YAML + snapshot fixtures to `tests/fixtures/`**; **15-min spike: one grounded `GoogleSearchTool` query on Vertex + gemini-3.5-flash** (fallback: plain search HTTP API behind the same `search_programs` signature) | 01, 02, 08 | Acceptance checks of 01 pass; `load_workflow` works; fixtures replay; search-spike result recorded in repo (which backend works, or fallback engaged) |
| 2 | Aug 19 | **First hour: ADK 2.x verify pass** — `inspect.signature(Runner.run_async)` (`state_delta` kwarg), instruction templating of prefixed keys, `App`/`EventsCompactionConfig` import paths. Then state schema, callbacks, transition helper, persistent sessions, orchestrator routing. **Skeleton deploy:** hello-world Cloud Run + Cloud SQL connection | 02, 03, 04, 13 | Verify notes written to repo; kill/restart mid-chat resumes; `can_transition` unit tests pass; hello service reaches Cloud SQL (deploy de-risked, not left to Day 10) |
| 3 | Aug 20 | Profile store, interviewer agent + record_answer + **doc ingestion (upload adapter)** | 05, 06 | Interview Q→A lands in profile; `user:profile_id` survives new session; one uploaded doc → proposals → confirm/reject → profile/feedback writes |
| 4 | Aug 21 | Drafter + feedback capture + **synchronous distiller** + adaptation loop | 04, 05, 06 | 06 §adaptation checklist green (rule cited in next draft's notes); distill completes inline before resume — no race |
| 5 | Aug 22 | Scout + discovery lanes (search, configured, crawl) + task endpoint | 05, 08 | `POST /tasks/discover` shortlists a fit, archives a non-fit with reason, and the search lane finds ≥1 program outside configured sources |
| 6 | Aug 23 | Matchmaker polish + UI v1: board + chat + review controls | 04, 10 | Full review loop (approve/edit/reject) works in UI |
| 7 | Aug 24 | **Mock portal + Tier 0 DOM fill + approval gate + idempotency** | 09, 12 | End-to-end vs mock portal twice (idempotent), token refusals audited, gate flow green |
| 8 | Aug 25 | **Tier 1 vision recon + recovery** (time-boxed 90s) | 09 | Recon coverage + no-submit allowlist + `?v=2` recovery beat green |
| 9 | Aug 26 | Webhooks, resume handler, Scheduler/Pub/Sub + **evals start** | 07, 08, 11 | 07 §acceptance green (portal_event → FOLLOW_UP; feedback resume); eval sets written |
| 10 | Aug 27 | **Cloud deploy** + **finish evals** (CI green by mid-day) | 13, 11 | 13 §verification checklist complete; CI green (Cloud SQL + Playwright-in-Docker already de-risked by Day-2 skeleton) |
| 11 | Aug 28 | UI polish, **voice-note intake** (Best Multimodal UX angle), adaptation money-shot rehearsal, architecture diagram, README spin-up | 10, 13 | Full demo script runs clean twice, timed ≤ 4 min; voice note → transcript → distilled rule works once end-to-end |
| 12 | Aug 29 | **Record video** (unedited action beats + cloud proof); draft Devpost text | brief §7–8 | Video ≤ 4 min incl. Cloud Run dashboard + Vertex logs. Pre-record checklist: `--min-instances 1` set, `/healthz` pre-warm, mock portal `/admin/ping-agent` green, board pre-seeded, live **PDF → structured record + citation** beat captured |
| 13 | Aug 30 | Submit; bonus: build-log post + social post (#AllThingsAgenticHackathon); Gemma/Veo/Lyria extra-model bonus only if trivial | brief §8 | Submission complete with 24 h buffer for Devpost surprises |

Aug 31 is buffer only. If any day slips, apply the cut list immediately — do not
"catch up later".

## Cut list (pre-agreed, in order)

1. **Real-portal adapter** — mock portal is the primary demo path anyway. (Cut first.)
2. **Lane 3 crawl** — depth-2 crawling is a flakiness multiplier for near-zero demo value; lanes 1+2 prove ingestion.
3. **`generate_application_pack` PDF rendering** — the document-surface story survives as a write-up paragraph.
4. **Live `?v=2` vision beat** — fall back to the cached `form_map` artifact + a pre-recorded segment.
5. **Extra source adapters** — one web_page source + the search lane is enough for the sweep story.
6. **Embedding-based retrieval** — deterministic tag retrieval already satisfies the spec.
7. **Agent Runtime / Vertex Agent Engine deploy** — plain Cloud Run is sufficient; note as roadmap.
8. **Investor-outreach stretch beat** — already optional in the brief; treat as deleted unless everything else is done by Aug 28.
9. ~~**Google Drive OAuth connector**~~ — **BUILT** (`services/drive_adapter.py`, Connections panel, one-time `scripts/oauth_setup.py`; `drive.readonly`, founder-selected files only). A **Gmail connector** (`gmail.readonly`, one watched label) ships alongside it.

Note: **Tier 1 vision recon is NOT on the cut list** — it is core to the
requirement-discovery story and the upgraded demo beat. If it slips despite
that, fall back to Tier-0-only demo (mock portal DOM fill) and describe vision
as roadmap in the write-up; do not sacrifice the approval gate or evals to save it.
**Voice-note intake IS on the cut list** (Day-11 bonus for the Multimodal UX
prize): skip it without ceremony if anything above it is unfinished. ~~Live
duplex voice (Gemini Live / bidi-streaming) is roadmap, not v1~~ — **BUILT**:
`app/live.py` WebSocket surface on the same orchestrator + session, verified
live (text→audio round-trip + browser mic pipeline, 2026-08-19).

## What NOT to build (scope guard, mirrors brief §5)

`21-alex-platform-north-star.md` specifies the post-v1 platform direction. It
does not expand this hackathon build scope; its migration phases begin only
after the final acceptance criteria below are green.

**Authorized pre-Phase-0 compatibility slice:** the competition build may ship
the feature-flagged conversational-discovery adapter described in 07/08:
`/discover` plus optional prose reuses the existing sweep, matchmaker, board,
founder selection, and `grant_applications.yaml` lifecycle. This exception also
includes transactional selection idempotency, opaque request IDs, durable
discovery execution receipts, transcript preservation, and their tests. It does
**not** authorize WorkflowRun/journey records, durable selection waits, `/apply`,
new workflow YAML, discovery-command attachments, a selection API/UI, or changes to review,
approval, submission, webhook, and follow-up guards. Today, natural-language
and command paths promise outcome-level convergence on the same deduplicated,
scored board; Phase 2 retains its stronger compile-level equivalence contract.
The independent attachment foundation (explicit conversation/profile scopes,
native safe extraction, citations, and durable workers) may ship without joining
attachments to `/discover` or changing the competition lifecycle.

- General business advice chat, strategy, pricing, hiring talk.
- "Works on any website" automation. (18's browse tools are **read-first** —
  open/navigate/read on request with no submit capability; that is research,
  not arbitrary-site automation.)
- More workflow definitions than `grant_applications.yaml`.
- Payments, taxes, legal workflows.
- A frontend framework, auth system, or multi-user support beyond a `founder_id`.

## Post-core additions (after final acceptance, time permitting)

- **17 — real portal access** (registration + sign-in, mailbox verification).
- **18 — browser agent**: orchestrator `browse.*` tools + Browser panel live
  view. Cheap demo win (founder pastes a link, Alex reads it live on screen);
  build only after the final acceptance criteria below are green.
- **22 — browser runtime hardening**: after 18 is green, single-flight process
  ownership, common popup/dialog/download containment, snapshot-first SSE,
  browse/fill Stop, quotas, durable expiry, launch circuit breaker, and static
  no-external-browser enforcement. Implement in 22's six work-item order; it
  hardens the browser surface without adding actions.
- **20 — Gemma Evidence Checker**: isolated, advisory comparison of completed
  drafts against persisted founder/programme evidence. Build only from its
  contract and enable only after its labelled rollout eval passes; it adds no
  agent, state, or approval gate.
- **23 — session-linked resources and global search**: after the existing
  competition lifecycle remains green, add the canonical resource/search
  projection and many-to-many session links in 23's eight-work-item order.
  This is a compatibility seam for 21, not permission to skip directly to the
  WorkflowRun phases or make chat history the work store.
- **34 — platform architecture and convergence**: the implementation overlay
  for 21. After the competition acceptance criteria are green, execute its
  Phase 0 stabilization gate first; no later runtime, memory, capability, or
  domain-expansion phase is authorized by the document alone.
- **25 — role-scoped Hiring intake and collaboration**: normal `/hiring` creates
  a real Founder-authored draft; the fixed FDE package remains an optional demo
  fixture only. The app-owned public form stays closed until exact role approval,
  a separate Founder publication click, and the dedicated encrypted-intake key.
  The safe local slice may exercise synthetic candidate applications, restricted
  CV/application records, evidence-to-criterion coverage labelled only
  `PRESENT` / `MISSING` / `UNCLEAR`, a candidate-scoped explanatory Alex
  conversation, and explicit Founder decisions. Alex never scores, ranks,
  recommends, advances, or declines a candidate. Production intake additionally
  requires role-scoped envelope encryption, explicit privacy consent, bounded
  uploads, data-rights controls, and a separately approved deployment rollout.

## Final acceptance criteria (mapped to judging)

**Innovation & Operational Utility (40%)**
- [ ] Agent leads every interaction: proposes next action, asks gap questions, advances the checklist — founder only answers and approves.
- [ ] Adaptation is provable on screen: seeded rejection → rule → cited avoidance in a later draft.
- [ ] Messy unstructured inputs become structured, cited records — **live on camera**: a real guidelines PDF → structured `Opportunity` record with a `raw_excerpt` citation (not seeded rows).

**Architectural Discipline (30%)**
- [ ] State machine + durable sessions + `state_delta` resume demonstrated by kill/restart.
- [ ] 7 agents with enforced tool scoping; guards G1–G3 refuse and audit.
- [ ] Idempotency, staleness guards, evals in CI.

**Demo & Production Readiness (30%)**
- [ ] ≤ 4-min video: problem → board → interview → draft/feedback/adaptation → live form-fill → approval → submit → webhook → Cloud proof. Unedited action beats.
- [ ] Repo: architecture diagram, reproducible README, eval configs, no secrets.
- [ ] App live at `.run.app` URL through the judging period at near-zero cost.

**Bonus (cheap wins, Day 13 only)**
- [ ] Public build-log post (states it was created for this hackathon).
- [ ] Social post with #AllThingsAgenticHackathon.
- [ ] Gemma Evidence Checker (20) enabled only if its product contract, live
      labelled evaluation, and visible review-panel result are green — never a
      decorative model call for +0.2.

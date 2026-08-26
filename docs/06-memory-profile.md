# 06 — Memory & Founder Profile

The adaptation story is the Collaborative Partner money shot. This doc specifies
where every fact lives, how feedback becomes rules, and how the drafter retrieves
them. If a judge asks "how does it adapt?", the answer is this file.

## Memory tiers (match each fact to a lifetime)

| Tier | Mechanism | Survives restart | Survives session end | Contents |
|---|---|---|---|---|
| Invocation | `temp:*` state keys | no | no | one turn's scratch |
| Session | plain state keys | yes | no | current application position, checklist |
| User | `user:*` state keys | yes | yes | profile pointer, interaction prefs |
| Long-term | Firestore `profiles/{id}` (reads via tools) | yes | yes | Founder Profile — the durable "who they are and how they think" |
| Blobs | artifact service | yes | yes | source pages, PDFs, screenshots, attachments |

Rule of thumb: conversation remembers the *work*; the profile remembers the
*founder*.

## Long-term memory: profile-as-memory (no separate memory service in v1)

The Founder Profile in Firestore IS the long-term memory. Agents read it via
tools (`get_profile`, `get_voice_rules`, `get_relevant_answers`) — v1 ships no
`FirestoreMemoryService`, because an unconsumed service is orphan code a judge
can poke at. The ADK `BaseMemoryService` seam (pass `memory_service=` to the
webhook Runner later) is the documented swap point for Vertex AI Memory Bank;
mention it in the write-up as roadmap, with "profile-as-memory, via tools" as
the v1 answer.

## Founder Profile schema (`profiles/{founder_id}`)

```json
{
  "version": 17,
  "facts": {
    "company_name": "...", "stage": "pre-seed", "sector": "...",
    "geography": "...", "traction_summary": "...", "team_size": "2",
    "arr_band": "pre-revenue"
  },
  "voice_rules": [
    {"id": "vr_3", "rule": "never use the word 'revolutionary'",
     "evidence": "founder rejection 2026-08-20: 'sounds like a scam pitch'",
     "created_at": "...", "active": true}
  ],
  "canonical_answers": [
    {"question_key": "describe_traction", "text": "...",
     "tags": ["traction", "metrics"], "approved_at": "...",
     "times_used": 4, "last_used": "...", "version": 3}
  ],
  "rejection_history": [
    {"program": "...", "date": "...", "stated_reason": "...",
     "lessons": ["..."]}
  ],
  "decision_patterns": [
    {"id": "dp_1", "pattern": "prefers non-dilutive funding",
     "evidence": "chose grant over equity accelerator twice"}
  ]
}
```

- `facts`: objective company data. Written by `record_answer` (interview) and
  `apply_profile_update(kind=fact_update)`.
- `voice_rules`: testable writing rules. Written **only** by the distiller.
- `canonical_answers`: founder-approved answer library. Written on section APPROVED
  (the approved text becomes the new canonical version for that question_key).
- `rejection_history` / `decision_patterns`: distiller-maintained.

## The adaptation loop (must be demo-visible)

```
founder edits/rejects section
   └─ record_feedback (verbatim reason stored)
        └─ POST /tasks/distill → distiller_agent runs in a system session
           (include_contents="none": sees ONLY the feedback JSON)
             └─ apply_profile_update → profile version+1
                  └─ NEXT draft: get_voice_rules / get_relevant_answers
                       └─ drafter cites the rule in save_draft_section.notes
                            └─ UI shows: "Avoided 'revolutionary' — your feedback of Aug 20"
```

Verbatim storage is deliberate and matches the reference lab's human-in-the-loop
pattern: **"store the sentence, not the schema"** — a rejection reason is one
decision with nuances no invented fields would survive. Distillation into rules
happens later, by the distiller, never at capture time.

Demo requirement (from brief §4): at least one moment where the agent explicitly
uses past feedback. Implementation checklist:
- [ ] `save_draft_section.notes` carries rule citations.
- [ ] UI renders notes under each draft (see 10).
- [ ] The week-1-edit → week-2-draft path is scripted in the demo (seed a rejection
      in `scripts/seed_demo.py`).

## Profile bootstrap: document ingestion ("understand the business")

The interview alone builds the profile slowly. The founder's real knowledge is
already written down: pitch deck, business plan, financial model, past
applications, rejection letters. The ingestion pipeline turns that corpus into
profile autonomously — asking the founder only about conflicts.

**Sources (behind a `DocSourceAdapter` interface):**
- `upload` (v1): founder drops files in the UI → artifact storage. Demo-safe,
  no OAuth. The default is **Use in conversation** (`reference_only`); **Add to
  Founder Profile** is an explicit choice. Upload registration is brief, then
  chat remains usable while extraction runs. Only a server-issued opaque id
  enters session state.
- `google_drive` (built): OAuth `drive.readonly`, founder-selected
  files only — no blanket indexing. Managed from the Connections panel;
  one-time consent via `scripts/oauth_setup.py`.

**Flow (every step judge-visible):**

```
document (upload or Drive)
  └─ byte/type/OOXML safety validation
       └─ scoped artifact + QUEUED ingestion (HTTP returns; no blocked chat)
            └─ idempotent Cloud Tasks worker
                 └─ structured pages/slides/sections → cited chunks
                      └─ retrieval index + Gemini document understanding
                           └─ profile scope only: PROPOSED updates:
                  facts (company, product, traction, team, market),
                  canonical-answer candidates (from past applications),
                  voice-rule candidates (from the founder's own writing),
                  rejection-history entries (from past rejection letters)
                  — each proposal tagged confidence high (verbatim) / low (inferred)
                  └─ auto_apply_profile_updates (autonomous, deterministic):
                       ├─ exact quote/hash citation + profile-scoped source
                       │    + fact/canonical-answer + non-conflicting
                       │    → EVIDENCE_VERIFIED profile update
                       └─ conflicts / low-confidence / missing citation /
                            voice-rule or decision-pattern → needs_founder:
                            founder confirms or rejects each flagged item (UI/chat)
                            ├─ approved → apply_profile_update
                            └─ rejected → feedback rows with reasons → distiller
```

Every upload records owner, origin session, explicit scope, content type, byte
size, SHA-256, randomized artifact name, and ingestion status. Chat resolves
those ids server-side; raw filenames have no authority. A profile-scoped upload
returns exact `auto_applied` and `needs_founder` counts, and the UI never claims
completion while the ingestion is `QUEUED`, `VALIDATING`, `EXTRACTING`, or
`INDEXING`.

Upload completion and extraction completion are distinct. The upload endpoint
returns an opaque attachment id with `QUEUED`; the client observes status changes
without holding the upload request open. A worker claims the ingestion with a
lease, commits chunks before profile proposals, and may be retried without
duplicating chunks or mutations. A generation pointer keeps readers on the
previous complete index if a multi-batch re-index is interrupted. Cloud Tasks
redelivers transient worker/model failures up to the bounded attempt cap. Zero
extracted content is `NO_TEXT`; malformed or unsafe content is
`FAILED`/`UNSUPPORTED`; none is described as success.
Profile proposals have deterministic ids, and each applied mutation writes a
transactional idempotency receipt. A crash after the profile write but before
the worker's terminal status therefore cannot bump the profile twice on retry.
This asynchronous contract is the Cloud Run path. Local development, where no
durable Cloud Tasks transport exists, deliberately completes extraction inside
the upload request instead of creating an orphan background coroutine.

PDF, DOCX, PPTX, XLSX, UTF-8 TXT, and CSV have native structured readers. Page,
slide, paragraph/section, table, speaker-note, sheet, and cell-range locators are
retained for citations. New upload extraction never invokes LibreOffice; that
external process is restricted to optional produced-document preview/conversion.
Legacy `.doc`, `.ppt`, and `.xls` are rejected with an export instruction.

`reference_only` artifacts can be searched through `search_attachment` when the
founder explicitly includes their ids in the current session. They cannot propose
or apply Founder Profile mutations. `profile` is an explicit UI choice, not the
default interpretation of every upload.

Two deliberate design choices:

1. **Autonomous application, deterministic escalation.** Extraction is
   auto-applied only for `fact_update` and `canonical_answer_update` when the
   claim has an exact hash-matching quote/locator in a founder-designated
   profile source and is non-conflicting. It is stored as `EVIDENCE_VERIFIED`,
   never founder-confirmed. Voice rules and decision patterns always wait for
   founder review until deterministic conflict/supersession semantics exist.
   Anything else is held with both values shown, and every
   rejection is *another verbatim feedback record*, so ingestion still
   produces adaptation evidence. All writes stay versioned and reversible;
   the only irreversible action in the system (submission) remains gated by
   founder approval.
2. **Voice seeding from the founder's own corpus.** Past applications and
   business writing are the founder's actual voice — the distiller extracts
   candidate voice rules and canonical answers from them instead of waiting
   for live rejections. Week-1 adaptation evidence exists on day one.

**Demo beat:** "It read my deck and business plan, applied 19 facts about my
company on its own, flagged 3 conflicts to ask me about — and it asked me 4
interview questions instead of 30."

## Retrieval at draft time

`get_relevant_answers(section_key)`:
1. Exact `question_key` match → latest approved version.
2. Else tag overlap with section key (tags assigned at approval time by distiller).
3. Else most recently approved answers, max 5.

Keep it deterministic and inspectable. Optional stretch (Best Architectural Design
fodder, only if core is done): Vertex text-embeddings over canonical answers,
cosine top-k; store embedding vectors in the same doc. Do not block v1 on this.

Attachment retrieval is separate from canonical profile authority. Every hit
returns `source_type`, `source_id`, `source_title`, a bounded `excerpt`, an exact
quote/hash/locator citation, and `authority=unconfirmed_evidence`. An incomplete
citation fails the result contract instead of synthesizing a successful-looking
answer. Revoked or deleted Drive sources keep historical provenance but render
`source_available=false`; high-impact facts then require revalidation for a new
consequential action.

## Context window discipline

- Source pages / PDFs → artifacts; only ≤300-char summaries in conversation.
- Compaction (04) summarizes old turns after 8; the state machine, not the
  summary, carries workflow position.
- Drafter receives: one section spec, ≤5 canonical answers, voice rules, program
  record. Nothing else. If it needs more, it calls a tool.

## Acceptance checks

- [ ] Feedback with reason "too buzzwordy, drop 'revolutionary'" results in an active voice_rule within one distiller run; the next `save_draft_section` notes cite `vr_*`.
- [ ] Approving a section replaces the canonical answer for its question_key (`version` bumps, `times_used` resets preserved history).
- [ ] Profile survives: delete session, new session, `user:profile_id` resolves, `get_profile` returns prior rules.
- [ ] A PDF/DOCX/PPTX upload returns `QUEUED`, becomes `READY` or
      `NEEDS_FOUNDER` asynchronously, and remains searchable by cited page/slide/
      paragraph chunks after a worker retry.
- [ ] Empty, corrupt, encrypted, type-mismatched, legacy Office, and archive-bomb
      fixtures terminate honestly without model invocation or profile mutation.
- [ ] `reference_only` never mutates the profile; a foreign-session attachment id
      returns not-found from both resolution and search.

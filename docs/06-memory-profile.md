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
  no OAuth.
- `google_drive` (built): OAuth `drive.readonly`, founder-selected
  files only — no blanket indexing. Managed from the Connections panel;
  one-time consent via `scripts/oauth_setup.py`.

**Flow (every step judge-visible):**

```
document (upload or Drive)
  └─ artifact companydoc_{ts}
       └─ Gemini document understanding (same multimodal stack; decks, DOCX,
          PDFs, Sheets for traction numbers)
             └─ structured extraction → ingestions doc with PROPOSED updates:
                  facts (company, product, traction, team, market),
                  canonical-answer candidates (from past applications),
                  voice-rule candidates (from the founder's own writing),
                  rejection-history entries (from past rejection letters)
                  — each proposal tagged confidence high (verbatim) / low (inferred)
                  └─ auto_apply_profile_updates (autonomous, deterministic):
                       ├─ confident + non-conflicting → apply_profile_update
                       │    (versioned, evidenced, audited — no founder prompt)
                       └─ conflicts / low-confidence → needs_founder:
                            founder confirms or rejects each flagged item (UI/chat)
                            ├─ approved → apply_profile_update
                            └─ rejected → feedback rows with reasons → distiller
```

Two deliberate design choices:

1. **Autonomous application, deterministic escalation.** Extraction is
   auto-applied only when it is both confident (stated verbatim in the
   document) and non-conflicting against the current profile — the conflict
   check is code, not model judgment. Anything else (a contradicted fact, an
   inferred number) is held for the founder with both values shown, and every
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

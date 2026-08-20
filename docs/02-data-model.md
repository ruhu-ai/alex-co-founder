# 02 — Data Model

Two stores, plus ADK session state and artifacts. Firestore is the system of record
for the pipeline; ADK sessions (SQLite/Cloud SQL) own conversation + session state.

## Firestore collections

### `opportunities/{opportunity_id}`

One document per discovered program/grant. Written by Scout + Matchmaker tools.

| Field | Type | Set by | Notes |
|---|---|---|---|
| `name` | str | scout | |
| `source_url` | str | scout | where it was found |
| `source_type` | str | scout | `web_page` \| `pdf` \| `newsletter` \| `manual` |
| `award` | str | scout | free text, e.g. "$50,000" |
| `deadline` | str\|null | scout | ISO date; null if rolling |
| `eligibility` | list[str] | scout | bullet strings, verbatim-ish |
| `application_url` | str | scout | where the form lives |
| `required_materials` | list[str] | scout | e.g. ["2 essays", "deck", "budget"] |
| `description` | str | scout | ≤ 500 chars |
| `raw_excerpt` | str | scout | source snippet for citation; ≤ 2000 chars |
| `dedup_hash` | str | scout | sha256(normalized name + application_url) |
| `state` | str | pipeline | DISCOVERED → SHORTLISTED \| ARCHIVED |
| `fit_score` | int\|null | matchmaker | 0–100 |
| `fit_rationale` | str\|null | matchmaker | ≤ 280 chars, founder-facing |
| `archive_reason` | str\|null | matchmaker | required when state=ARCHIVED |
| `urgency` | map\|null | sentinel | `{tier, days_left, note}` — see 08 |
| `created_at`, `updated_at` | str | server | ISO-8601 UTC |

Indexes: `state` ASC, `deadline` ASC. (`dedup_hash` unique-enforced in code.)

### `applications/{application_id}`

Created when the founder chooses to apply to a SHORTLISTED opportunity.

| Field | Type | Notes |
|---|---|---|
| `opportunity_id` | str | ref |
| `founder_id` | str | |
| `state` | str | application lifecycle — see 03 |
| `checklist` | list[map] | `[{key, label, status, section_id?}]`; status ∈ PENDING \| IN_PROGRESS \| DONE \| BLOCKED |
| `interview_qa` | list[map] | `[{question_key, question, answer, asked_at, answered_at}]` |
| `draft_sections` | list[map] | `[{section_id, section_key, content, word_count, status, version}]`; status ∈ DRAFTED \| IN_REVIEW \| CHANGES_REQUESTED \| APPROVED |
| `form_fill_report` | map\|null | `{filled, total, needs_human: [field], portal_state_hash, screenshot_artifact, ran_at}` |
| `submit_idempotency_key` | str | generated on entering APPROVED; see 05 |
| `submission` | map\|null | `{confirmation_id, submitted_at, portal_url}` |
| `followups` | list[map] | `[{kind, due_at, status, note}]` |
| `created_at`, `updated_at` | str | |

### `profiles/{founder_id}` — the Founder Profile (long-term memory)

Structure detailed in **06-memory-profile.md**. Collections: `facts`, `voice_rules`,
`canonical_answers`, `rejection_history`, `decision_patterns`. This document is the
"constantly adapts" evidence; every mutation is versioned (`version` int, bumped by
`apply_profile_update`).

### `ingestions/{ingestion_id}`

One document per ingested company document (06 §bootstrap).

| Field | Type | Notes |
|---|---|---|
| `founder_id` | str | |
| `source_type` | str | `upload` \| `google_drive` |
| `source_ref` | str | filename or Drive file id |
| `artifact` | str | `companydoc_{ts}` artifact name |
| `status` | str | EXTRACTED \| REVIEWING \| CONFIRMED |
| `proposed_updates` | list[map] | `[{id, kind, payload, evidence_quote, status: PENDING|APPROVED|REJECTED}]` — kind matches `apply_profile_update` kinds |
| `confirmed_at` | str\|null | |
| `created_at` | str | |

### `feedback/{feedback_id}`

| Field | Type | Notes |
|---|---|---|
| `application_id`, `section_id` | str | |
| `founder_id` | str | |
| `type` | str | `approve` \| `edit` \| `reject` |
| `original` | str | draft as presented |
| `edited_text` | str | when type=edit |
| `reason` | str | verbatim founder reason when type=reject/edit — required for reject |
| `distilled` | bool | set true after distiller runs |
| `distilled_rule_ids` | list[str] | pointers into profile mutations |
| `created_at` | str | |

### `approvals/{approval_id}`

| Field | Type | Notes |
|---|---|---|
| `application_id` | str | |
| `gate` | str | `submit_application` (v1: only this) |
| `token` | str | opaque, `uuid4().hex`; single-use |
| `status` | str | PENDING \| GRANTED \| CONSUMED \| EXPIRED \| DENIED |
| `expires_at` | str | now + `APPROVAL_TTL_MINUTES` |
| `granted_by` | str\|null | founder_id on grant |
| `consumed_at` | str\|null | |
| `created_at` | str | |

### `audit/{audit_id}` — append-only, never updated

| Field | Type | Notes |
|---|---|---|
| `actor` | str | `agent:<name>` \| `founder:<id>` \| `system:<job>` |
| `action` | str | e.g. `form_fill`, `submit`, `profile_update`, `state_transition` |
| `idempotency_key` | str\|null | |
| `target` | str | entity ref, e.g. `applications/abc` |
| `result` | str | `success` \| `error` \| `refused` |
| `detail` | str | ≤ 500 chars, no PII beyond refs, **no secrets** |
| `created_at` | str | |

## ADK session state keys

Written via `tool_context.state`. Injected into instructions by name (see 04).

| Key | Prefix semantics | Contents |
|---|---|---|
| `current_step` | session | one of: `IDLE`, `TRIAGE`, or an application state |
| `active_application_id` | session | which application is in focus |
| `active_opportunity_id` | session | opportunity being discussed |
| `active_program_requirements` | session | requirements list for the interviewer |
| `checklist_status` | session | compact map `{item_key: status}` for instruction injection |
| `pending_signals` | list | events the agent is dormant waiting on, e.g. `["founder_reply", "portal_confirmation"]` |
| `current_section` | session | section key being drafted/reviewed |
| `browser_status` | session | `{active, url, goal, last_action}` — live browse-session state (18) |
| `today` | session | today's date (ISO), refreshed every turn by the callback — the agent's clock |
| `user:profile_id` | user-scoped | founder profile doc id (survives across sessions) |
| `user:prefs` | user-scoped | small map of founder interaction prefs |
| `app:workflow_id` | app-scoped | active workflow id |
| `temp:*` | invocation-only | scratch (e.g. `temp:page_extraction`) — never relied on later |

Initialize all keys in `before_agent_callback` (see 03/04) so instructions never
render an empty placeholder.

**Three user ids coexist — seed for all of them.** `adk web` hardcodes
`userId=user` (reference-lab note), eval sets seed `user_id="eval_founder"`
(11), and the UI uses the real `founder_id`. Because `user:`-scoped state —
including the `user:profile_id` pointer — is keyed by user id,
`scripts/seed_demo.py` seeds the Founder Profile under **all three ids**;
otherwise dev inspection in `adk web` and the Day-9 evals show an empty
profile and look broken.

## Artifacts (GCS / local file service)

Binary or large payloads never enter the prompt; they are saved with
`tool_context.save_artifact(...)` and referenced by filename in state.

| Artifact | Name pattern | Produced by |
|---|---|---|
| Source page snapshot | `source_{dedup_hash}.txt` | scout fetch |
| Guideline PDF text | `guidelines_{dedup_hash}.txt` | scout PDF parse |
| Founder deck/attachments | `attachment_{founder_id}_{name}` | upload endpoint |
| Form-fill screenshot | `fillshot_{application_id}_{ts}.png` | form-filler |
| Vision recon map | `form_map_{application_id}.json` | form-filler (Tier 1 recon, 09) |
| Application pack PDF | `pack_{application_id}_{version}.pdf` | drafter (`generate_application_pack`, 05) |
| Founder voice note | `voicenote_{founder_id}_{ts}.webm` | `submit_voice_note` (05) |
| Company document | `companydoc_{founder_id}_{ts}` | `ingest_document` (05) — decks, plans, models, past applications |

## Acceptance checks

- [ ] `services/firestore.py` exposes typed accessors for all 6 collections; creating an opportunity twice with the same `dedup_hash` returns the existing id (no dupes).
- [ ] `state_schema.py` defines constants for every state + key above; no string literals for states outside this file.
- [ ] Saving a 5 KB source page stores an artifact and returns a ≤ 300-char summary (never the full text into the conversation).

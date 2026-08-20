# 04 — Agents

Seven agents total: one root orchestrator + five sub-agents in the transfer
graph (scout, matchmaker, interviewer, drafter, form-filler), plus the
standalone distiller (task-run, isolated). Separation of concerns is a
judging criterion — each agent has a narrow instruction and a minimal tool set.
The drafter cannot submit; the form-filler cannot draft; the scout cannot score.

## Model selection

| Agent | Model | Why |
|---|---|---|
| orchestrator | `ADK_MODEL` (gemini-3.5-flash) | routing + judgment |
| scout_agent | `ADK_MODEL` | structured extraction from messy text |
| matchmaker_agent | `ADK_MODEL` | reasoning over fit |
| interviewer_agent | `ADK_MODEL` | conversational quality matters most here |
| drafter_agent | `ADK_MODEL` | writing quality + voice fidelity |
| form_filler_agent | `ADK_MODEL` | tool sequencing, page reasoning |
| distiller_agent | `ADK_MODEL` | precise rule extraction |

All read `os.environ["ADK_MODEL"]` via a shared `config.py` that fails loudly at
import if Vertex credentials are missing. `config.py` also bootstraps the
environment the way the reference repo does:

```python
import google.auth, os
from google.adk.models import Gemini
from google.genai import types

_, project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

MODEL = Gemini(
    model=os.environ["ADK_MODEL"],
    retry_options=types.HttpRetryOptions(attempts=3),   # transient 5xx/429 resilience
)
```

Version notes: pin `google-adk>=2.6`. `EventsCompactionConfig` (which we use)
is pre-GA in 2.6.x — `[EXPERIMENTAL]` log warnings are expected and harmless
(the reference lab ships with them). We do **not** use `ResumabilityConfig` —
see 03 for the deliberate `state_delta` deviation. The
`GoogleCloudPlatform/generative-ai` onboarding sample pins ADK 1.x; where its
patterns and ADK 2 differ, ADK 2 wins.

## Wiring (`agents/co_founder/agent.py`)

```python
from google.adk.agents import Agent
from google.adk.apps import App                      # verify import path on installed ADK
from google.adk.apps.app import EventsCompactionConfig  # verify on installed ADK
from .callbacks import initialize_session_state
from .instructions import ORCHESTRATOR_INSTRUCTION
from .sub_agents import (scout, matchmaker, interviewer,
                         drafter, form_filler, distiller)
from .tools import pipeline, feedback as feedback_tools, browse as browse_tools

root_agent = Agent(
    name="co_founder",
    model=MODEL,
    instruction=ORCHESTRATOR_INSTRUCTION,
    tools=[pipeline.get_pipeline, pipeline.choose_opportunity,
           feedback_tools.record_feedback,
           # general-purpose browsing (18) — open/navigate/read pages on request
           browse_tools.open_page, browse_tools.read_page,
           browse_tools.browser_action, browse_tools.close_browser],
    sub_agents=[scout.agent, matchmaker.agent, interviewer.agent,
                drafter.agent, form_filler.agent],
    before_agent_callback=initialize_session_state,
)
# NOTE: distiller is NOT a conversational sub-agent. It runs isolated in a
# system-owned session via POST /tasks/distill (see 06/07), configured with
# include_contents="none" so it sees only the feedback payload.

app = App(
    name="co_founder",
    root_agent=root_agent,
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=8,   # summarize after every 8 turns
        overlap_size=2,          # re-read 2 turns either side
    ),
)
```

`agents/co_founder/__init__.py` exposes both `root_agent` and `app`
(`adk web` prefers `app` when present).

## State injection

Instruction templates are Python strings with `{current_step}`,
`{active_application_id}`, `{checklist_status}`, `{pending_signals}` etc. ADK fills
these from session state on every invocation — this is how the agent always knows
where it is without replaying history. Keys must be initialized in
`callbacks.initialize_session_state` before first use (see 03).

**Template-key rule (binding):** instruction templates may reference ONLY keys in
the 02 state-key table — unknown keys render empty or fail. Two sanctioned
channels for everything else:
- **Static workflow values** (entity schema, workflow display name, fit
  criteria): baked into the instruction string at agent-construction time from
  the loaded `WorkflowDefinition` (plain Python formatting at startup) — never
  through session state or ADK templating.
- **Dynamic profile data** (voice rules, canonical answers, profile facts):
  fetched via tools (`get_voice_rules`, `get_relevant_answers`, `get_profile`),
  never injected — that is why those tools exist.

---

## 1. Orchestrator (root agent)

**Owns:** routing, state machine, founder-facing leadership. Does **not** do
specialist work itself — it delegates.

```
You are the Co-Founder — the founder's working partner for the active
applications pipeline (workflow display name baked in at build time). You lead;
the founder decides.

Today: {today}
Current step: {current_step}
Active application: {active_application_id}
Checklist: {checklist_status}
Waiting on: {pending_signals}

Routing rules — follow exactly:
1. current_step IDLE or TRIAGE: call get_pipeline, summarize the board
   (urgent first), and propose one concrete next action. When the founder
   picks an opportunity, call choose_opportunity and hand off to interviewer_agent.
2. INTERVIEWING: interviewer_agent owns the conversation. Do not draft anything.
3. DRAFTING: hand off to drafter_agent. One section at a time.
4. AWAITING_REVIEW: present each section via its summary; collect feedback with
   record_feedback (approve / edit / reject-with-reason). A reject or edit returns
   that section to DRAFTING. Never silently rewrite.
5. APPROVED: hand off to form_filler_agent immediately to pre-fill the form —
   do not wait for the founder to ask. When the last section approval flips
   state to APPROVED mid-turn, proceed in the same turn. Report the fill result
   (filled X/Y; fields needing the founder). The founder can also trigger
   filling from the UI.
6. FORM_FILLING / AWAITING_SUBMIT_APPROVAL: form_filler_agent acts. Never request
   submission yourself; submission requires the founder's approval token.
7. SUBMITTED / FOLLOW_UP: report status and what you are waiting for.

Behavior rules:
- AUTONOMOUS BY DEFAULT. Do the work without asking permission: research,
  score, ingest documents, draft, fill forms. Ask the founder only when you
  are blocked — a conflict between sources, a fact only they know, a genuine
  ambiguity — or at a gate that is theirs: section approval and final
  submission.
- LEAD. End every reply with what happens next and what (if anything) you need
  from the founder. Never ask "what would you like to do?"
- Never skip a state. If asked to, refuse briefly and name the gate.
- Ground every claim in tool data. If you don't know, ask a clarifying question
  or say what you will go and check.
- Cite the Founder Profile when it shaped something ("I kept this under 150 words
  because you asked for short answers on the last application").
```

---

## 2. scout_agent

**Owns:** fetching sources, extracting structured `Opportunity` records. Runs in
background sweeps (see 08), not founder chat. Knows nothing about "grants" — it
extracts whatever `entity_schema` the workflow defines.

```
You are the Scout. You receive fetched source material and the workflow entity
schema, and you extract clean structured records.

Entity schema: baked into this instruction at build time from the active
workflow definition (see template-key rule above).
Raw source is provided per fetch. Rules:
- Extract ONLY what the source states. Missing fields are null, never invented.
- Copy a raw_excerpt (<=2000 chars) that justifies the extraction — this is your
  citation.
- Normalize deadlines to ISO dates; "rolling" -> null.
- Call save_opportunity once per record found. Call dedupe_check first; skip dupes.
- Save full source text as an artifact, return only summaries to the conversation.
```

Tools: `discovery.search_programs`, `discovery.fetch_source`, `discovery.dedupe_check`, `discovery.save_opportunity`.

---

## 3. matchmaker_agent

**Owns:** fit scoring and urgency. Writes `fit_score`, `fit_rationale`, `state`
(SHORTLISTED/ARCHIVED) on opportunities.

```
You are the Matchmaker. Score each pending opportunity against the Founder
Profile. Call get_profile first; never score from memory.

For each opportunity:
1. Call get_unscored_opportunities.
2. Score 0-100 on fit: stage, sector, geography, award size vs. profile.
3. fit >= 70: call shortlist with a <=280-char founder-facing rationale and a
   deadline-urgency note ("closes in 9 days, needs 2 essays - start now").
4. fit < 70: call archive_with_reason. The reason is mandatory and specific
   ("requires $50k+ ARR; profile shows pre-revenue"), never "not a fit".
Be conservative: shortlisting weak fits wastes the founder's time.
```

Tools: `pipeline.get_unscored_opportunities`, `pipeline.shortlist`,
`pipeline.archive_with_reason`, `profile.get_profile`.

---

## 4. interviewer_agent

**Owns:** clarifying questions + step-by-step guidance. The heart of the
Collaborative Partner rubric.

```
You are the Interview & Guide agent. You prepare the founder for a strong
application by finding and filling gaps between what the program demands and what
the Founder Profile already knows.

Program requirements: {active_program_requirements}
Known profile facts: call get_profile at conversation start and after answers
update it; never assume what the profile holds.
Checklist: {checklist_status}

Rules:
- Ask ONE clarifying question at a time, and say why you are asking it
  ("This program requires a sustainability plan. Do you have one, or should I
  draft one later from your impact metrics?").
- When the founder answers, call record_answer immediately.
- If an answer conflicts with a stored fact, ask which is current; never
  silently overwrite.
- When no gaps remain for the required sections, call complete_interview.
- Guide step-by-step: after each answer, show checklist progress
  ("3 of 6 done. Next: traction numbers.").
```

Tools: `profile.get_profile`, `profile.record_answer`, `pipeline.complete_interview`,
`pipeline.get_checklist`, `profile.ingest_document`, `profile.auto_apply_profile_updates`,
`profile.propose_profile_updates`, `profile.confirm_profile_updates`.

Document-driven profile building: when the founder provides company documents
(onboarding or mid-flight), drive the ingestion flow (06 §bootstrap): ingest,
then `auto_apply_profile_updates` — confident, non-conflicting proposals land
in the profile immediately (versioned, evidenced, audited). Ask the founder
only about `needs_founder` items: for a conflict, state both values and ask
which is current; resolve flagged items via `propose_profile_updates` /
`confirm_profile_updates`. Only ask interview questions for gaps the documents
did not fill. Documents first, questions second.

---

## 5. drafter_agent

**Owns:** section drafts in the founder's voice.

```
You are the Drafter. You write application sections that sound like the founder,
grounded only in the Founder Profile.

Section to draft: {current_section}
Program context: {active_opportunity_id} (call get_opportunity for details)
Before drafting: call get_voice_rules and get_relevant_answers for this
section — every draft applies them.

Rules:
- Ground every claim in profile facts or interview answers. Invented metrics,
  customers, or awards are a critical failure. If a fact is missing, write
  "[FOUNDER TO SUPPLY: ...]" and flag it.
- Respect the program's word limit. State the word count.
- Apply voice rules explicitly, and cite them in your tool call notes when a rule
  shaped the draft ("avoided 'revolutionary' per feedback of 2026-08-20").
- Call save_draft_section. Never paste a full draft only into chat.
```

Tools: `drafting.save_draft_section`, `pipeline.get_opportunity`,
`profile.get_relevant_answers`, `profile.get_voice_rules`.

---

## 6. form_filler_agent

**Owns:** browser automation. The most constrained agent (see 09 and 12).

```
You are the Form-Filler operator. You navigate an application portal, discover
what it requires, and pre-fill the form from APPROVED answers. You are precise
and pessimistic.

Fill report target: application {active_application_id}

Rules:
- On any portal, start with map_form_requirements to learn what the form asks
  (it is cached; reuse it while the page signature is unchanged). Its form_map
  feeds your fill mapping and the Interviewer's gap analysis.
- Fill from approved sections only. Partial success is a valid outcome: report
  "filled X/Y; fields needing the founder: ...".
- If the page changed since inspection (the fence will stop your tool call),
  re-run map_form_requirements and report what changed - never guess fields.
- vision_step is for recon and recovery ONLY. Its allowlist excludes submission
  controls by construction; never attempt to submit through it.
- Never submit without a valid approval token. submit_form validates it; if it
  fails, report the refusal - do not retry with cleverness.
- Call capture_screenshot after filling and after submission.
```

Tools: `browser.open_portal`, `browser.inspect_form`, `browser.verify_page_state`,
`browser.map_form_requirements`, `browser.vision_step`, `browser.fill_fields`,
`browser.capture_screenshot`, `browser.submit_form`, `pipeline.request_approval`.

---

## 7. distiller_agent

**Owns:** turning raw feedback into structured profile mutations. Invoked after
`record_feedback` via `POST /tasks/distill` in a **system-owned session** — never
as a transfer target in founder chat. Configured `include_contents="none"` and
`output_key="distillation"` (reference-lab pattern: the worker sees none of the
conversation; its answer lands in state, not the transcript). Its only input is
the feedback JSON in the wake message.

```
You are the Feedback Distiller. You convert one piece of founder feedback into
precise Founder Profile mutations.

Input: feedback type, original text, edited text, founder's verbatim reason.

Produce mutations of kind:
- voice_rule: a specific, testable writing rule.
  BAD: "be better". GOOD: "never use the word 'revolutionary'".
- canonical_answer_update: improved stored answer for a question_key.
- fact_update: corrected company fact (with the correction evidence).
- decision_pattern: how the founder chooses (e.g. "prefers programs with
  non-dilutive funding").

Rules:
- Quote the founder's reason in the rule's `evidence` field.
- One feedback may yield 0-3 mutations. Zero is fine if it was situational.
- Call apply_profile_update for each mutation. Never touch facts the feedback
  did not address.
```

Tools: `profile.apply_profile_update`, `feedback.get_feedback`, `feedback.mark_distilled`.

---

## Acceptance checks

- [ ] `adk web` graph view shows 6 agents with the wiring above (root + 5 sub-agents); the distiller is standalone (07) and does not appear in the transfer graph.
- [ ] Tool scoping: drafter's tool list contains no browser or submit tools; form-filler's contains no drafting tools (assert in a unit test importing both agents). The orchestrator's `browse.*` tools (18) appear in **no** sub-agent's list.
- [ ] Each instruction renders with state values present (no literal `{current_step}` reaching the model) — verify in `adk web` event view.
- [ ] Compaction triggers after 8 turns; older turns collapse into summary events (visible in `adk web`).

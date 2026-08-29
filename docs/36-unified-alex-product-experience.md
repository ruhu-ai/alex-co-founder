# 36 — Unified Alex product experience

**Status:** proposed design specification for review only.

**Authorization boundary:** this document does **not** authorize UI
implementation, route changes, schema changes, connector activation, production
Hiring, or any external effect. It is a documentation-only proposal. Existing
specifications and code remain binding until an implementation phase is
separately approved, built, tested, and accepted. “Proposed” below always means
target behavior, not behavior the current application claims to provide.

This specification unifies the founder-facing Co-Founder application and Hiring
Operations into one Alex product language. It is standalone for product/design
review, while preserving the authority, approval, evidence, privacy, and
dormancy contracts defined by the current architecture.

**Binding companion:** [38 — User-facing vision for Alex](38-user-facing-vision.md)
owns the live-media capture and transport protocol, server-owned consent grants,
single-use start nonces, explicit start/resume/restart gestures, bounded frame
delivery, still-image ingestion, deterministic voice-cloud reducer, live-media privacy,
retention, telemetry, and safe-error contracts. This document owns the unified
information architecture, navigation, layouts, contextual workspace, Hiring UX,
decision experience, responsive behavior, and high-level product requirements.
Where both documents describe the voice cloud or media-sharing experience, doc 38 is
normative for reducer, transport, consent, privacy, and retention mechanics;
this document is normative for placement, product composition, and shared-shell
behavior. The documents are complementary, not alternatives.

---

## 1. Executive product decision

The product becomes **conversation-first, work-visible**.

Alex occupies a spacious central conversation canvas rather than a narrow chat
column between two equally weighted admin panels. A stable navigation rail
anchors the left edge. A contextual workspace on the right stays Closed or
Quiet until there is useful work to show. Its four tabs are **Work, Evidence,
Decisions, and Activity**. There is no permanent Form tab; Work changes shape
with the active task.

Hiring is a dedicated operations workspace reached through the same navigation,
not a chat tab. It owns months-long RoleRun, CandidateRun, and later
OnboardingRun views, restricted evidence, human decisions, waits, and causal
timelines. It still feels like Alex: the same interaction dock, voice-call cloud,
captions, approval system, contextual conversation, visual tokens, copy, and trust
cues.

The central product promise is visible in the layout:

> Talk naturally to Alex. Alex does the reversible work. Evidence stays
> inspectable. The founder authorizes consequences through exact controls.

---

## 2. Existing behavior versus proposed behavior

| Area | Existing behavior | Proposed behavior |
|---|---|---|
| Main shell | Three edge-to-edge columns: Pipeline/Browser, Conversation, Review. Side panels often receive more width than chat. | Compact stable navigation rail, spacious conversation canvas, contextual right workspace Closed or Quiet by default. |
| Conversation | Conventional message bubbles in a relatively narrow middle panel; voice state lives mainly on a Talk button and interim caption bubble. | A narrow readable message measure inside a large calm canvas and a unified interaction dock. Ordinary text chat has no orb; an organic blue Alex status cloud and live captions appear only for an explicit active voice lifecycle. |
| Left surface | Funding Pipeline and Browser share one switchable left cell. | Left rail is navigation only. Pipeline/runs become destinations or contextual content; Browser moves to the right workspace. |
| Right surface | Application-specific review pane with pinned draft controls and Form/Documents/Activity reference tabs. | Product-wide contextual workspace with Work/Evidence/Decisions/Activity. The active run and selected entity determine content. |
| Browser | Shares the left cell with Pipeline and auto-opens there. | A conditional contextual-workspace destination on the right, revealed only when explicitly opened or backed by an actual run; it may enter Focus Stage and never opens an external window. |
| Forms | “The form” is a permanent reference tab in the application review pane. | Forms, browser, editor, checklist, comparison, and task views are polymorphic Work modes; no permanent Form tab. |
| Approvals | Application gate modal plus banners and a global approval inbox; Hiring has separate exact-action controls. | One shared Decisions model: exact server-rendered preview, one clear authorization, single-use state, and durable receipt across every domain. |
| Activity | Audit/reference pane and collapsible activity line, plus return digest. | One durable Activity grammar across conversation, runs, browser, approvals, and Hiring causal timelines. |
| Hiring | Separate sparse `hiring.html`: role sidebar, stacked cards, modal candidate Evidence Passport, and a small scoped Alex panel. | Dedicated Hiring workspace within the shared shell: role cockpit, candidate workspace, contextual Alex dock, shared Decisions and Activity surfaces. |
| Media | Microphone access starts voice/voice notes; no unified explicit founder-shared live-vision product contract. | Camera and browser-selected tab/window/whole-screen sharing follow doc 38's explicit disclosure, gesture, start-authorization, cue, stop, and no-frame-retention contract. Still images use the ordinary attachment path, not live sharing. |
| Responsive UI | At 960–1279 px, Pipeline/Browser and Conversation remain side by side while Review becomes a fixed right drawer. Below 960 px, Pipeline/Conversation/Browser become pane tabs and Review remains a drawer. | At ≥1280 px the rail, readable center, and optional workspace fit without horizontal overflow. The rail becomes bottom navigation on phones; contextual workspace and Hiring records become full-screen routes/sheets with preserved context. |

The proposal retains current strengths: semantic design tokens, generated
Phosphor icons, AA contrast, visible focus, read-only browser containment,
server-authoritative projections, no optimistic effects, exact approvals,
durable waits, citations, receipts, and non-ranking Hiring evidence.

---

## 3. Experience principles

1. **Conversation is the default place to begin and return.** Structured UI
   appears when it reduces ambiguity, shows work, or carries authority.
2. **Whitespace signals partnership, not emptiness.** The center gives Alex and
   the founder room to listen, think, speak, and review a concise exchange.
3. **Operational detail is contextual.** Evidence, drafts, browser frames,
   decisions, and receipts appear beside the conversation only when relevant.
4. **Durable work is not chat.** Conversation can reference runs and focus
   records, but never acts as their system of record or authority.
5. **Visual weight follows consequence.** Read-only work is quiet; uncertainty
   is explicit; irreversible or externally visible actions receive the
   strongest treatment.
6. **One Alex, many bounded contexts.** The persona and controls are coherent,
   while access to evidence, capabilities, and actions remains run-scoped.
7. **Autonomy without approval theatre.** Alex researches, analyzes, captions,
   drafts, and advises without prompts. The product asks once, exactly, only
   where founder authority is required.
8. **Visible media scope.** Alex sees or hears only the named source the founder
   deliberately shares. There is no ambient desktop access.
9. **State is stated, not performed.** Motion may reinforce a named state; it
   cannot be the only way to perceive it.
10. **Committed truth wins.** UI state follows server projections and receipts;
    a fluent message, generated artifact, or browser frame cannot claim success.

---

## 4. Information architecture and navigation

### 4.1 Recommendation: compact icon rail

Use a **compact icon rail**, not a top-navigation arrangement.

Reasons:

- The product needs persistent destinations across conversation and specialist
  operations. A rail scales better than a top bar as Runs, Hiring, Decisions,
  Activity, and future workflow families mature.
- The top edge is valuable for the active context: conversation/run title,
  workspace scope, privacy state, connector health, and window controls. Primary
  navigation there would compete with live state and lead to truncation.
- A rail gives the center a visually stable axis while the right workspace
  opens, closes, or widens.
- It maps naturally to a bottom destination bar on phones without changing the
  information architecture.
- It makes Hiring a first-class destination without presenting it as another
  mode of a funding pipeline or another chat tab.

The rail is **64 px** wide at normal desktop density and may expand to a
**216–240 px** labelled drawer on explicit request. It does not resize on hover.
Icons come only from the generated Phosphor sprite; every item has a visible
tooltip and accessible name. On side-rail layouts the New/Alex/Search/Runs/
Hiring/Decisions/Activity stack begins below the global header with a deliberate
12 px inset; it never enters the header band. The rail's aligned 52 px header
slot remains intentionally blank until a real Co-Founder product icon is
approved; no generic placeholder is rendered. Item rhythm stays even and
Settings remains anchored at the bottom. The phone bottom bar does not inherit
this vertical inset or render a placeholder slot.

### 4.2 Rail destinations

One global **New** session item precedes Alex. It is the sole session-creation
control in normal UI: a filled blue rounded rail item with a white plus and
white visible **New** label, plus the accessible name/tooltip **New session**.
Its hover, active, and focus states retain AA contrast in light/dark themes and
the same labelled action occupies the mobile navigation. It is deliberately
distinct from the transparent/outlined Work in this session disclosure.

In recommended order:

1. **Alex** — conversation home and return digest.
2. **Search** — sessions, runs, artifacts, and permitted records.
3. **Runs** — all durable work, including funding/application journeys.
4. **Hiring** — dedicated operations workspace.
5. **Decisions** — global pending approvals and human decisions, with count.
6. **Activity** — cross-run receipts and alerts, not raw logs.

The binding rail glyphs are New/`plus`, Alex/`chat`, Search/`search`,
Runs/`board`, Hiring/`hiring` (`users-three`), Decisions/`shield`,
Activity/`clock`, and Settings/`gear`. The interaction dock uses
Attach/`paperclip`, voice note/`mic`, live voice/`waveform`, camera/`camera`,
screen share/`screen-share` (`monitor-arrow-up`), and Send/`send`. These are the
generated local sprite mappings in [doc 16 §5.4.1](16-design-system.md); neither
a target for Camera/Hiring nor a globe for screen sharing is permitted.

Connections, profile/preferences, theme, help, and account live in a utility
group anchored at the bottom. Candidate records are excluded from generic
search unless a later reviewed Hiring contract explicitly permits bounded
role-scoped search; v1 keeps them inside Hiring.

The former “Pipeline” is now the Funding detail inside the global Runs
destination, not a permanent navigation destination or the definition of all
work. This preserves the workflow while removing the product’s grant-tracker
framing.

**Implemented boundary:** `?destination=runs` is a global, server-projected work
index with Funding, Hiring, and Skills families; investor outreach remains a
separately guarded activity within Funding. The existing funding pipeline
remains available as the Funding detail rather than defining the whole page.
Skill-backed work appears only after its own rollout gate admits
a real durable run. Hiring contributes only authorized role/run summary
metadata; candidate identity, Evidence Passports, decisions, notes, onboarding,
and restricted activity remain inside the dedicated Hiring workspace and are
never hydrated into the generic Runs surface. Future-operation cards are marked
`Planned` and have no launch action until their own contracts and gates exist.

### 4.3 Context header

The header is one restrained row above the content, not another navigation bar.
It contains:

- breadcrumb or context title, such as `Alex`, `Runs / Meridian Programme`, or
  `Hiring / Forward Deployment Engineer / C-014`;
- durable runtime and domain state in founder-facing language;
- scoped privacy/source cue when microphone, camera, tab, window, or screen is
  shared;
- workspace mode toggle and, when relevant, run controls;
- account/workspace menu.

The header never places an irreversible action beside routine navigation.

### 4.4 Navigation and focus rules

- Selecting a run or candidate changes the **focus context**, not necessarily
  the conversation session.
- The header always names both when they differ: for example, “Conversation:
  Weekly planning · Focus: Candidate C-014.”
- Contextual Alex conversations opened from Hiring bind to the selected role or
  candidate run on the server. They cannot fall through to a generic session or
  retain stale candidate scope after selection changes.
- Back navigation restores the prior list filters, scroll position, right-tab
  selection, and compact conversation state.
- Deep links include only opaque record identifiers; they never expose names,
  emails, source text, or approval tokens.

---

## 5. Shared desktop shell

### 5.1 Conversation-default layout

The workspace vocabulary is fixed:

| Mode | Width and meaning |
|---|---|
| **Closed** | No contextual workspace or edge handle is rendered because nothing is focused or pending. |
| **Quiet** | A **40–48 px edge handle only**. It may show the active-tab icon, pending Decisions count, or live-browser indicator; it displays no contextual content. |
| **Panel** | A **420–600 px** contextual panel beside conversation, defaulting near 520 px and clamped so the centered conversation remains readable. |
| **Browser Takeover** | Conditional Browser-only right workspace, defaulting to approximately two-fifths of desktop width while the full conversation remains alongside. It hides the persistent tab strip and is never the default for other content. |
| **Focus Stage** | An explicit temporary deep-work view: Work uses the larger share while the same comfortable conversation retains roughly two-fifths of usable desktop width and never becomes a sliver. |

Recommended at widths **≥1280 px**:

```text
┌──────┬───────────────────────────────────────────────┬──────────────────┐
│ rail │ context header                                                 │
├──────┼───────────────────────────────────────────────┬──────────────────┤
│      │                                               │ contextual       │
│ Alex │             Alex conversation canvas          │ workspace        │
│ Runs │                                               │ Closed/Quiet    │
│ Hire │            readable message measure            │ by default       │
│ Dec. │          voice cloud only during a call        │                  │
│ Act. │                                               │ Work Evidence    │
│      │           readable message measure            │ Decisions Act.   │
│      │                                               │                  │
│ util │               interaction dock                │                  │
└──────┴───────────────────────────────────────────────┴──────────────────┘
```

- Rail: fixed **64 px**.
- Center: `minmax(560px, 1fr)`; message content capped at **64–68ch**.
- Right workspace: Closed, Quiet, **420–600 px Panel** (520 px default),
  conditional Browser Takeover, or user/founder-expanded Focus Stage. At this
  breakpoint its maximum inline size is
  **`min(48vw, 600px, calc(100vw - 624px))`**: the final term reserves exactly
  64 px for the rail and at least 560 px for the readable center. Borders and
  splitter hit areas remain inside those allocated boxes.
- The center remains visually dominant whenever no focused Work view needs the
  stage.

Quiet is exclusively the 40–48 px edge handle. Panel is the 420–600 px open
contextual surface. Browser Takeover is the only renderer-specific wider
right-hand state; it does not redefine Panel for Work, Evidence, Decisions, or
Activity. Focus Stage is the expanded work layout below. This naming
is used throughout the product and state contracts.

### 5.2 Focus Stage layout

Browser, large document, comparison, spreadsheet, or complex form work may
become the main stage:

```text
┌──────┬───────────────────────────────────────────────────────────────┐
│ rail │ context header                                                │
├──────┼───────────────────────────────────────────────┬───────────────┤
│      │ Work main stage                               │ compact Alex  │
│      │ browser / document / editor / comparison      │ dock          │
│      │                                               │ voice/caption │
│      │                                               │ last messages │
│      │                                               │ composer      │
└──────┴───────────────────────────────────────────────┴───────────────┘
```

Entering Focus Stage is reversible and preserves the conversation. At ≥1280 px,
the conversation retains approximately two-fifths of usable width with a hard
480 px minimum; Work uses the remainder. At narrower widths the product switches
between conversation and workspace instead of rendering two cramped panes. The
Alex dock is not a second transcript; it is the same conversation projection
with the same interaction dock. Exiting focus
returns the conversation to center without creating a new session. If a live
voice lifecycle is active, the same voice cloud and captions move with the
conversation; otherwise no cloud placeholder is rendered.

### 5.3 Resizing and persistence

- Users may resize the right workspace within safe limits with a pointer or a
  keyboard-operable `role="separator"`.
- Panel width and active tab may persist per device and per major workspace
  (`Alex` versus `Hiring`), not per sensitive record. Focus Stage itself never
  persists or becomes a startup default.
- Agent-triggered opening may enter Panel only to the last user-approved width.
  Focus Stage requires an explicit founder focus action; a pending live Browser
  may offer it but not force, remember, or auto-restore it.
  Alex never repeatedly fights a manual collapse.
- A pending approval may call attention to Decisions but cannot steal focus
  from a text field or open a modal over active speech.

---

## 6. Conversation canvas

### 6.1 Composition

The canvas has four vertical zones:

1. **Context line** — active conversation, focused run/entity, and privacy cue.
2. **Voice stage, conditional** — the Alex soft cloud, voice status, and captions
   exist only from an explicit active voice lifecycle start through its visible
   terminal error/retry surface.
3. **Transcript** — a narrow, readable message stream; during voice, captions sit
   immediately above it without changing the text-chat layout when voice ends.
4. **Interaction dock** — stable input and media controls.

The transcript scroll container shares the centered conversation/composer
content grid; the bounded 64–68ch reading measure is nested within it. Its
scrollbar track sits immediately at the grid's logical `inline-end`, just
outside the readable message measure and aligned with Work in this session and composer—not
at the distant conversation-pane divider. Reserve a stable scrollbar gutter
from first paint so scrollbar
appearance, new messages, session-work disclosure changes, and route restoration never shift or
jitter the message column. In RTL the track follows logical inline-end. It must
not overlay message/document-reference hit areas, the composer, captions, or
the voice stage; native/platform scrolling remains available even when the
visual scrollbar auto-hides. Use the thin native/platform indicator—not a
custom animated scroll control. It is visually quiet at rest, appears during
pointer scrolling, hover, or keyboard focus, then fades according to platform
and reduced-motion preferences. Its visible thumb remains usable and has
sufficient contrast without becoming permanent heavy chrome.

On a new or quiet text conversation, one short Alex prompt and suggested actions
may sit above the transcript. There is no idle orb, cloud placeholder, star,
sparkle, or simulated-presence animation. The product must not fill the empty
space with dashboards, onboarding cards, or generic tips.

**Work in this session** is collapsed by default into one compact icon-and-count
disclosure immediately beneath the Conversation header. It is not product
navigation, a generic Context destination, or an alias for the last selected
workspace tab. Its accessible name and tooltip are **Work in this session**.
The trigger is an outlined, fully rounded control with a blue line icon, clear
hover/focus/expanded treatment, and a distinct light badge whose numeric
foreground remains dark and high-contrast in light mode with the equivalent
high-contrast semantic foreground in dark mode. Its accessible name includes
the count, so neither state nor quantity depends on color alone.
Activating it reveals only the current session's linked work list inline in the
main chat area; activating it again restores the clean chat without changing
the conversation or canonical work records.

The collapsed control and revealed list are part of the conversation content grid: their logical
start and end align exactly with the transcript/composer grid rather than
floating as a narrower centered card. It is collapsed by default on initial
load. When opened, session work remains directly below the Conversation header,
above the chronological transcript. Items use a readable wrapping grid (for
example `repeat(auto-fit, minmax(min(15rem, 100%), 1fr))`) instead of a squeezed
single-row carousel. Dense collections use progressive disclosure, paging, or
a vertically scrolling workspace region; they do not show an always-on inner
horizontal scrollbar. Each row focuses its canonical Work/Evidence/run item;
generated documents remain canonical in Work. This presentation change must
not alter item identity, counting, focus restoration, or the transcript scroll anchor.

### 6.2 Message behavior

- Founder and Alex turns form one immutable chronological transcript. New turns
  append at the bottom immediately above the composer; older turns scroll
  upward. Projection refreshes may reconcile delivery/status metadata but must
  never reorder committed turns or insert detached content at the bottom.
- Alex messages are plain readable blocks rather than large bordered chat
  bubbles; founder turns may retain a subtle aligned bubble for voice
  separation.
- Tool/runtime narration collapses into the Activity line and is not mixed into
  prose as chips for every internal step.
- Durable tasks, runs, evidence collections, and work panels live canonically in
  Work, Evidence, Decisions, or Activity and never enter the transcript as
  independently projected panels. A relevant assistant turn may contain one
  compact link/reference that opens and focuses that canonical item.
- A document created or first discussed by an assistant event may render as a
  compact document reference/card inside that exact assistant turn. The
  reference and the canonical Work item share one immutable item/artifact ID;
  opening the reference focuses that same item and never creates a copy. The
  card is never appended later as a detached bottom-of-page card, pinned,
  promoted above newer turns, reordered by projection refresh, or expanded into
  a persistent work panel. Later discussion links back to the same Work item.
- Exact approvals remain canonically available in Decisions and through their
  independent badge/route. A chronological assistant handoff may link to the
  same approval, but collapsing Work in this session or a transcript projection must not hide
  or duplicate the authorizing server-rendered control.
- New messages announce through a polite live region. Streaming text does not
  reread the entire partial response on every token.
- Auto-follow the bottom only when the founder is already at the latest turn.
  If they are reading older history, preserve the current visual scroll anchor
  while messages or work arrive and show a non-disruptive **New messages** or
  **New work** button/count. Activating it moves to the relevant chronological
  turn or focuses the canonical Work item; it never silently scrolls the page.
- “Alex is thinking” appears only when there is a real bounded request without
  a more specific activity description.
- The last committed transcript remains visible during reconnects. Unsent
  founder input remains in the composer until acknowledged.

### 6.3 Return digest

On return after absence, a compact digest appears above the latest transcript:

1. items blocked on the founder, ordered by urgency;
2. meaningful changes with durable receipts;
3. open waits and the next wake condition.

It shows at most three rows before **Show all** and is an initial/return
presentation, not persistent conversation chrome. **Show all** opens the
relevant Work, Decisions, Evidence, or Activity view; dismissal is local
presentation state and does not mutate workflow state. It does not overload the
separate Work-in-this-session disclosure.

The compact Work-in-this-session control is a native button with an accessible
name such as **Work in this session, 4 items**, `aria-expanded`, and
`aria-controls` for the inline list. It is reachable immediately after the
Conversation heading and operable with Enter/Space. Opening/closing preserves
the active transcript scroll anchor. Its count equals the visible deduplicated
session-work rows only; approvals and live-voice status retain their independent
Decisions and voice surfaces.

---

## 7. Alex live-voice cloud and multimodal state contract

### 7.1 Voice-only lifecycle and visual identity

The Alex cloud is a call-status instrument, not a persistent assistant avatar.
Ordinary text chat renders no orb, cloud placeholder, idle animation, star, or
sparkle. The voice stage becomes visible only after an intentional founder voice
start enters `CONNECTING`; it remains visible through an active call,
`RECONNECTING`, and a terminal voice error while its Retry/End surface is still
present. It hides and stops rendering when the voice lifecycle ends or that
terminal surface is dismissed. A vision-only connection, global approval,
background task, or text reasoning turn never makes it appear.

The binding visual is a simple soft blue cloud implemented with a native Canvas
2D renderer:

- two or three translucent, overlapping closed Bézier blobs, each built from
  eight to ten stable radial control points and low-frequency seeded sine phases;
- a static CSS radial-gradient halo behind the canvas, so no blur/filter is
  redrawn per frame;
- cool semantic blue roles from the design system, with modest light/dark
  variation and no identifying waveform, face, eye, lens, or external asset;
- a fixed layout box so deformation never changes document geometry; and
- adjacent visible status text and, where needed, a generated state icon outside
  the cloud. Color and motion are always redundant.

This component uses no WebGL, animation framework, bitmap/video loop, remote
asset, or media service. Canvas 2D gives the required organic, audio-responsive
form without WebGL context complexity or per-frame SVG/DOM path updates.
[`requestAnimationFrame`](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame)
provides repaint-aligned timing and normally pauses in hidden documents;
[`Path2D`](https://developer.mozilla.org/en-US/docs/Web/API/Path2D) is a widely
available optional path container, not a required dependency.

### 7.2 Trusted view model, states, and approval modifier

The shared-shell view model is a presentation of doc 38's one trusted reducer,
not a second source of state. Only one base state is announced at a time.
Approval is an orthogonal modifier while another base activity is visible. A
pending approval never suppresses Speaking or outgoing-audio response; after
the final audible output ends, Awaiting approval becomes the base state when no
higher-priority connection, speech, founder activity, or trusted processing
state applies.

The exact event priority, lifecycle gating, race handling, stale-generation
behavior, and reducer tests are owned by
[doc 38 §8.6](38-user-facing-vision.md#86-trusted-voice-cloud-activity-contract).

| State | Cloud behavior | Text/non-color cue | Audio/accessibility behavior |
|---|---|---|---|
| Connecting | Small blue form coalesces once into the fixed box. | “Connecting to Alex.” | One polite announcement; no microphone-listening claim before it is true. |
| Listening | Wider, gently open lobes; silence settles. Founder-input energy may add only bounded local deformation while the mic is actually live. | Microphone icon beside “Listening.” | Input energy is decorative and never announced, retained, or used while Alex is speaking. |
| Microphone off | Static, subdued blue silhouette. | Slashed-microphone icon + “Microphone off.” | The cloud remains only because the voice call is still connected; it never implies listening. |
| On hold | Static, subdued blue silhouette; no input-energy response. | Pause icon beside “On hold — only the on-device ‘Alex resume’ listener is active.” No Hold button is rendered. | Normal audio ingress, model output, captions, and visual sharing are fenced. Only a locally classified, Alex-addressed resume intent can restore fresh audio forwarding. |
| Thinking / Processing | Slightly compact cloud with very slow internal phase drift and a trusted specific activity label where available. | “Thinking” or closed-vocabulary activity text. | Never indefinite without wait/error fallback; model prose cannot select it. |
| Speaking | Brighter, fuller lobes respond to Alex audio that is actually audible. | Speaker/waveform icon beside “Alex speaking.” | Motion derives from outgoing playback only. Pending approval remains a static external marker. |
| Interrupted | A single 160–220 ms bounded compress/settle transition, then Listening. | Visible label changes immediately to “Listening.” | Queued output stops first; the transition never delays barge-in and produces no extra screen-reader announcement. |
| Awaiting approval | Nearly still blue form with a static amber bracket/badge outside the cloud. | “Awaiting your approval” plus stable route/count. | Only the durable approval projection selects it. Activating the route focuses Decisions; it never approves. No pulse or countdown. |
| Reconnecting | Cloud freezes or settles to low energy; a broken external status mark may make retry state non-color-only. | “Reconnecting to Alex.” | Last transcript/caption remains; retry is announced once. No media silently resumes. |
| Error | Static pinched/notched silhouette with external alert icon and Retry/End action. | Concise voice error and recovery copy. | No pulse. Assertive announcement only when the active voice action is blocked; dismiss/end hides the cloud. |

There is no ordinary-chat `IDLE` cloud. A connected call with no microphone and
no active output uses **Microphone off** or another truthful connected-call
label, never “Alex ready” or simulated presence.

#### Conversational Hold

Hold is voice-only; the live surface has no Hold/Resume button. During an active
call, a short command that directly addresses Alex and clearly means “wait” or
“hold” (for example, “Alex, hold on” or “Hey Alex, wait a moment”) enters
`HELD`. Mentioning Alex in background conversation, an unaddressed “hold on,”
or a longer mixed-purpose request does not. The command is an attention-state
intent only: it cannot authorize a tool, approval, memory write, workflow
transition, or external action, and is omitted from durable conversation turns.

In `HELD`, client and server both fence normal microphone audio before the Alex
model, queued response audio is suppressed, interim captions are cleared, and
active visual sharing stops. The call remains connected and the visible status
states that only a local resume listener is active. That listener uses verified
on-device `SpeechRecognition` with `processLocally=true`; it has no remote
fallback, creates no caption/transcript, retains no utterance, and sends the
server only a short addressed command after deterministic local classification.
The server independently reclassifies it and accepts only resume intent such as
“Alex, resume,” “Alex, continue now,” or “Alex, you can listen again.” All other
background speech is discarded on device. If on-device recognition or its
language pack is unavailable, conversational Hold is not negotiated for that
call; if the listener fails while held, the call ends fail-closed. **Pause
Alex** remains a separate explicit UI privacy control that releases microphone
capture entirely.

This implementation follows the browser's experimental on-device recognition
contract for [`processLocally`](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/processLocally),
[`available()`](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/available_static),
and [`install()`](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/install_static).

### 7.3 Vision sharing remains a separate truthful cue

Vision state never changes whether the cloud exists. A live source acknowledged
under doc 38 renders a persistent source chip/card near the context header and
capture controls:

- `Viewing selected tab: Programme application`
- `Viewing window: Keynote — Ruhu deck`
- `Camera shared`
- `Whole screen shared`

The chip/card includes the appropriate generated source icon, exact source
class, active/paused/not-sending copy, elapsed state where required, and a
visible **Stop sharing** control. It remains present for vision-only use, when no
cloud is rendered. During voice plus vision it sits adjacent to—not inside—the
voice status. No active cue appears without the local track and server
acknowledgement required by doc 38. Still images remain explicit attachments and
receive no live source state or Stop control.

When sharing stops or the operating system revokes it, the source cue clears or
changes to the truthful terminal state immediately. The transcript records only
a safe event such as “Screen sharing ended,” not source contents.

### 7.4 Rendering, audio alignment, privacy, and performance

- Run the cloud only while its voice lifecycle visibility gate is true. Use
  `requestAnimationFrame` with timestamp-based movement, cap drawing at 30 fps,
  pause/cancel on `document.hidden`, and discard the loop on voice end/dismiss.
- Size the backing canvas from its fixed CSS box with device pixel ratio capped
  at 2. Do not resize it on energy frames or cause layout/reflow.
- Normalize and clamp energy to `[0,1]`, apply a silence floor, approximately
  80 ms attack and 220 ms decay, and frame-rate-independent interpolation.
- Align speaking deformation with the AudioContext playback cursor, not network
  arrival. A bounded ephemeral queue may hold only per-chunk RMS plus scheduled
  start/end times and is discarded as playback advances. An equivalent bounded
  playback analyser is acceptable if it does not threaten audio delivery.
- Founder-input energy is used only for Listening while the mic is live. Alex
  outgoing energy is used only for Speaking. Interruption clears the scheduled
  output set and outgoing envelope immediately.
- No PCM copy, energy sample/series, shape seed, or animation trace is logged,
  persisted, sent to the server/provider, placed in analytics/browser storage,
  or used for inference, identity, biometrics, approval, or authority.
- Target a render cost below 2 ms p95 and no measurable audio underrun. If the
  budget is exceeded, step down to 15 fps and then a static state. Audio,
  captions, controls, and reducer correctness always outrank animation.

### 7.5 Accessibility and reduced motion

- The canvas and halo are `aria-hidden` and never focusable. A visible label plus
  a pre-existing `role="status" aria-atomic="true"` node announces deduplicated
  semantic state changes; audio amplitude and the interruption micro-transition
  never produce announcements. This follows
  [W3C technique ARIA22](https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA22).
- `prefers-reduced-motion: reduce` renders one deterministic static silhouette
  per state: no morph, sweep, scale, halo pulse, talk-button pulse, or energy
  response. Labels, state icons, captions, approval route, and controls remain.
- If Canvas 2D is unavailable or the renderer exceeds its safety budget, use a
  static CSS blue-gradient silhouette plus the same label/icon contract.
- The voice stage works in dark/light themes, grayscale, forced colors, at 200%
  zoom, and with browser text enlargement. No state relies on hue, shape, or
  motion alone.

### 7.6 Captions

- Captions are available for both founder and Alex speech and default on for a
  new live-voice session unless the founder preference says otherwise.
- The caption tray sits immediately below the active cloud/status and shows at
  most two recent lines before internal scrolling.
- Captions are collapsible to a one-line “Captions on” bar. Collapsing captions
  never hides or stops an otherwise active voice cloud; ending/dismissing voice
  hides both according to their own lifecycle.
- Interim words update in place; finalized captions join the durable transcript
  only under the applicable conversation-retention contract.
- Caption size, contrast, background opacity, and language are user-adjustable.
- Caption controls are keyboard reachable and do not require opening Settings.

---

## 8. Interaction dock

### 8.1 Layout and controls

The dock is a calm floating surface centered at the bottom of the conversation
or compact Alex dock. It contains:

- multiline composer with command and mention autocomplete;
- attach button with explicit attachment scope;
- talk/microphone control;
- vision/source picker;
- send button, replaced by Stop only when the current foreground turn is
  genuinely cancellable;
- concise activity/wait row above the controls.

The dock grows to a bounded height, then scrolls internally. It stays above the
mobile safe area and onscreen keyboard. Hit targets are at least 44×44 px on
coarse pointers.

Camera and screen-sharing controls are present but disabled and non-actionable
in ordinary text chat and throughout voice OFF, CONNECTING, RECONNECTING,
ended, or terminal-error states. Their accessible names and tooltips explain
that an active voice call is required. They become available only after the
trusted live view model reports `ACTIVE`, an actual microphone/audio context is
attached, and the server-negotiated capability for that source is enabled.
They return to disabled immediately when those facts stop being true. Enabling
a control does not start camera/display capture, reuse approval, or infer
consent: the founder must still intentionally activate it and complete doc 38's
source-specific disclosure, chooser/preview, Share, and fresh start-authorization
flow. Camera uses a literal camera glyph; screen share uses a monitor with an
outgoing arrow, never a globe.

### 8.2 Approval-free interactions

No approval prompt is shown for:

- ordinary text or voice conversation;
- listening after the microphone disclosure/permission and intentional session
  start required by the applicable live contract;
- live captions and transcript display;
- read-only analysis, research, evidence organization, and status inspection;
- draft generation, draft revision, and advisory recommendations;
- opening or closing the contextual workspace;
- focusing a run, candidate, document, citation, or browser receipt;
- pausing/stopping local media capture or a cancellable foreground operation.

A direct founder edit, feedback, or draft-approval button is itself the explicit
mutation command. It does not receive a second ceremonial confirmation unless
the mutation is independently irreversible or externally visible.

### 8.3 Media consent

Live-media disclosure acceptance is one-time **per source class, per
authenticated product session**, subject to doc 38's version, expiry, actor,
workspace, and session binding. It is separate from consequential-action
approval. Avoiding a repeated disclosure never grants capture authority.

First use presents a source-specific explanation:

- **Microphone:** “Share microphone audio with Alex for this live session.”
- **Camera:** “Share your camera with Alex for this live session.”
- **Selected tab/window:** the operating-system/browser chooser controls the
  exact source; Alex receives only that source.
- **Whole screen:** strongest warning; “Alex can see everything visible on this
  screen until you stop sharing.”

Every initial start, resume, and restart requires an intentional founder gesture,
the applicable local preview or browser source chooser, an explicit Share/Resume
action, and doc 38's fresh server-issued single-use start authorization. The
application never starts or resumes a live source from model text, prior consent,
transport recovery, retained client state, or a background event. Source change
ends the prior share and follows the reviewed new-source path.

Stop, permission revocation, logout, session end, page teardown, connection
loss, hidden/background suspension, device/source change, and operating-system
track end all fail closed. Reconnect may restore voice/text under their own
contract but never resumes live visual transmission automatically. No frames
captured or queued before a new acknowledged generation are delivered later.

Persistent cues while active:

- named source in the context header and persistent source chip/card, adjacent
  to voice status only when voice is active;
- source-specific icon and non-color text;
- elapsed sharing duration;
- always-available Stop sharing;
- immediate cue if capture is muted, paused, denied, revoked, or disconnected.

The application never claims ambient desktop access, never starts screen or
camera capture from conversation text, and never treats “you can look” as a
server-resolved external-action approval.

The binding consent grants/nonces, capture lifecycle, frame bounds, generation
fences, retention, telemetry, and safe-error behavior are defined by
[doc 38 §§5, 7, 9, 12, and 16](38-user-facing-vision.md). This UI document does
not define an alternate transport.

### 8.4 Still-image attachment

**Add image** and confirmed **Capture still** use doc 38's ordinary image
attachment/ingestion path. Before upload the founder sees the image preview,
conversation scope, durability/retention copy, and explicit Add action. Initial
images are owner- and origin-session-scoped `reference_only` artifacts under
that contract.

A still may appear as an attachment chip, Work preview, or cited Evidence item
while Alex analyzes it. It is never represented as a live share, never receives
a sharing duration or Stop sharing control, and does not participate in browser
permission, OS track revocation, or live-source restart state.

---

## 9. Contextual workspace

### 9.1 Shared tab model

The right workspace has exactly four **persistent** top-level tabs:

| Tab | Purpose | Examples |
|---|---|---|
| **Work** | The live object Alex or the founder is working on. Polymorphic, never a generic dashboard. | Application editor, form map/fill, checklist, role brief, policy diff, interview plan, document preview, comparison. |
| **Evidence** | Sources and citations supporting the focused work. | Programme requirements, Founder Profile facts, attachment excerpts, Evidence Passport, unknowns, contradictions, receipts supporting a claim. |
| **Decisions** | Draft feedback controls, human decisions, exact approvals, conflicts requiring judgment. | Approve/revise section, choose opportunity, candidate ADVANCE/HOLD/REQUEST_EVIDENCE/DECLINE, exact send/submit/invite approval. |
| **Activity** | Durable causal history and current waits. | Run events, browser actions, external action ledger, approval/decision receipts, next wake, uncertainty/reconciliation. |

There is no permanent Form tab. A form is one Work renderer selected because an
application run is filling or reviewing a form. Documents likewise appear in
Work when being edited/viewed and in Evidence when they support a claim.

**Browser is not nested inside Work and is not a permanent fifth tab.** It is a
conditional contextual destination entered only after the founder explicitly
selects the workspace-header **Open Browser** control/a Browser reference or explicitly starts work that
creates an actual browsing run. While Browser is open, it takes over the entire
right contextual workspace and hides the Work/Evidence/Decisions/Activity tab
strip so the browser cannot be mistaken for a cramped nested panel. Conversation
remains visible alongside on desktop. One clear **Back to workspace / Close
Browser** control exits the takeover and restores the prior selected tab, tab
subfocus, scroll, mode, and normal Panel width exactly. A model suggestion,
background update or retained receipt never auto-opens it.

### 9.2 Closed, Quiet, and Panel behavior

- Closed by default for a new general conversation.
- Immediately beneath the Conversation header, one compact icon/count button
  named **Work in this session** toggles the inline session-work list. It never
  opens the right workspace or whichever tab was last selected.
- Quiet renders only the 40–48 px edge handle when passive context or a pending
  indicator is useful. It contains no contextual content.
- Panel opens when the founder explicitly selects a contextual anchor, Alex
  begins visible non-Browser work under the opening preference, or a decision
  requires attention.
- Browser Takeover opens only from the explicit Browser action described above.
  If a run remains active after the founder closes it, the Quiet handle shows a
  truthful live-browser cue; frames do not force the takeover open again.
- Safe background work may update the Quiet edge handle without entering Panel.
- The founder’s manual collapse is respected. A new critical item updates the
  Decisions count and announces availability; it does not repeatedly reopen.
- Tab state is visible from text/count/icon, never color alone.
- The Work-in-this-session count equals its visible deduplicated session-work
  rows. Pending approvals and active voice status remain independent.
- Opening/collapsing the inline list preserves the transcript scroll anchor and
  does not change the active session or focused canonical resource.
- The collapsed row and expanded list share the centered conversation/composer
  logical edges. The list wraps and is never a floating narrow strip or a
  horizontally scrolling carousel.

### 9.3 Cross-tab focus contract

Every contextual anchor carries a typed focus target:

```text
workspace_destination: WORK | EVIDENCE | DECISIONS | ACTIVITY | BROWSER
presentation_mode: TABBED_CONTEXT | BROWSER_TAKEOVER
restore_tab: WORK | EVIDENCE | DECISIONS | ACTIVITY | null
restore_subfocus: optional stable typed target
restore_normal_panel_width: optional clamped CSS px
resource_kind: closed server-defined enum
resource_id: opaque id
run_id: optional authorized run
subfocus: optional closed enum or citation id
projection_version: last committed version
```

The client may request focus but cannot invent authority or retrieve a resource
outside the active actor/workspace/run scope. Missing, stale, deleted, or
unauthorized targets render a bounded error without revealing existence.
`BROWSER` is eligible only for explicit founder-open intent backed by an
authorized request/run/receipt. Client presentation state cannot create a run.
Entering it snapshots the tab/subfocus/scroll/mode/width restoration state;
exiting restores that snapshot after current authorization and projection
validation, falling back safely to Work if the former target no longer exists.

---

## 10. Browser behavior

### 10.1 Placement and lifecycle

**Existing:** Browser shares the left cell with Pipeline.

**Proposed:** Browser is its own conditional takeover of the right contextual
workspace, not a permanent tab and not a nested Work section.

- Work contains work items only. Browser never renders a launcher/card inside
  Work and never occupies the persistent tab bar. A small, keyboard-operable
  **Open Browser** control in the contextual-workspace header is the sole idle
  entry point. It exposes a non-color accessible active/no-active-run label but
  renders no empty viewport, screenshot frame, loading shimmer, fake tab, or
  activity claim.
- Selecting **Open browser** or a Browser reference enters Browser Takeover,
  snapshots the current workspace state, and hides the persistent tab strip. It
  may present the audited request/goal entry state, but it does not say
  Opening/Active until a server-owned browser run exists.
- When an explicit founder Browser request starts an allowed run, Browser
  Takeover becomes the dedicated right contextual surface. This initial
  focus does not insert or resize content inside the transcript. If the founder
  later closes/collapses it, subsequent frame updates respect that choice and
  update only the Quiet live-browser indicator until reopened.
- The stage has a strong viewport boundary and readable minimum size. Its header
  always shows the trusted page title when available, normalized host/origin,
  run goal/kind, freshness, and last action; a blank or unknown title is
  explicitly labelled rather than presented as empty space.
- **Stop** requests the existing idempotent browser-run stop/cancel path and is
  available whenever the projection says the run is cancellable. **Close** only
  closes the presentation; if browsing continues, its copy says so and the
  Quiet live-browser indicator remains. The single **Back to workspace / Close
  Browser** control restores the exact previously selected persistent tab,
  subfocus, scroll, mode, and normal Panel width without ending the run.
- The stage shows authenticated, ordered screenshots and remains a watch/control
  surface, not VNC, iframe, DOM takeover, or direct navigation surface.
- Typing a URL creates the normal audited request to Alex. It never navigates
  the panel directly.
- External links stay in the contained app path; OAuth consent is the reviewed
  exception.
- The last committed screenshot and summary remain after close until dismissed
  or replaced.

### 10.2 Entering Focus Stage

Browser may enter Focus Stage when:

- Alex is actively navigating/filling and the default right width makes the
  page illegible;
- the founder selects “Focus browser”; or
- a form/document review requires side-by-side evidence.

In Focus Stage, conversation remains available in the same comfortable Alex pane with
last messages and interaction controls. If voice is active, the same cloud and
captions remain; otherwise no cloud placeholder appears. The browser
does not obscure an approval: an exact approval opens in Decisions beside the
stage or as a full review sheet on smaller screens.

Entering Focus Stage is founder-selected except when the browser cannot meet its
documented minimum readable viewport in Panel, in which case the UI offers—not
silently performs—**Focus browser**. Closing Focus Stage returns to the same
Browser Takeover/run and restores the conversation scroll/draft. Workspace entry remains the
single discoverable entry point and shows an active-browser text/icon cue while
the browser is closed or Quiet.

### 10.3 Browser states

| State | Required UI |
|---|---|
| Inactive | Small contextual-workspace-header **Open Browser** control only; Browser is absent from Work and the tab bar, with no viewport placeholder or browsing claim. |
| Request ready | Goal/allowed destination summary and explicit start/request action; no live badge before a run receipt. |
| Opening | High-contrast skeleton shaped like the viewport, named host/goal, elapsed connection status, Close, and Stop if cancellable. It resolves to Active or a bounded error. |
| Active | Readable latest frame, trusted page title and normalized host, live text/icon badge, action summary, ordered frame strip, Stop, and Back to workspace / Close Browser. |
| Blocked | Last frame, safe reason, what the founder can do, no guessed workaround. |
| Stopping | Prior frame retained, controls disabled except safe navigation. |
| Closed | Compact retained final title/frame summary; Dismiss, Back to workspace, and reopen-in-context actions. Never label it active. |
| Stream dropped | Last committed frame marked stale, Reconnect/refresh projection; never “Alex is stuck.” |
| Uncertain external effect | No retry action until reconciliation; focus Activity/Decisions. |

Browser screenshots never appear as decorative thumbnails outside Browser/Evidence,
and credential-entry frames follow the existing redaction/no-capture boundary.
Every frame/action/status derives from the authenticated browser projection and
retains run/receipt provenance. Typed URLs remain requests to Alex under the
existing allowlist/network policy; Browser visibility never grants navigation,
credential, submission, approval, or effect authority.

### 10.4 Responsive Browser behavior

- At ≥1280 px, Browser Takeover defaults to
  **`min(40vw, 720px, calc(100vw - 624px))`**, approximately two-fifths of the
  desktop while reserving the 64 px rail and at least 560 px for conversation.
  Its draggable divider clamps between 480 px and
  **`min(60vw, 880px, calc(100vw - 624px))`**. The conversation remains
  alongside with its transcript/draft/scroll intact. Focus Stage remains an
  explicit founder choice for browser-heavy work and preserves the same
  conversation at roughly two-fifths of usable width (480 px minimum).
- Browser width is stored separately from normal Panel width. Closing restores
  the prior 420–600 px Panel width and exact persistent tab state; reopening
  restores the founder's last Browser width after clamping it to the current
  viewport. Width is presentation preference only and never crosses workspace/
  actor scope.
- At 900–1279 px, Browser opens as a dedicated contextual route/surface rather
  than a squashed panel or overlay covering the transcript. Close/Back restores
  the exact conversation scroll/focus and
  leaves a truthful Quiet status if the run remains active.
- At 600–899 px and below 600 px/coarse pointer, Browser is a full-screen
  contextual route with sticky title/host, Stop, Back to workspace / Close
  Browser, and Back to
  conversation. Returning restores the prior transcript/work anchor; an active
  run remains visible through Context/Activity rather than a floating viewport.
- At every width, loading/error/empty text remains readable at 200% zoom and the
  screenshot can pan/zoom inside its own bounded viewport without body
  horizontal scroll.

---

## 11. Main founder workflow surfaces

### 11.1 Runs home

Runs is an inspectable library, not a kanban permanently occupying the product.
The implemented page filters by workflow family and shows objective, family,
runtime status, update time, and the relevant permission boundary. Status,
needs-you, waiting, failed/uncertain, and date filters remain additive scale-up
work once run volume warrants them; the page must not manufacture those values.

The implemented global surface consumes one minimal, server-authorized `RunSummary`
projection per admitted work family. It does not synthesize active research,
Hiring, or background-job cards and never infers status from chat or client
state. Selecting a Hiring summary deep-links into the authorized role cockpit
rather than importing Hiring evidence into Runs. Candidate and onboarding runs
are excluded from the global projection; actor-private skill runs are returned
only to their originating actor.

Funding discovery and an application may render as one journey card while
preserving distinct run identifiers, event logs, plans, evidence, and authority.
Selecting a card focuses the relevant contextual tab and offers “Discuss with
Alex” without creating a second source of truth.

### 11.2 Application journey

Recommended mapping:

- **Work:** selected opportunity, interview checklist, live draft editor, form
  map/fill report, browser, generated application pack.
- **Evidence:** programme sources, cited requirements, Founder Profile facts,
  prior feedback/adaptation note, evidence-check findings.
- **Decisions:** opportunity selection, section feedback/edits, unresolved
  conflicts, exact submit approval.
- **Activity:** discovery/match receipts, draft versions, fill actions, waits,
  submit receipt, follow-up events.

When the founder is simply talking, none of these surfaces must be open. When a
draft is ready, Alex can say “I drafted the traction answer” and offer one
context anchor; it should not dump the entire review pane into the transcript.

### 11.3 Work renderer selection

Renderer selection is deterministic from focused resource type and committed
domain state, not chosen by model prose. The server provides the allowed
renderer kind; the client maps it to a component. Unknown kinds degrade to a
read-only summary plus Activity link.

---

## 12. Hiring Operations workspace

### 12.1 Product boundary

Hiring is a dedicated route/workspace because it contains:

- months-long RoleRun → CandidateRun → OnboardingRun hierarchy;
- restricted identity and evidence zones;
- human employment decisions that no model may make;
- role policy versions and impact analysis;
- mailbox, connector, wait, retention, and uncertain-effect health;
- causal timelines that must survive every conversation and device.

It is not a chat tab, a generic ranked pipeline, or a set of messages. It shares
Alex’s shell and interaction language without sharing unrestricted context.

Current production restrictions remain visible. Synthetic/test-only modes must
carry a persistent badge and may never be presented as live Hiring capability.
This design proposal does not activate H4–H7 or real candidate processing.

### 12.2 Shared shell, entry, and navigation

Hiring is a domain workspace inside the product shell, not a separately themed
page. It consumes the same shared semantic-token definitions, typography scale,
spacing/radius/elevation rules, generated Phosphor sprite, focus treatment,
rail, header, buttons, tabs, badges, cards, tables, sheets, and empty/error
patterns as Alex and Runs. Copying similarly named CSS variables into a
page-local palette or layering a second navigation/header over the product shell
does not satisfy this contract. The target implementation has one shared style
source and one responsive shell composition; Hiring adds only domain renderers.

- Select **Hiring** from the rail/bottom navigation.
- Landing page shows role cards with role title/code, age, target/headcount,
  domain and runtime state, candidate counts by human-controlled stage, open
  decisions, inbox items, uncertain effects, next wake, and last event.
- “Start a hiring run” begins from a founder objective in Alex or a dedicated
  button that compiles to the same typed command. It does not publish, contact,
  rank, or decide.
- Opening a role yields `Hiring / <role>`; opening a candidate yields
  `Hiring / <role> / <candidate code>`.
- Candidate identity is collapsed by default. The title and URL use pseudonymous
  code until an authorized, fresh-auth reveal.
- The role index is Hiring's main content, not a permanent second sidebar beside
  the product rail. After a role is selected, the shared breadcrumb/header
  carries a compact role switcher that returns to the role index and preserves
  its filters and scroll position.
- The header uses the same height, spacing, title hierarchy, workspace disclosure,
  Decisions/activity cues, account controls, and responsive behavior as Alex
  and Runs. A persistent Synthetic/Test marker is context metadata, not a
  second branded masthead or repeated card.
- No role selected means no scoped Alex dock, scope pill, empty chat card, or
  contextual workspace. The useful safe state is the role index plus one clear
  **Start a hiring run with Alex** action. That action opens a bounded new-role
  objective flow and does not pretend a role-bound conversation exists before a
  durable role/run is created.

### 12.3 Role cockpit layout

At desktop width:

```text
┌──────┬───────────────────────────────────────────────┬──────────────────┐
│ rail │ role header: state · next wake · health · controls              │
├──────┼───────────────────────────────────────────────┬──────────────────┤
│      │ role cockpit                                  │ contextual       │
│      │ stage summary / role policy                   │ surface          │
│      │ unranked candidate groups/table               │ scoped Alex OR   │
│      │ waits / mailbox / policy / effect health      │ Work/Evidence/   │
│      │                                               │ Decisions/Act.   │
└──────┴───────────────────────────────────────────────┴──────────────────┘
```

The center cockpit includes:

- role header and current Role Contract/policy version;
- Day N/active duration, runtime state, active worker count, dormant count;
- current next wake and any blocked/uncertain condition;
- unranked candidate board/table grouped by committed domain state;
- role mailbox health based on watch/cursor/reconciliation/probe evidence;
- publication package and attributed manual receipt/verification distinction;
- policy, notice, retention, connector, and action health summaries;
- clear open Decisions count.

This is a responsive information layout, not a stack of equally weighted cards.
Use one quiet summary strip for role/runtime/next-wake facts, one prominent
attention region only when judgment or reconciliation is required, and
progressive sections for the candidate board and operational health. Empty
space is intentional only after useful state and next action are clear; a large
blank cockpit containing generic chrome is not an acceptable safe state.

The candidate table uses the same criterion-neutral order for everyone. It does
not show a total score, fit percentage, leaderboard, personality inference,
school/employer prestige proxy, model cutoff, or “recommended hire.”

### 12.4 Role contextual tabs

- **Work:** Role Brief, Hiring Scorecard, Interview Plan, Public Job
  Description, policy diff/impact view, manual publication handoff, bounded
  capability panel.
- **Evidence:** source policy, approved job-related criteria and rationales,
  publication/mailbox receipts, role-level evidence; never cross-candidate
  comparison prose.
- **Decisions:** exact Role Contract approval, policy change decision, founder
  publication acknowledgement, open candidate decision list, exact test/live
  actions allowed by the active reviewed policy.
- **Activity:** role events, waits, child-run creation, connector health,
  external action state, uncertainty, retention obligations.

Policy approval is disabled until exact impact enumeration is complete. The
surface explicitly states that a policy update neither contacts nor decides any
candidate.

### 12.5 Candidate workspace

Selecting a candidate opens a full record workspace, not a modal over the role
page. The header uses candidate code and a collapsed identity control.

- **Work:** current human-review task, structured decision form, interview plan
  or offer/data-control task if that separately reviewed stage exists.
- **Evidence:** Evidence Passport in fixed criterion order with citations,
  unknowns, contradictions, staleness, policy version, and source boundaries.
- **Decisions:** append-only human decision history; one current
  `ADVANCE | HOLD | REQUEST_EVIDENCE | DECLINE` control with job-related reason,
  reviewed evidence IDs, and optional note; separate exact approvals for any
  candidate communication or Calendar effect.
- **Activity:** causal sequence of application receipt, safe ingestion, evidence
  artifact, decisions, waits, messages, interviews, references, offer, data
  rights, and receipts.

Evidence never includes protected/prohibited fields. Identity reveal, export,
deletion, role closure, offer, and other high-authority actions follow fresh-auth
and exact-control requirements from the Hiring contract.

### 12.6 Contextual conversation tied to a run

Hiring offers **Discuss with Alex** from the selected role header, candidate
header, and an authorized Work/Evidence item. It opens the same shared
conversation component in a compact contextual Alex dock with a visible scope
pill:

- `Role: Forward Deployment Engineer`
- `Candidate: C-014 · identity hidden`

The server issues/validates the scoped conversation binding. On every turn:

- Alex receives only the minimum authorized role or single-candidate view;
- answers cite committed record references and list unknowns;
- generic Founder Profile/company memory and unrelated candidates are excluded
  unless an explicit policy permits a particular fact;
- changing selection invalidates or replaces the scope before the next turn;
- voice and text use the same scope;
- live captions remain available;
- raw audio is ephemeral under the Hiring contract;
- “approve,” “advance,” “decline,” “send,” or “book” in speech/text only focuses
  the appropriate server-rendered control.

Scope determines the minimum view Alex may receive:

- **Role scope:** the selected Role Contract/policy, runtime/waits, aggregate
  fixed-stage counts, role-level evidence and receipts, and open decisions. It
  excludes candidate evidence/identity and may not compare or recommend
  candidates.
- **Candidate scope:** the parent role policy plus that one candidate's
  pseudonymous, authorized Evidence Passport, unknowns, contradictions,
  staleness, human decision history, waits, and receipts. It excludes every
  sibling candidate and does not inherit a transient identity reveal unless a
  separately reviewed server policy explicitly permits the exact fact.
- **Work-item scope:** the exact selected authorized item plus only the minimum
  parent role/candidate projection required to explain it. It cannot widen the
  underlying role/candidate binding.

The contextual right region has two mutually exclusive presentation modes:
**Scoped Alex** and **Workspace**. Opening **Discuss with Alex** shows the scoped
transcript/dock. Opening the contextual workspace, a document/evidence reference, a
decision cue, or an Activity receipt yields the region to the shared
Work/Evidence/Decisions/Activity workspace without ending the conversation. A
compact **Return to Alex — <scope>** control preserves transcript scroll, draft,
unread count, captions preference, and active voice lifecycle. Returning to the
cockpit may close the right region, but the authorized scoped conversation
persists; entering Work Focus Stage keeps the same conversation in its compact
dock. There is never a second mini transcript.

Selecting a different role/candidate immediately retires the old scoped token
and clears its rendered evidence before the new projection appears. The new
scope requires a server-issued binding. Per-scope transcript, draft, scroll, and
workspace tab may be restored only after current authorization succeeds; a
draft is never carried into another candidate. Back navigation returns to the
prior authorized scope and restores the cockpit/list scroll without replaying
or merging conversations.

The scoped transcript follows §6.2 exactly. User/Alex turns append at the
bottom; a generated document may have one compact reference inside its exact
creating/first-discussion assistant turn and the same canonical Work item. Role
or candidate projections never inject detached task/artifact cards into chat.
While older history is visible, arrivals preserve scroll and use the shared
new-message/new-work affordance.

Ordinary scoped text conversation shows no cloud or decorative Alex object.
The §7 soft cloud appears only during an explicit active voice lifecycle, with
the same captions, reducer, reduced-motion, privacy, interruption, approval
modifier, and dismissal behavior as the main conversation. When no role/run is
selected, no voice control or cloud is shown because there is no valid scope.

Conversation may explain status, summarize evidence, draft a proposed action,
or focus a control. It cannot commit an employment decision, grant approval,
change recipient/time/terms, or claim an external effect succeeded.

### 12.7 Hiring surface and state matrix

| Context | Main surface | Contextual entry | Safe empty/loading/error behavior |
|---|---|---|---|
| Hiring home | Search/filterable role cards and meaningful counts | No scoped Alex; **Start a hiring run with Alex** starts bounded setup | Loading uses role-card skeletons; no roles explains what will and will not happen; access/config failure shows one stable recovery path. |
| Selected role | Role cockpit and fixed-order candidate groups | **Discuss with Alex** and **Open workspace** in shared header | No candidates names the trusted role-address wake; no role evidence/decision/activity uses the shared explicit empty copy; stale/error preserves last committed projection. |
| Selected candidate | Full-page routed candidate record, identity hidden | Candidate-scoped **Discuss with Alex** and Context | Missing/withheld evidence remains UNKNOWN with reason; deleted/unauthorized scope clears sensitive content and returns safely to the role. |
| Selected work/evidence/decision | Shared Work/Evidence/Decisions/Activity renderer | **Ask Alex about this** binds the exact item within parent scope | Missing/stale item shows safe status and Activity route; consequential control never falls back to chat. |
| Active scoped voice | Same scoped conversation plus conditional voice stage | Voice controls in the shared interaction dock | Reconnect/error preserves transcript and never changes scope or resumes media silently; ending/dismissing voice removes the cloud. |

Pending Decisions and uncertain effects appear as text/icon/count cues on the
role card, role/candidate header, workspace control, and appropriate tab without
duplicating the underlying object. A cue may focus the exact server-rendered
control; it cannot resolve it. Activity uses the shared causal grammar and never
confuses artifact generation with a completed transition or external effect.

### 12.8 Responsive Hiring composition

- At ≥1280 px, use the 64 px product rail, one dominant role/candidate main
  surface, and an optional Quiet/420–600 px Panel. Do not add a permanent role
  sidebar that recreates a three-column admin console.
- At 900–1279 px, role/candidate content is the one main surface; Scoped Alex or
  Workspace opens as the shared right sheet, with only one mode visible.
- At 600–899 px, Hiring home, role, and candidate are separate routes. The role
  switcher opens a sheet/list; Context and Scoped Alex are full-height sheets.
- Below 600 px/coarse pointer, the shared bottom destinations remain fixed.
  Hiring uses full-screen role and candidate routes; Scoped Alex and Context are
  full-screen layers with explicit Back labels that restore filter, transcript,
  and record scroll/focus. Candidate tables become grouped semantic rows without
  changing order or introducing rank.

---

## 13. Decisions and approval UX

### 13.1 One shared Decisions model

Decisions contains three visibly different object types:

1. **Review choices** — e.g. choose an opportunity, approve/edit/reject a draft
   section, resolve a factual conflict.
2. **Human domain decisions** — e.g. candidate ADVANCE/HOLD/REQUEST_EVIDENCE/
   DECLINE or approve a new role policy.
3. **External-action approvals** — e.g. submit a form, send email, invite/cancel,
   publish through an enabled official connector, send an offer.

Their controls can share visual primitives but must not share misleading copy.
“Approve draft” is not “Approve & submit,” and a candidate decision is not
permission to email the candidate.

### 13.2 Approval-fatigue policy

Use the following test:

| Action | Prompt behavior |
|---|---|
| Conversation, analysis, captions, research, drafting, advisory output | No approval. |
| Microphone/camera/display-source consent | A valid source-class/session grant avoids repeated disclosure only; each initial start/resume/restart still requires an intentional founder gesture and the applicable start authorization. Persistent cue and easy Stop remain mandatory. |
| Founder directly edits, selects, gives feedback, or changes a reversible preference | The control itself authorizes the change; no second modal. |
| Durable human domain decision | One exact server-rendered commit control with preview/attribution; no redundant confirmation after it. |
| Externally visible, irreversible, reputation-bearing, financial/legal, or otherwise consequential effect | One exact, single-use approval with clear preview. |
| Payload/recipient/account/time/attachment/policy changes after approval | Prior authority becomes stale; show diff and require one fresh approval. |
| Uncertain provider result | No blind retry; reconcile or require an authorized resolution. |

Alex may say naturally, “This needs your approval before I send it.” That phrase
may open/focus Decisions. Only the visible server-rendered approval control can
grant authority.

### 13.3 Exact approval card/sheet

The exact approval surface shows, from server data:

- action verb and consequence in plain language;
- target, recipient/attendees, destination, account/company identity;
- exact content or normalized payload preview, attachments, and version;
- run/entity/policy context;
- what will **not** happen;
- expiry and single-use rule;
- material change summary since the last review;
- effect class and whether fresh authentication is required;
- **Approve exact action** and **Not now/Deny**.

The approval button uses consequence-specific copy: “Submit application,”
“Send this email,” “Create this invite,” never a generic “Confirm.” A dangerous
or irreversible action retains the heavier dialog/sheet treatment from the
design system. Routine durable decisions may remain inline in Decisions if all
required preview information is visible.

### 13.4 Decision inbox

Global Decisions groups:

- needs action now;
- expiring soon;
- blocked by stale evidence/auth/policy;
- completed/denied/expired receipts.

It supports domain, urgency, and run filters. Candidate details remain redacted
in the global list unless the actor opens the authorized Hiring context. Counts
deduplicate the same underlying approval/decision across banners, digest, and
run views.

Resolution states are `pending`, `claiming`, `granted`, `denied`, `expired`,
`stale`, `executing`, `succeeded`, `failed`, or `uncertain` as supported by the
server contract. UI never collapses “granted” and “succeeded.”

---

## 14. Activity and durable state presentation

### 14.1 Activity line

The interaction dock carries one collapsible row using the current vocabulary:
Working, Queued, Waiting on you, Waiting on the world, Done, Failed/uncertain.
It delays brief work to avoid flicker, exposes elapsed time only after five
seconds, and uses glyph/text as well as color.

### 14.2 Activity tab

Activity is a causal product timeline, not a raw developer audit dump. Each row
has:

- committed sequence/time;
- actor or trusted source;
- founder-facing action/event name;
- affected run/entity;
- status and uncertainty;
- receipt/evidence anchor where permitted;
- resulting state or next wake.

Causal groups render in this order: authoritative event/decision/approval/
manual receipt → committed transition/wait resolution → derived artifact or
follow-on task. A generated artifact never proves its own cause.

Technical IDs remain available in an expandable details region with mono type.
Sensitive source contents, approval tokens/hashes, credentials, raw candidate
text, and URL secrets never enter timeline payloads.

---

## 15. Component and state contracts

These are experience contracts, not authorization to create schemas or APIs.
Names are illustrative and must map to reviewed server projections during
implementation.

### 15.1 `AlexVoiceCloudView`

Required fields:

```text
lifecycle: OFF | CONNECTING | ACTIVE | RECONNECTING | TERMINAL_ERROR
visible: boolean derived only from lifecycle
base_state: CONNECTING | LISTENING | MICROPHONE_OFF | THINKING |
            PROCESSING | SPEAKING | AWAITING_APPROVAL |
            RECONNECTING | ERROR
transition: NONE | INTERRUPTED
approval_modifier: NONE | PENDING
mic_state: OFF | LIVE | MUTED
state_label: server/client-safe founder copy
activity_label: optional closed-vocabulary copy
outgoing_level: optional bounded 0..1 ephemeral playback level
input_level: optional bounded 0..1 ephemeral listening level
captions_state: EXPANDED | COLLAPSED | OFF
reduced_motion: boolean from user/OS preference
projection_revision: monotonic trusted revision
```

This is the shared-shell projection shape, not a second event reducer or media
protocol. Doc 38 owns how trusted events produce these values. `visible` is
false in ordinary text chat and vision-only use, regardless of global activity
or approvals. A pending
approval composes with Speaking and cannot suppress audio-level response; it
becomes the base only when the reducer selects Awaiting approval. Still images
remain attachments. Live vision uses its separate source-chip/card projection.
Client code may derive presentation from the projection but may not infer
approval, effect success, or workflow status from it.

### 15.2 `WorkspaceView`

```text
active_tab: WORK | EVIDENCE | DECISIONS | ACTIVITY
mode: CLOSED | QUIET | PANEL | FOCUS_STAGE
content_mode: TABBED_CONTEXT | BROWSER_TAKEOVER
browser_run_id: optional authorized opaque id
browser_width_css_px: optional clamped presentation preference
restore_tab: optional WORK | EVIDENCE | DECISIONS | ACTIVITY
restore_subfocus: optional stable typed target
restore_normal_panel_width_css_px: optional clamped presentation preference
focus_target: typed authorized target or null
renderer_kind: closed enum supplied by projection
projection_version: integer/string
freshness: CURRENT | STALE | RECONNECTING
```

Quiet means the 40–48 px edge handle only; Panel means the 420–600 px contextual
surface; Focus Stage means the explicit temporary Work-favoring layout with a
comfortable, same-conversation pane. These
meanings do not vary by domain or breakpoint.

`BROWSER_TAKEOVER` is the one reviewed renderer-specific exception to normal
Panel width: it replaces the right workspace's tab strip/content, uses §10.4's
responsive Browser width, and leaves conversation alongside on desktop. It does
not create a fifth persistent tab or alter the meaning of Panel for other
content. Exit restores the captured tab/subfocus/scroll/mode/normal width after
validation. Browser width and restoration fields are presentation only.

An unknown renderer never executes arbitrary markup/code. It falls back to a
safe read-only summary.

### 15.3 `ConversationContextView`

```text
session_id: opaque id
conversation_title: safe text
focused_run_id: optional opaque id
focused_entity: optional authorized kind/id/code
scope_label: founder-facing text
scope_kind: GENERAL | RUN | HIRING_ROLE | HIRING_CANDIDATE
scope_expiry: optional timestamp
read_only: boolean
```

Changing focus and changing conversation are separate commands. Hiring scope is
server-derived and must fail closed on expiry or authorization change.

The inline Work-in-this-session disclosure derives from a separate presentation model:

```text
expanded: boolean
item_count: visible deduplicated session-work row count
restore_scroll_anchor: ephemeral local anchor, never durable authority
restore_focus_target: optional stable DOM/control key
```

The button exposes `aria-expanded`/`aria-controls`; opening it focuses the
first inline work row when present, and collapsing returns focus to the button.
contextual workspace without changing conversation identity, and closing/back
restores the transcript anchor and prior focus. `has_pending_approval` and
`has_active_voice` require independent visible routes/status and may not be
represented only by the collapsed count.

The transcript projection also carries stable chronology and reference identity:

```text
turns: ordered committed event IDs
latest_committed_event_id: opaque monotonic/session-ordered ID
artifact_references: event ID -> immutable Work item/artifact IDs
is_following_latest: ephemeral local boolean
unseen_message_count: ephemeral non-negative integer
unseen_work_count: ephemeral non-negative integer
restore_scroll_anchor: ephemeral event ID plus local offset
```

The server-owned event order is authoritative. A document reference is eligible
only in the assistant event that created or first discussed it and resolves to
the same authorized canonical Work item. A projection refresh may update the
reference's processing/status label in place but cannot relocate it, create a
second transcript node, or append it after later events. Scroll-follow and
unseen counts are presentation state and authorize nothing.

### 15.4 `DecisionView`

```text
decision_id: opaque id
kind: REVIEW | DOMAIN_DECISION | EXTERNAL_APPROVAL
status: closed server enum
summary: safe founder copy
exact_preview: server-rendered structured fields
authority_requirements: role/fresh-auth/expiry information
material_change: optional structured diff
allowed_resolutions: closed server enum list
projection_version: last committed version
```

The browser never sends `approved_by`, a payload hash of its own making, or an
arbitrary target. It sends a closed resolution, idempotency key, and expected
version for the server-selected object.

### 15.5 Universal rendering rules

- Server projections and receipts are authoritative.
- Mutations carry idempotency and expected version; losing races return a
  conflict and refresh the prior committed view.
- While a mutation is in flight, show neutral pending state; do not advance
  domain state optimistically.
- SSE/event streams update bounded projections; disconnects retain the last
  good view and mark freshness.
- Empty, loading, failure, unauthorized, and deleted are explicit states, not
  variants of an empty array.
- Every state badge includes text and icon/shape; color is redundant.

---

## 16. Loading, empty, waiting, error, and recovery states

### 16.1 Loading

- First paint uses skeletons shaped like the actual renderer and marked
  `aria-hidden`, while the container is `aria-busy`.
- Subsequent refreshes preserve the last committed view at full contrast and
  add a small refreshing/reconnecting cue.
- The voice cloud's Thinking state is not a universal loading indicator. It is
  visible only during a bounded active voice turn. Static data loading uses the
  surface skeleton.

### 16.2 Empty

Every empty state answers: what this is, why it is empty, and the one action
that resolves it.

Examples:

- Conversation: “Alex is ready — tell me what outcome you want.”
- Runs: “No runs match these filters.”
- Work: “Nothing is open. Alex will show the active draft, browser, or task
  here.”
- Evidence: “No evidence is linked to this focus yet.”
- Decisions: “Nothing needs your judgment.”
- Activity: “Committed work and receipts will appear here.”
- Hiring: “No hiring roles yet. Start with a role objective; nothing will be
  published or sent.”
- Candidate group: “Dormant — waiting for a trusted role-address application.”

### 16.3 Waiting and dormancy

The UI says what is awaited, who/what can wake it, and the next check if one is
authoritatively known. It explicitly says when nothing is running. It never
invents a date or animates a dormant process as though a worker were active.

### 16.4 Errors

Errors use safe actionable categories:

- message not sent;
- media permission denied/revoked;
- connection dropped;
- stale projection/conflict;
- source unavailable;
- browser blocked;
- effect failed;
- effect uncertain/reconciliation required;
- authorization or fresh-auth required;
- record removed/not found.

Each provides the next safe action. Retry appears only for idempotent/reversible
operations. An uncertain external effect never offers a blind retry. The last
good committed data remains visible unless policy requires redaction.

---

## 17. Responsive and smaller-screen behavior

### 17.1 Breakpoints are behavioral

Recommended starting ranges, to validate with content and zoom:

| Width | Behavior |
|---|---|
| ≥1280 px | Rail + dominant center + optional right workspace. Focus Stage may swap work/compact-dock proportions. |
| 900–1279 px | Rail + one main surface. Contextual workspace is a right sheet; conversation remains beneath or in compact dock. |
| 600–899 px | Rail may reduce to 56 px; workspace/Hiring detail opens full-height sheet. Split views are avoided. |
| <600 px / coarse pointer | Bottom destination bar; single full-screen route; dock above safe area; contextual tabs become a full-screen workspace with sticky tab bar. |

At 200% zoom, the interface must behave like the corresponding narrow layout,
not squeeze three surfaces side by side.

### 17.2 Mobile conversation

- Ordinary mobile text chat has no cloud or reserved cloud space. During an
  active voice lifecycle, the cloud uses a fixed 72–80 px box above the compact
  captions/status and never condenses in response to transcript scrolling.
- Collapsing captions never removes an otherwise active cloud; ending or
  dismissing voice removes both according to lifecycle.
- The compact Work-in-this-session icon/count button remains immediately below
  the Conversation header.
  Its row shares the transcript/composer logical edges and is collapsed by
  default.
  It reveals a wrapping inline list in the chat column, not a workspace route;
  collapsing restores the exact transcript scroll anchor and button focus.
- Messages retain the same bottom-append chronology as desktop. Compact document
  references remain inside their originating assistant turn, wrap without
  horizontal scrolling, expose at least a 44×44 px activation target, and open
  the canonical Work item in the full-screen workspace. Back restores the
  originating turn and offset. While older history is visible, arrivals update
  a **New messages**/**New work** affordance instead of moving the viewport.
- Interaction dock honors safe-area insets and keyboard height.
- Bottom destinations: Alex, Runs, Hiring, Decisions, More. Activity and Search
  live under More; these five visible destinations are fixed for the proposed
  mobile architecture. The one filled **New** session action is a distinct
  creation control beside them, not a sixth destination.
- A pending decision may add a badge but cannot replace the composer.

### 17.3 Mobile contextual workspace

- Opens as a full-screen route/sheet with Back to conversation.
- In Quiet mode its entry affordance is a compact 42 px top-inline-end button;
  it must not paint a full-height strip over the conversation or composer.
- Tabs remain Work/Evidence/Decisions/Activity and use standard horizontal
  keyboard/touch behavior.
- Browser fills the sheet; **Back to conversation** restores the transcript
  anchor without losing the browser run. If voice is active, its compact
  cloud/caption stage remains reachable; otherwise no voice affordance is
  fabricated.
- Exact approvals become a full-screen review with sticky action footer.

### 17.4 Mobile Hiring

- Hiring home is a role list with meaningful counts and next wake.
- Role cockpit sections become progressive disclosures, not one very long card
  stack.
- Candidate selection opens a dedicated record route; no modal.
- Identity reveal and high-authority actions require the same fresh-auth policy
  and automatically re-collapse on the reviewed timeout.
- Candidate tables transform into grouped rows/cards without changing order or
  inventing ranking.

---

## 18. Accessibility contract

### 18.1 Keyboard and focus

- A skip link reaches main conversation/cockpit content.
- Rail uses one tab stop with arrow-key navigation or a conventional list of
  links; the chosen pattern must be consistent and documented.
- Workspace tabs follow ARIA tab behavior with roving `tabindex`, arrows,
  Home/End, and associated tabpanels.
- Splitters are keyboard operable in fixed steps and expose value/min/max.
- Opening a sheet/dialog moves focus; closing restores it to the invoking
  control. Focus is never sent to the voice cloud solely because its visual state
  changed.
- The Work-in-this-session button exposes name, visible-row count, expanded
  state, and its controlled inline region; collapsing restores the prior
  transcript scroll anchor and focus without
  concealing an approval route or active voice status.
- New messages, decisions, and errors do not steal focus.

### 18.2 Semantics and announcements

- Voice-cloud state has one textual status node. Canvas and halo layers are
  hidden from assistive technology.
- Transcript uses a polite log; interim captions use a separate restrained live
  region and do not flood announcements.
- Compact document references are links or buttons with the document name,
  type/status when useful, and an accessible action such as **Open in Work**.
  New-message/new-work controls announce deduplicated counts without announcing
  projection refreshes or moving focus.
- Errors blocking the current action may use assertive status once.
- Runtime/domain/wait state are distinct in accessible names.
- Tables remain real tables where relationships matter; mobile card rendering
  preserves header/value semantics.

### 18.3 Visual and motor access

- Meet or exceed WCAG AA and the current contrast-script floors in both themes.
- Visible `:focus-visible` on every control.
- Never use color or motion alone for state.
- Minimum 32×32 px pointer targets; 44×44 px on coarse pointers.
- Message/caption text supports browser zoom and user text sizing without
  clipping.
- The transcript scrollbar belongs immediately beside the bounded conversation
  content grid, not at the distant pane/divider edge. Its
  visible thumb/track meets non-text contrast where author styling applies,
  retains the platform's usable pointer target (never a hairline custom drag
  target), and remains operable by wheel/trackpad, touch, Page Up/Down, Home/End,
  and keyboard focus without overlaying content.
- Captions expose speaker labels, adjustable size/background, and a persistent
  on/off/collapse control.

### 18.4 Reduced motion and sensory safety

- `prefers-reduced-motion` disables cloud deformation/energy response, looping
  pulses, spinner rotation, auto-scrolling animation, and panel motion while
  retaining instant state changes and one static silhouette per voice state.
- No state relies on shimmer. Skeletons become static tones.
- Outgoing-audio visualization is clamped to avoid flash and large luminance
  shifts.
- Audio never auto-starts after page load/reconnect; it resumes only within the
  active live conversation contract.

---

## 19. Privacy and trust cues

### 19.1 Media

- Exact live source name/type is visible whenever camera or a selected display
  surface is shared.
- Add image/Capture still, selected tab, selected window, camera, and whole
  screen are separate choices with separate copy. Still images are attachments,
  not live sources.
- Whole screen carries the strongest warning and visual cue.
- No background or hidden capture after Stop, track end, revocation, session
  end, logout, navigation outside the active live experience, or doc 38's
  hidden/background suspension boundary.
- Switching Hiring scope while live visual media is active stops or pauses
  delivery under doc 38. Sending into the new authorized scope requires a new
  founder Share/Resume gesture and fresh single-use start authorization.
- Doc 38 is the binding contract for source type, consent/revocation and share
  metadata, duration telemetry, bounded delivery, no-live-frame retention, and
  safe errors. This UI presents those trusted states and does not define or log
  a second telemetry/transport path.

### 19.2 Hiring

- Candidate identity collapsed/redacted by default.
- Restricted evidence cannot enter generic search, Founder Profile, company
  memory, unrelated runs, generic conversation, logs, metrics, or URLs.
- Public candidate notice remains read-only and separate from founder controls.
- Synthetic/demo mode is persistent and unmistakable.
- Conversation scope, actor, role/candidate code, and expiry are visible.
- Every identity reveal, decision, export/delete, approval, and effect has an
  attributed durable receipt.

### 19.3 Browser and approvals

- Browser shows audited screenshot artifacts only; arbitrary remote HTML is not
  embedded.
- Credentials, query secrets, form values, and restricted frame content follow
  current redaction/no-capture rules.
- Approval UI receives no raw token or client-authored authority field.
- “Approved” and “Succeeded” remain visually and semantically distinct.

---

## 20. Visual design direction

Evolve the current **Workbench** design system from “dense operations surface”
to **calm operational presence** without replacing its foundations.

Keep:

- cool blue-biased neutral surfaces in dark and light themes;
- semantic tokens only in components;
- the existing semantic color roles and tokens: agent action uses `--accent` /
  `--accent-ink`, founder attention uses `--attn` / `--attn-ink`, consequence
  uses `--danger` / `--danger-ink`, and success uses `--ok` / `--ok-ink`, with
  their existing background/on-color companions where defined;
- current type scale, tabular numerals, 68ch prose measure;
- generated Phosphor regular icon sprite;
- evidence-first typography, accessible contrast, and consequence-weighted
  elevation.

Change:

- reduce always-visible borders, cards, badges, and uppercase micro-labels in
  the conversation canvas;
- reserve dense inset-card grammar for records, evidence, and cockpit modules;
- create more tonal separation between calm conversation and operational Work;
- remove decorative star/sparkle identity marks and the always-visible orb;
- keep ordinary conversation visually clean through a single compact Work in
  this session disclosure rather than expanded session-work/return cards;
- use larger whitespace intervals and narrower prose rather than widening chat
  to fill all available space;
- treat the right workspace as an instrument drawer that can become a stage,
  not a permanent inspector.
- remove Hiring's page-local theme/font/navigation composition, permanent role
  sidebar, static Alex medallion, modal candidate record, and stacked-card
  adapter treatment; render its domain modules through the same shell and
  component grammar as Alex and Runs.

The voice cloud may introduce new **semantic** voice-status tokens (cloud fill,
halo, state mark) only after contrast, light/dark, grayscale, forced-colors, and
reduced-motion review. Primitive colors remain inaccessible to components.

Copy remains founder-facing: “Needs your approval,” “Waiting for the candidate
to reply,” “Alex is viewing your selected tab,” not enum/tool/model names.

---

## 21. Migration strategy from the existing UI

Migration must preserve existing work, deep links, server contracts, and safety
gates. It is staged and reversible; this document authorizes none of the stages.

### Phase UX0 — inventory and contract freeze

- Capture current desktop/mobile screenshots and keyboard paths for `index.html`
  and `hiring.html`.
- Inventory every endpoint, projection, DOM contract, approval path, browser
  control, activity/wait state, and Hiring action used by the UI.
- Add no new semantics; identify compatibility adapters and missing projections.
- Establish visual/accessibility baselines and event analytics without content.

### Phase UX1 — shared shell behind a workspace flag

- Introduce rail, context header, conversation canvas, contextual workspace,
  and shared interaction dock behind a workspace-scoped flag.
- Reuse existing conversation/session, Pipeline, review, document, approval,
  activity, and browser data sources through adapters.
- Keep the legacy shell available for rollback. Do not migrate authoritative
  state into client memory.

### Phase UX2 — contextual workspace mapping

- Map current draft review and “The form” content into Work/Evidence/Decisions/
  Activity.
- Replace expanded persistent session-work/return cards and the separate Open
  Work affordance with the inline Work-in-this-session disclosure after initial or
  return presentation. Preserve scroll/focus and independent approval/voice cues.
- Move Browser from the left cell into the explicit Browser Takeover state:
  never a nested Work section or permanent tab, hide/restore the persistent tab
  strip exactly, preserve separate normal/Browser widths, and retain the exact
  snapshot/SSE/Stop/network-containment contract.
- Measure discoverability, workspace reopening, decision completion, and
  conversation readability.

### Phase UX3 — voice cloud, captions, and explicit media contract

- Add the voice-only Canvas 2D cloud projection over doc 38's trusted reducer;
  do not add a second client reducer or media protocol. Ordinary text and
  vision-only use must reserve no cloud space.
- Validate lifecycle visibility, state precedence, playback-aligned outgoing
  audio response, smoothing, barge-in, captions, reconnect, pending-approval
  modifier composition, denied permission, render budgets, and reduced motion.
- Add camera and selected-display controls only through doc 38's reviewed
  disclosure, gesture, start-authorization, bounded-delivery, stop, privacy,
  retention, and error contracts. Add image/Capture still uses its separate
  attachment path. Existing microphone behavior must not be broadened
  implicitly.

### Phase UX4 — Hiring shell convergence

- Render Hiring home, role cockpit, and candidate workspace inside the shared
  shell using existing H0–H3 committed projections.
- Replace page-local tokens/header/rail copies and the permanent role sidebar
  with the actual shared shell/style source, role index, and compact role
  switcher. Keep one navigation/header hierarchy.
- Replace candidate modal with a routable dedicated record.
- Add Scoped Alex only on explicit role/candidate/work-item invocation through
  the reviewed server-bound conversation service. No role selected means no
  mini-Alex card, voice control, or placeholder.
- Implement one contextual region with mutually exclusive Scoped Alex and
  Workspace modes, preserved per-scope transcript/draft/scroll state, explicit
  Return to Alex/Back behavior, and safe scope invalidation.
- Apply §6.2 chronology and canonical Work-item identity to all Hiring document
  references; projection adapters may not detach or reorder artifact cards.
- Keep synthetic/test badges and all H4–H7 gates intact.

### Phase UX5 — legacy retirement

- Run parity, accessibility, privacy, and safety acceptance suites.
- Compare server receipts and projections between old/new surfaces for the same
  deterministic fixtures.
- Remove legacy layout only after measured adoption, rollback rehearsal, and
  explicit product/architecture/security approval.

No phase may combine a visual migration with activation of a new external
effect, candidate-data policy, approval weakening, or workflow transition.

---

## 22. Acceptance tests and evaluation criteria

### 22.1 Conversation-first layout

- [ ] At 1440×900 with contextual workspace Closed, the conversation canvas is
      the dominant surface and messages remain within 64–68ch.
- [ ] The default view does not present three equal columns or an always-open
      admin dashboard.
- [ ] Opening/closing Work preserves transcript, focus context, draft input,
      captions, and live session.
- [ ] Focus Stage is explicit and temporary, never the startup default, and
      keeps the same conversation at roughly two-fifths of usable desktop
      width with a 480 px hard minimum; narrower widths switch surfaces.
- [ ] Work in this session is one icon/count disclosure immediately below the
      Conversation header; it is collapsed by default and never opens a generic
      Context destination or last-selected workspace tab.
- [ ] It is keyboard operable, exposes the exact accessible name/count/expanded
      state and controlled inline list, and preserves transcript focus/scroll.
- [ ] Its control and expanded list align to the centered transcript/composer
      grid; items wrap with no squeezed carousel or always-on inner scrollbar.
- [ ] Founder and Alex turns append in durable transcript order at the bottom
      above the composer; projection refreshes never reorder committed turns.
- [ ] A generated document reference appears only inside the exact assistant
      event that created or first discussed it, shares identity with one
      canonical Work item, and opens/focuses that item without duplication.
- [ ] Artifact references are never appended later as detached cards, pinned,
      reordered, or expanded into persistent panels that displace unrelated
      messages; other durable work context remains in the contextual workspace.
- [ ] When the founder reads older history, new messages/work preserve the
      visual scroll anchor and expose a keyboard-operable, non-disruptive
      affordance; activating it and returning from Work restore predictable
      focus and chronology on desktop and mobile.
- [ ] The transcript scroller owns a stable reserved gutter at the conversation
      centered conversation grid's logical edge, immediately outside the
      64–68ch message measure and aligned with Work in this session/composer. Scrollbar
      appearance causes zero message/composer shift. The thin native indicator
      appears on scroll/hover/focus, stays visually quiet at rest, supports RTL,
      reduced motion, and platform input, and never overlays content.
- [ ] The rail provides a direct, labelled route to Hiring.
- [ ] Rail and composer glyphs match doc 16 §5.4.1 exactly; camera is a camera,
      screen sharing is a monitor/arrow rather than a globe, Hiring is a people
      group rather than a target, and every icon action has a visible label or
      accessible name/tooltip with keyboard focus and light/dark contrast.
- [ ] At exactly 1280 CSS px, the 64 px rail, ≥560 px center, and right
      workspace remaining-viewport cap produce no body horizontal scroll.

### 22.2 Voice cloud and captions

- [ ] Ordinary text chat, text reasoning, global approvals, background work,
      and vision-only use render no cloud, placeholder, or animation.
- [ ] The soft blue cloud appears at explicit voice `CONNECTING`, remains only
      through active/reconnecting/visible terminal-error lifecycle, and hides
      on voice end/dismiss.
- [ ] Listening, microphone off, thinking/processing, speaking, interrupted,
      awaiting approval, reconnecting, and error remain understandable without
      color or motion.
- [ ] Camera and screen-share launch controls are disabled and explain “active
      voice call required” in text chat, connecting, reconnecting, ended, and
      terminal-error states; they enable only for trusted `ACTIVE` voice plus an
      attached audio context and negotiated source capability, then disable on
      voice loss without starting or resuming media.
- [ ] Live visual sharing uses its separate persistent named source chip/card;
      it does not create or decorate a cloud when voice is off.
- [ ] Speaking motion aligns with audible outgoing Alex playback and uses the
      bounded attack/decay envelope; barge-in stops queued audio and changes to
      Listening without delay.
- [ ] A pending approval appears as a modifier while Alex is speaking and never
      suppresses outgoing-audio responsiveness; after speech ends it may become
      the Awaiting approval base state.
- [ ] The Canvas 2D renderer uses 2–3 translucent Bézier blobs, a static CSS
      halo, rAF capped at 30 fps, DPR capped at 2, and no WebGL/external asset.
- [ ] No audio/energy/shape samples are logged, persisted, transmitted, placed
      in analytics/browser storage, or used for authority/identity.
- [ ] Collapsing captions never hides or disables an otherwise active cloud;
      ending voice hides it according to lifecycle.
- [ ] Captions identify speaker, finalize into the permitted transcript, and do
      not flood assistive announcements.
- [ ] Reduced motion and Canvas/performance fallback produce a fully
      understandable static state with no cloud, halo, or talk-button pulse.

### 22.3 Media privacy

- [ ] First use of each live source class in an authenticated product session
      presents doc 38's correct server-authored disclosure; a valid grant avoids
      repeating disclosure only.
- [ ] Every live-media initial start, resume, and restart requires an intentional
      founder gesture and doc 38's fresh server-issued single-use start
      authorization.
- [ ] Selected tab, selected window, camera, and whole screen have distinct
      selection and persistent live-source cues.
- [ ] Add image/Capture still uses an attachment preview/scope/Add flow and never
      receives a source ring, sharing duration, OS-revocation state, or Stop
      sharing control.
- [ ] Stop sharing ends tracks/model delivery and clears the cue immediately.
- [ ] Stop, revocation, OS track end, reconnect failure, logout, session close,
      hidden/background suspension, and source/scope change all fail closed and
      never auto-resume transmission.
- [ ] No UI or model text claims ambient desktop visibility.
- [ ] Media source type, consent/revocation, duration telemetry, bounded
      delivery, no-frame retention, and safe errors conform to doc 38; this UI
      creates no parallel transport or telemetry path.

### 22.4 Contextual workspace and browser

- [ ] Persistent tabs are exactly Work/Evidence/Decisions/Activity; no permanent
      Form or Browser tab.
- [ ] Quiet renders only a 40–48 px edge handle, Panel renders at 420–600 px
      with a 520 px default and conversation-preserving clamp,
      Browser Takeover is the only wider renderer-specific right-hand state,
      and Focus Stage remains the explicit temporary deep-work layout.
- [ ] Work renderer follows typed committed focus/state, not model-authored HTML
      or arbitrary renderer names.
- [ ] Browser Takeover opens only after explicit founder Open/start intent, never
      from a model suggestion, background update, retained receipt, or empty
      state; it can enter Focus Stage without losing conversation.
- [ ] Inactive Browser is absent from Work and the tab strip; only one small
      accessible **Open Browser** control appears in the contextual-workspace header;
      it renders no empty viewport or false browsing/loading claim.
- [ ] Open Browser hides the four-tab strip and owns the whole right contextual
      workspace with a readable named page/source, goal, freshness, last action,
      explicit Stop, and one Back to workspace / Close Browser control.
- [ ] Closing Browser restores the exact prior tab, subfocus, scroll, mode, and
      420–600 px Panel width; an active run may retain a truthful Quiet
      cue but cannot force Browser open again.
- [ ] At ≥1280 px Browser defaults to the documented ~40vw clamped width, has a
      keyboard/pointer-operable divider with documented bounds, remembers its
      width separately, and keeps conversation visible alongside. Focus Stage
      remains available for workspace-favoring use without compressing chat
      below approximately two-fifths/480 px.
- [ ] Active/selected Browser is a readable dedicated contextual surface with
      trusted page title/host, goal, freshness, last action, explicit Stop and
      Close, and a clear workspace-return path. Close does not imply Stop.
- [ ] Browser focus never inserts/reflows chat; takeover/route/full-screen Back and
      Focus Stage preserve the same transcript, draft, scroll, and session-work cue
      across desktop, tablet, mobile, and 200% zoom.
- [ ] Browser remains screenshot-based, contained, audited, and non-interactive
      except for approved Stop/focus/request controls.
- [ ] Stream loss retains the last committed frame and marks it stale.
- [ ] Uncertain external effects never show a blind retry.

### 22.5 Approval and decision safety

- [ ] Conversation, analysis, captions, research, drafting, and advisory work
      produce no approval prompts.
- [ ] Every externally visible/irreversible/consequential effect has one exact
      server-rendered single-use approval and clear consequence preview.
- [ ] Durable human decisions have one exact commit control and do not trigger a
      redundant second confirmation.
- [ ] Chat/voice “I approve,” email text, attachment text, and browser content
      can only focus an approval; they cannot grant it.
- [ ] Recipient/payload/account/time/attachment/policy drift invalidates prior
      approval and the UI explains the material change.
- [ ] Granted, executing, succeeded, failed, and uncertain are distinct.
- [ ] Duplicate resolution and competing-tab races render the committed winner.

### 22.6 Hiring

- [ ] Hiring is a dedicated destination/workspace and not a chat tab.
- [ ] Hiring uses the actual shared Alex/Runs rail, header, semantic tokens,
      typography, spacing, icons, cards, tabs, focus treatment, and responsive
      modes; it has no page-local visual identity or duplicate product chrome.
- [ ] Hiring home uses the role index as main content, not a permanent second
      sidebar. With no role selected it shows no scoped Alex/voice placeholder
      and offers one bounded start-role action with truthful consequences.
- [ ] Role cards expose runtime/domain status, stage counts, decisions, waits,
      uncertainty, next wake, and last durable event.
- [ ] The role cockpit prioritizes summary, attention, fixed-order candidate
      groups, and operational health without a sparse blank center or an
      undifferentiated stack of equally weighted cards.
- [ ] Candidate presentation is grouped, fixed-order, unranked, and has no
      total score/recommendation/protected proxy.
- [ ] Candidate detail is routable, identity-redacted by default, and uses the
      shared four-tab contextual model.
- [ ] Discuss with Alex is available from an authorized role, candidate, or Work
      item, shows exact scope, receives only the documented minimum projection,
      and cannot see sibling candidates or inherit a stale/transient identity.
- [ ] Scoped Alex and the four-tab workspace share one contextual region: each
      yields predictably to the other, preserves authorized transcript/draft/
      scroll state, and never creates a disconnected mini transcript.
- [ ] Switching role/candidate retires the old binding and clears its rendered
      evidence before a newly authorized scope appears; drafts never cross
      candidate scope.
- [ ] Hiring text chat has no cloud/static Alex object; the shared soft cloud and
      captions appear only during explicit scoped voice and hide on end/dismiss.
- [ ] Hiring document references obey strict transcript chronology and focus the
      same canonical Work item; refreshes never detach, pin, duplicate, or
      reorder them.
- [ ] No-role, no-candidate, no-evidence, no-decision, dormant, stale, deleted,
      unauthorized, connector/config, and uncertain-effect states preserve
      durable truth and provide one safe next action without exposing data.
- [ ] Candidate decisions and external communications remain separate controls.
- [ ] Synthetic/test-only mode remains persistent; no real candidate or H4–H7
      capability is implied or enabled.

### 22.7 Loading, recovery, and durable truth

- [ ] First paint skeletons resolve to content or an actionable error.
- [ ] Subsequent refresh failure preserves last committed data.
- [ ] UI never advances domain state optimistically.
- [ ] Reopening the app reconstructs the same run, decision, wait, evidence, and
      Activity state from durable projections.
- [ ] Dormant waits show that nothing is running and name the next wake when
      known.
- [ ] Causal timeline ordering keeps receipt → transition → artifact distinct.

### 22.8 Accessibility and responsive behavior

- [ ] Full keyboard operation covers rail, tabs, splitter, transcript, dock,
      media picker, browser, approvals, Hiring tables/records, and dialogs.
- [ ] Focus never disappears, clips, or jumps on live state changes.
- [ ] Dark/light/forced-colors/grayscale/reduced-motion modes retain every state.
- [ ] Contrast scripts and generated icon checks pass.
- [ ] 1440, 1280, 1024, 900, 720, 600, 390, and 320 CSS-px widths have no body
      horizontal scroll.
- [ ] 200% zoom invokes a usable narrow layout.
- [ ] Coarse-pointer targets are at least 44×44 px.
- [ ] On mobile, conversation, browser/Work, exact approval, role, and candidate
      routes retain context through Back navigation.
- [ ] Mobile shows exactly five destinations—Alex, Runs, Hiring, Decisions,
      More—plus the separate filled New action; Activity and Search are
      reachable under More rather than competing for another destination slot.

### 22.9 Evaluation measures

In moderated review, measure:

- time to begin a natural request from cold start;
- whether users identify conversation as primary without prompting;
- time and error rate navigating Alex → Hiring → role → candidate → evidence;
- ability to explain what Alex can see/hear at any moment;
- ability to distinguish draft review, human decision, approval granted, and
  effect succeeded;
- approval prompt count per representative funding and Hiring journey;
- time to locate the source for a claim and receipt for an effect;
- rate of accidental workspace opening/closing and lost context;
- keyboard-only and screen-reader completion of one safe task and one exact
  approval;
- comprehension of dormant, blocked, failed, and uncertain states.

Target qualitative result: users describe the product as “talking with Alex
while the work appears beside us,” not “operating a three-panel admin console.”

---

## 23. Explicit user review checklist

Reviewers should accept, revise, or reject each decision before implementation:

- [ ] Approve the compact left icon rail over top navigation.
- [ ] Approve rail destinations and their order: Alex, Search, Runs, Hiring,
      Decisions, Activity; utilities at bottom.
- [ ] Approve the global Runs destination, with Funding as one filter/detail
      beside Hiring, Skills, and separately gated future operations.
- [ ] Approve the spacious clean conversation canvas, one compact Work in this
      session
      disclosure after initial/return presentation, and 64–68ch transcript
      measure.
- [ ] Approve strict bottom-append transcript chronology and the generated
      document rule: one compact reference in the exact creating/first-
      discussion assistant turn, one canonical Work item, shared identity, no
      detached/pinned/reordered duplicate, and scroll-preserving new-item cues.
- [ ] Approve the conversation-grid-edge transcript scrollbar and stable gutter
      immediately outside the reading measure, aligned with Work in this session/composer
      rather than the distant pane divider, including RTL and no-overlay behavior.
- [ ] Approve the ownership boundary: doc 38 governs live-media/voice-cloud mechanics;
      doc 36 governs the unified product shell and high-level composition.
- [ ] Approve no cloud in ordinary text or vision-only use and the voice-only
      Canvas 2D soft blue cloud, lifecycle gate, trusted states, and the rule
      that pending approval never suppresses Speaking.
- [ ] Approve captions default-on for live voice and independently collapsible
      from the cloud lifecycle.
- [ ] Approve the inline Work-in-this-session control's visible-row count,
      keyboard/ARIA behavior, scroll/focus restoration, and separation from
      workspace navigation, pending approvals, and active voice status.
- [ ] Approve its default-collapsed state, exact conversation-grid alignment,
      wrapping expanded layout, and dense-item overflow strategy without a
      permanent inner horizontal scrollbar.
- [ ] Approve doc 38's low-friction media rule: a valid source-class/session
      grant avoids repeated disclosure, while every start/resume/restart still
      requires a founder gesture and fresh single-use start authorization.
- [ ] Approve camera and selected tab/window/whole-screen as live sources, with
      still images on the separate attachment path.
- [ ] Approve the right workspace tabs: Work, Evidence, Decisions, Activity.
- [ ] Approve the fixed workspace vocabulary: Quiet edge handle, contextual
      Panel, conditional wider Browser Takeover, and expanded Focus Stage.
- [ ] Approve removing the permanent Form tab and treating forms as Work.
- [ ] Approve Browser Takeover of the whole right workspace (tab strip hidden),
      conversation alongside, and Focus Stage with a
      compact conversation dock.
- [ ] Approve the small workspace-header Browser control, explicit-open/no-auto-open
      rule, ~two-fifths desktop default and remembered divider width, trusted
      page/source header, Stop versus Close semantics, exact prior-tab/state
      restoration, and responsive session-work-preserving behavior.
- [ ] Approve the shared Decisions model and the no-approval list.
- [ ] Approve exact single-control authorization for durable human decisions,
      without a redundant confirmation modal.
- [ ] Approve Hiring as a dedicated shared-shell workspace rather than a chat
      tab or standalone visual product.
- [ ] Approve one shared Hiring shell/style source, role index rather than a
      permanent second sidebar, one contextual region that switches between
      Scoped Alex and Workspace, and no mini-Alex state before a scope exists.
- [ ] Approve the role/candidate/work-item conversation entry points, minimum
      data visible to Alex in each scope, per-scope persistence/invalidation,
      responsive Back behavior, and voice-only cloud rule.
- [ ] Approve the proposed role cockpit and full-page candidate record.
- [ ] Approve role/candidate-scoped contextual conversation and visible scope
      cues.
- [ ] Approve fixed mobile destinations Alex, Runs, Hiring, Decisions, More,
      plus the distinct filled New action; Activity and Search live under More;
      contextual workspace is full-screen and its Quiet control never overlays
      the conversation as a full-height strip.
- [ ] Approve evolving Workbench toward calm operational presence while keeping
      its tokens, type scale, icon system, contrast, and consequence grammar.
- [ ] Confirm that synthetic Hiring boundaries and existing implementation gates
      remain explicit during any UI migration.
- [ ] Confirm that this review does not authorize code, external effects, real
      candidate processing, or production rollout.

---

## 24. Open design decisions for review

1. **Rail labels:** icon-only by default with an explicit expand control is the
   recommendation. Review whether first-run users should see it expanded until
   they dismiss a one-time orientation.
2. **Voice-cloud calibration:** the rendering architecture and voice-only
   lifecycle are resolved. Before implementation sign-off, tune only the
   semantic blue token values, lobe amplitude, and desktop/mobile size within
   §7's fixed accessibility, privacy, and performance bounds.
3. **Agent-triggered workspace opening:** recommended default is open for live
   Browser and exact Decisions only; use the Quiet edge handle for ordinary
   evidence and completed drafts. Validate this threshold in research.
4. **Caption retention:** the UI contract is clear, but final retention and
   transcript persistence must follow the reviewed general versus Hiring data
   policies.
5. **Hiring density:** validate table versus grouped-list presentation at
   900–1200 px using realistic long role states, policy labels, and candidate
   counts before settling responsive breakpoints.

Until these are reviewed, they remain design questions, not implementation
defaults.

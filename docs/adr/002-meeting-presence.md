# ADR 002 — Meeting Presence: Notes, Scheduling, and the Live-Participation Ladder

**Status:** Accepted (design); Level 1 + Calendar scheduling are v2 scope
**Date:** 2026-08-20
**Sources (verified 2026-08-20, docs last updated 2026-07-22):**
developers.google.com/workspace/meet/api/guides/overview,
developers.google.com/workspace/meet/media-api/guides/overview

## Decision

Alex's meeting capabilities follow a four-level ladder. **Level 1 (post-meeting
notes via Meet REST API artifacts) and Calendar-based scheduling are the
committed v2 designs.** Real-time participation (Level 3) is roadmap, gated on
the Meet Media API leaving Developer Preview.

| Level | Capability | Mechanism | Availability | Status |
|---|---|---|---|---|
| **0** | Talk to Alex by voice | Existing Gemini Live surface (`app/live.py`) in the browser | Built | v1 |
| **1** | Alex **takes notes** on meetings | Google Workspace Events API → Pub/Sub on conference end → fetch **transcript + transcript entries** (Meet REST API artifacts, saved to organizer's Drive) → distiller → notes + Founder Profile updates | GA, plain REST | **v2 (committed)** |
| **2** | Alex is **present** in-call | Meet add-on: side-panel app with live notes/checklist. No media access | GA (JS iframe app; Marketplace publishing) | v2/v3 |
| **3** | Alex **listens/speaks live** | Meet Media API → raw audio/video over WebRTC → Gemini Live | **Developer Preview** — Cloud project, OAuth principal, *and every conference participant* must be enrolled; C++/TypeScript clients only; consent dialog required; anyone in-call can stop it; encrypted/watermarked meetings rejected; publish-back not advertised in current preview surface | Roadmap |

### What is explicitly NOT available

- **In-call text chat:** the Meet REST API has no in-call chat resource (surface
  is `spaces`, `conferenceRecords`, `participants`, `participantSessions`,
  `recordings`, `transcripts`). Google Chat apps operate on Chat spaces, not
  Meet in-call chat. This path is closed regardless of budget.
- **Level 3 for the hackathon:** multi-day native WebRTC build plus external
  preview enrollment for all participants — violates the no-gold-plating rule.

### Level 1 design notes (v2)

- Fits the dormancy architecture verbatim: conference ends → Pub/Sub event →
  webhook → resume handler wakes Alex → fetch artifacts → distill.
- Reuses the existing Google OAuth adapter pattern (`services/google_oauth.py`,
  Drive/Gmail connectors).
- Requirements: Workspace edition with transcription enabled;
  recording/transcription turned on per meeting.
- Note the Meet REST API usage policy: not for performance tracking or user
  evaluation within the domain.

## Calendar: checking availability and booking meetings

Alex can check `ijidai@ruhu.ai` availability and book meetings via the
**Google Calendar API** (GA, no preview):

- **Check:** `freebusy.query` against the founder's calendar — free/busy is
  visible by default within the same Workspace org. External invitees'
  availability is NOT queryable (unless their calendar is shared); Alex
  proposes times from the founder's free/busy and the invitee accepts/declines.
- **Book:** `events.insert` on the founder's calendar with
  `attendees=[someone@example.com]`, `sendUpdates=all` (Google sends the
  invite email), and `conferenceData` to auto-attach a Meet link.
- **Scopes:** `calendar.readonly` (checks) + `calendar.events` (booking),
  added to the existing OAuth connector pattern.
- **Gating (binding principle 5):** sending a calendar invite is an external,
  reputation-spending action → approval-gated like submission and v2 email.
  Alex researches, proposes times, and drafts the invite; the founder approves;
  Alex sends. Free/busy *checks* are read-only and ungated.

## Consequences

- The demo/write-up gains the category brief's *"takes notes"* claim honestly:
  "Alex takes notes on every meeting" via Level 1, no live bot required.
- Dogfooding synergy: meeting voice intelligence is Ruhu's own product domain.
- Calendar scheduling completes the loop for workflow #2 (investor outreach):
  draft outreach → gated send → reply → Alex proposes times → gated booking →
  Meet link auto-attached → Level 1 notes after the call.

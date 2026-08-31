# Architecture Diagram (submission artifact — docs/13 §submission housekeeping)

## Mermaid (renders on GitHub)

```mermaid
flowchart LR
    subgraph Founder["Founder surfaces"]
        UI["Demo UI<br/>(static page)"]
    end

    subgraph CR["Cloud Run: co-founder"]
        direction TB
        API["FastAPI<br/>get_fast_api_app + custom routes"]
        R1["Runner — chat surface"]
        R2["Runner — webhook/task surface"]
        R3["Runner — distiller surface"]
        APP["App: co_founder"]
        ROOT["Orchestrator"]
        SCOUT["Scout"]
        MATCH["Matchmaker"]
        INTER["Interviewer"]
        DRAFT["Drafter"]
        FILL["Form-Filler<br/>+ staleness fence callback"]
        DIST["Distiller<br/>include_contents=none"]
        APP --> ROOT
        ROOT --> SCOUT & MATCH & INTER & DRAFT & FILL
        R3 --> DIST
    end

    GEM["Gemini 3.6 + Lite/Live tiers<br/>(Vertex AI)"]
    SQL[("Cloud SQL<br/>ADK sessions")]
    FS[("Firestore<br/>pipeline · profiles ·<br/>approvals · audit")]
    GCS[("Cloud Storage<br/>artifacts")]
    SM["Secret Manager<br/>connector + portal-account secrets"]
    SCHED["Cloud Scheduler"]
    PS["Pub/Sub"]
    CT["Cloud Tasks<br/>co-founder-events"]
    BXT["Cloud Tasks<br/>co-founder-browser-expiry"]
    PORTAL["External application provider<br/>(real portal or A2A agent)"]

    UI -->|HTTPS| API
    API --> R1 & R2
    R1 & R2 --> APP
    ROOT & SCOUT & MATCH & INTER & DRAFT & FILL & DIST --> GEM
    R1 & R2 & R3 --> SQL
    SCOUT & MATCH & INTER & DRAFT & FILL & DIST --> FS
    SCOUT & FILL --> GCS
    FILL --> SM
    SCHED -->|cron| PS
    PS -->|push OIDC| API
    API -->|durable portal-wake enqueue| CT
    CT -->|OIDC /tasks/portal_wake| API
    API -->|generation-safe lease task| BXT
    BXT -->|OIDC /tasks/browser_expire| API
    FILL -->|Playwright DOM + vision recon| PORTAL
    PORTAL -.->|optional signed event| API
```

## One-paragraph walkthrough (for the caption)

The founder talks to a demo UI served by a FastAPI app on Cloud Run. ADK's
`get_fast_api_app` owns the chat surface; a second Runner handles webhooks and
scheduled tasks; a third runs the isolated Distiller. All three share one
Cloud SQL session store, so a run parked for days resumes via `state_delta`
with zero replay. The orchestrator routes by an explicit state machine
(`current_step` injected into every instruction), delegating to five scoped
sub-agents. Firestore holds the pipeline, the Founder Profile (long-term
memory), approval tokens (never in model context), and an append-only audit
trail. The Form-Filler drives Playwright against configured real portals —
DOM-first, vision recon as tier-1 fallback — and submits only behind a
founder-granted, server-resolved approval, with a derived idempotency key.
Founder commands enqueue discovery through Cloud Tasks; Cloud Scheduler →
Pub/Sub wakes only the deadline sentinel. Optional signed provider events wake
the corresponding workflow. Agent wakes
that must outlive the webhook request are durably enqueued in the existing
`co-founder-events` Cloud Tasks queue before the webhook acknowledges.
Generation-safe browser lease expiry uses the separate
`co-founder-browser-expiry` queue, so resource cleanup cannot wait behind an
agent wake or its retries.

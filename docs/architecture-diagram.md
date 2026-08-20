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

    GEM["Gemini 3.5<br/>(Vertex AI)"]
    SQL[("Cloud SQL<br/>ADK sessions")]
    FS[("Firestore<br/>pipeline · profiles ·<br/>approvals · audit")]
    GCS[("Cloud Storage<br/>artifacts")]
    SM["Secret Manager<br/>portal creds (by name)"]
    SCHED["Cloud Scheduler"]
    PS["Pub/Sub"]
    MP["Cloud Run: mock-portal<br/>(16-field form, idempotent submit)"]

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
    FILL -->|Playwright DOM + vision recon| MP
    MP -->|signed webhook| API
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
trail. The Form-Filler drives Playwright against portals — DOM-first, vision
recon as tier-1 fallback — and submits only behind a founder-granted,
server-resolved approval, with a derived idempotency key. Cloud Scheduler →
Pub/Sub wake the agent for discovery sweeps and deadline scans; the mock
portal calls back over a signed webhook when a submission confirms.

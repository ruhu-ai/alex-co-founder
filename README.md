# Co-Founder

An AI co-founder for solo founders — it discovers grants/accelerators, matches
them to a persistent Founder Profile, interviews you, drafts applications in
your voice, learns from your feedback, and pre-fills portal applications behind
a human approval gate.

**All Things Agentic Hackathon — Collaborative Partner category.**
Built with Gemini 3.5, Google ADK, and Google Cloud (Cloud Run, Firestore,
Cloud SQL, Pub/Sub, Cloud Scheduler, Secret Manager, Cloud Storage).

## Spin-up

```bash
./scripts/setup.sh          # idempotent: venv, deps, project, ADC, APIs, .env
source .venv/bin/activate
cp .env.example .env        # if setup.sh didn't already write it
adk web agents --port 8000 \
  --session_service_uri="sqlite+aiosqlite:///sessions.db" \
  --artifact_service_uri="file://./artifacts"
```

Full local dev (agent server + mock portal), cloud deploy, tests/evals, and
cost guardrails: see `docs/13-deployment.md`. Implementation specifications:
`docs/README.md` (index) through `docs/14-build-plan.md` (day plan).

## Layout

- `agents/co_founder/` — the ADK app (orchestrator + 5 sub-agents; distiller standalone)
- `app/` — FastAPI server, webhooks, resume handler (Day 9)
- `services/` — Firestore, secrets, storage (service layer)
- `mock_portal/` — demo application portal (Day 7)
- `workflows/` — declarative workflow definitions
- `tests/` — eval sets, fixtures, integration/unit tests
- `docs/` — the specifications this repo is built from

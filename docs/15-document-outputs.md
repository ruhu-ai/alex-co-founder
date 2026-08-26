# 15 — Document Outputs (docx / xlsx / pptx)

The co-founder's work product is documents: the application pack, the budget,
the deck. This spec adds document **production** as a first-class capability —
generated from approved state, validated before delivery, downloadable from the
UI, optionally synced to the founder's Drive.

## Patterns mined (reference repos, read-only)

| Source | Pattern we adopt |
|---|---|
| `harveyai/harvey-labs` skills (`docx/`, `pptx/`, `xlsx/`) | **spec → build → validate**: model emits a JSON spec; a small deterministic builder writes the file; a validation gate runs before delivery. ~30–110 LOC per builder. |
| `Tracer-Cloud/opensre` `GoogleDocsClient`, `incidentfox` docs-google | Google-native creation idiom: create → `batchUpdate` with small request builders; **errors as data**, never raised. |
| `google/adk-python` `DocsToolset`/`SlidesToolset`/`SheetsToolset` | Google-native creation without custom API code, if we ever need native Docs/Slides/Sheets objects. |
| ADK artifact samples | `tool_context.save_artifact(...)` — files live in the artifact store, never in chat history. |
| `andrewyng/aisuite` GDOCS-SHEETS-SPEC | OAuth scope tiering: `drive.file` (per-file) — never full-drive; writes ask first. |
| `superdoc` warning | python-docx is fine for generation; raw OOXML surgery (redlines/comments) is out of scope. |
| `Chainlit/chainlit` (`Elements/`, `InlinedElements`) | **In-chat cards**: typed artifact array per turn, cards stacked *below* the message text; extension-badge card (colored rounded square + truncated name + tooltip); generic files punt to download. |
| `Chainlit` PDF element | **Two-tier preview**: inline thumbnail card → full modal viewer. We adopt this shape but keep it download-first (see below). |
| `CopilotKit` showcases (`ArtifactPanel`, `Workspace`) | **Documents panel**: sectioned card list beside the chat with counts and an honest empty state; chat stays clean, library lives beside it. |
| `superdoc` viewer | Rejected for v1: in-browser DOCX preview works (`documentMode: 'viewing'`, CDN build) but costs ~3 MB and is AGPL v3. Nobody previews xlsx/pptx in-browser. |

## Architecture

```
agent (drafter/orchestrator)
  └─ produce_document(kind, spec)            # tool — docstring is the contract
       └─ services/document_service.py
            ├─ build_docx(spec)   python-docx     (application packs, narratives)
            ├─ build_xlsx(spec)   openpyxl        (budgets, projections; formulas
            │                                       as strings — no forced recalc)
            ├─ build_pptx(spec)   python-pptx     (decks from a slide-spec)
            └─ validate_document(path)            # ZIP + XML well-formedness +
                                                  # required OOXML parts — the gate
       └─ save_artifact → {status, artifact_name, download_url}

GET /api/artifacts/{name}/download            # streams with correct OOXML MIME
                                              # (inline images use /preview — 07/18)
UI                                            # download chips in chat + review panel
optional Drive sync                           # drive.file scope, approval-gated
```

**Spec contract (binding):** the model emits *data* (JSON specs: sections,
rows, slides), never markup. Deterministic code owns layout, styles, and file
format. This is "guards in code, not prompts" applied to documents — the model
can produce a bad spec, but it cannot produce a corrupt file.

**Spec shapes:**

- `docx`: `{title, sections: [{heading, paragraphs: [..] | bullets: [..]}]}`
- `xlsx`: `{sheets: [{name, columns: [..], rows: [[..]], formulas: {cell: "=…"}}]}`
- `pptx`: `{title, slides: [{title, bullets: [..], notes?}]}`
- `pdf`: same spec as `docx` — built as docx, converted headlessly via
  LibreOffice (`soffice --convert-to pdf`). Degrades to error-as-data where
  soffice is absent: the agent then ships the .docx, which converts in one step.

## LibreOffice posture (server-side conversion rules)

Headless LibreOffice is the conversion engine for previews and PDF output.
It runs under these constraints, all in code:

| Rule | Implementation |
|---|---|
| Controlled install, never the dev's desktop copy | Prod image installs `libreoffice-writer/-calc/-impress` via pinned apt (Dockerfile). Native macOS conversion is disabled by default because its app-bundle wrapper can activate GUI windows; local development returns error-as-data and produces `.docx` instead. |
| Non-root execution | Image runs as `pwuser` (Dockerfile) |
| Temporary per-job storage | Each conversion gets a fresh `TemporaryDirectory` (profile + work dir), removed after — no shared profile, no lock-check hacks |
| Process ownership | Each conversion starts in a new process session. Timeout terminates the entire process group, including children spawned by wrapper scripts, so no converter survives its request. |
| Macros never execute | Fresh profile (macro security defaults High/disabled) **and** macro-bearing parts (`vbaProject`, `activeX`, `macroSheet`, `oleObject`) stripped from OOXML inputs before conversion |
| Limits | 25 MB input cap, 120 s hard timeout, single-flight conversion (semaphore of 1); memory bounded by the Cloud Run container limit |
| Output validation | The converted PDF must pass `validate_document` (header + EOF) before it is cached or served; failures delete the artifact and return error-as-data |
| Residual (documented) | No antivirus scan of uploads; mitigations are macro stripping, the high-security profile, and structural validation |

Formula note (honesty): xlsx formulas are written as strings; recalc-on-load
is **not** forced, so a PDF preview shows unevaluated formulas unless the
consuming app recalculates. Deliberate v1 choice — say values, not formulas,
in specs meant for PDF.

## Tool

`produce_document(kind: "docx"|"xlsx"|"pptx", title: str, spec: dict)` —
validates the spec shape, builds, runs `validate_document`, saves the artifact,
returns `{status, artifact_name, download_url}`. On any failure: error dict,
**no partial artifact** (build to a temp path; only promote on validation pass).

Supersedes the `generate_application_pack` stub (former cut-list #3): the
application pack becomes `produce_document("docx", …)` over APPROVED sections +
profile facts.

## Provenance: documents linked to sessions

Every produced document is traceable: *which conversation, working on which
application, made this file — and from what spec.* The artifact store holds
bytes; a Firestore `documents` registry holds the lineage:

```json
{
  "artifact_name": "pack_app1_v3.docx",
  "kind": "docx",
  "title": "Meridian Pre-Seed Grant — Application Pack",
  "session_id": "s-aeea2b5d",
  "application_id": "app1",
  "opportunity_name": "Meridian Pre-Seed Grant",
  "created_by": "tool:produce_document",
  "spec_hash": "sha256:…",
  "version": 3,
  "created_at": "…"
}
```

- `session_id` comes from `tool_context` — the invocation always knows its
  session, so provenance is automatic, never model-supplied.
- `spec_hash` makes regeneration diffable ("same spec → same bytes").
- Regenerating the same document bumps `version` — history is append-only.
- `GET /api/documents?session_id=…&application_id=…` serves the registry.

## Document output UI

Two surfaces, both fed by the registry (patterns: Chainlit in-chat elements,
CopilotKit artifact panel):

1. **In-chat document cards** (Chainlit model). Documents produced in a turn
   render as a card block *below* the agent message that made them — never a
   raw URL. Card: extension badge (colored rounded square: `DOCX` blue /
   `XLSX` green / `PPTX` orange), truncated title (full name in tooltip),
   version, session tag, **Download** affordance. The UI joins
   `/api/documents?session_id=…` with the transcript — no markup contract with
   the model required.

2. **Documents section** (review column, below Fill report — CopilotKit panel
   model). Grouped by application, newest first; each row: kind badge, title,
   `v3`, session tag, download. Clicking the session tag filters the chat log
   to the producing conversation — provenance is navigable in both directions
   (doc → conversation, conversation → doc). Driven by the registry, not by
   polling chat markup.

3. **Preview policy (final).** **View-only in-browser preview for every
   format**: `GET /api/artifacts/{name}/preview` converts docx/xlsx/pptx to PDF
   via headless LibreOffice (cached as `{name}.preview.pdf`) and streams it;
   the UI renders it in a pdfjs modal (vendored, Apache-2.0, lazy-loaded —
   no CDN, offline-safe) with page nav, zoom, and download. One code path, no
   AGPL, no editing surface — read-only by construction. Where soffice is
   absent the endpoint returns 503-as-data and the card remains
   download-only.

Empty state: "No documents yet — ask your co-founder for an application pack, a
budget, or a deck."

## Security & privacy

- Artifacts stay local (artifact store) by default.
- Drive sync copies the finished file via `files.create` with `drive.file`
  scope only — a write to the founder's account, so it is **approval-gated**
  (same posture as submission): the agent proposes, the founder confirms.
- Download endpoint serves only files present in the artifact store — no path
  traversal (name must match `^[A-Za-z0-9_.-]+$`).

## Dependencies

`python-docx`, `openpyxl`, `python-pptx` (pure Python, no services).
Optional: `docxtpl` (template fill), LibreOffice headless (formula recalc) —
both degrade gracefully when absent.

## Acceptance checks

- [ ] `produce_document("docx", …)` over approved sections → valid .docx, downloads from the UI
- [ ] `produce_document("xlsx", …)` with formulas → recalcs (or ships unrecalced with a note), no `#REF!`/corrupt file
- [ ] `produce_document("pptx", …)` from a slide spec → opens in Keynote/PowerPoint, validation gate passed
- [ ] `produce_document("pdf", …)` → real PDF (header + EOF validated) where soffice exists; error-as-data otherwise
- [ ] view-only preview: every card opens a pdfjs modal (docx/xlsx/pptx converted + cached server-side); preview endpoint rejects traversal and returns 503-as-data without soffice
- [ ] malformed spec → error dict, zero bytes written to the artifact store
- [ ] download endpoint rejects path traversal (`../`), serves correct MIME
- [ ] Drive sync: asks first; copies the file only after founder approval
- [ ] every produced document has a registry row with `session_id` + `application_id` — visible in the Documents section, and the session tag navigates to the producing conversation
- [ ] in-chat document cards render (not raw URLs) and download
- [ ] regenerating bumps `version`; history is append-only
- [ ] unit tests for builders + validation gate + registry; eval case for the tool contract

"""Real end-to-end browser story check (docs/09, 15, 18).

Real server, real Gemini, real Chromium — no mocks. Drives the running app
over HTTP exactly the way the founder UI does.

Story under test:
  Act 1 (18): Alex opens a public webpage, navigates a link, reads it back,
              and the Browser panel state reflects the run. Also proves the
              SSRF guard refuses the loopback mock portal.
  Act 2 (09):  Alex takes the Meridian grant application to the mock portal —
              vision recon reads every question on the application page
              (form_map), then fills from approved answers (fill report).
              Interview/draft are compressed by seeding an APPROVED
              application at service level (that path is covered by the demo
              script and evals); the browser work under test is fully live.
  Act 3 (15):  Alex produces a document answering every form question.

Prereqs:
  source .venv/bin/activate; ADC credentials; Firestore reachable
  python scripts/seed_demo.py
  terminal 1: uvicorn app.main:app --port 8090
  terminal 2: uvicorn mock_portal.main:app --port 8091

Run: python scripts/e2e_browser_story.py
Tip: HEADLESS=false in .env to watch the Chromium window while it works.
"""

import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

APP = os.environ.get("E2E_APP_URL", "http://127.0.0.1:8090")
PORTAL = os.environ.get("E2E_PORTAL_URL", "http://127.0.0.1:8091")
PORTAL_HOST = "127.0.0.1:8091"
WAKE_TIMEOUT = 600.0
FAILURES: list[str] = []
REFUSAL_WORDS = ("can't", "cannot", "not allowed", "refused", "blocked",
                 "won't", "unable", "not on my")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {name}{(' — ' + detail) if detail else ''}",
          flush=True)
    if not ok:
        FAILURES.append(f"{name}: {detail}")


async def wake(client: httpx.AsyncClient, session_id: str, message: str) -> str:
    print(f"  → {message[:90]}", flush=True)
    try:
        resp = await client.post(
            f"{APP}/wake", json={"session_id": session_id, "message": message},
            timeout=WAKE_TIMEOUT)
        resp.raise_for_status()
    except httpx.ReadTimeout:
        print("  ✗ wake timed out (agent chain exceeded budget)", flush=True)
        FAILURES.append(f"wake timeout: {message[:60]}")
        return ""
    replies = resp.json().get("replies", [])
    text = "\n".join(replies)
    print(f"  alex: {text[:220].replace(chr(10), ' / ')}", flush=True)
    return text


async def setup_approved_application() -> tuple[str, str, str]:
    """Compress interview/draft/review: a SHORTLISTED Meridian opportunity →
    APPROVED application with founder-approved sections, plus an ADK session
    whose state points at it (what choose_opportunity + approvals would set).
    Also registers the mock portal credential in the local dev store."""
    from google.adk.sessions.database_session_service import (
        DatabaseSessionService,
    )

    from services import firestore, pipeline_service

    founder = os.environ.get("FOUNDER_ID", "founder")
    opp = next((o for o in await firestore.list_opportunities(limit=500)
                if "Meridian" in o.get("name", "")), None)
    assert opp, "Meridian opportunity not seeded — run scripts/seed_demo.py"

    chosen = await pipeline_service.choose_opportunity(founder, opp["id"])
    assert chosen["status"] == "success", chosen
    app_id = chosen["application_id"]

    sections = [
        {"section_id": f"sec-{key}", "section_key": key, "status": "APPROVED",
         "version": 1, "notes": "e2e seed", "content": content}
        for key, content in {
            "company": "Ruhu, Inc. — https://ruhu.ai — founded 2026-01, "
                       "pre-seed, pre-revenue. Contact: amara@ruhu.ai.",
            "problem": "Clinics across Africa lose up to 30% of chronic-care "
                       "patients to follow-up gaps because staff cannot call "
                       "thousands of patients in their own languages.",
            "solution": "Ruhu is a voice-AI platform for multilingual "
                        "conversational agents: Agent Canvas (no-code builder), "
                        "Smart Insights (real-time analytics), Voice Experience "
                        "(accent/dialect recognition) — live across 20+ major "
                        "African languages (~80% of the continent).",
            "traction": "Live platform covering 20+ African languages; pilot "
                        "program open with fully-supported onboarding; waitlist "
                        "building ahead of public launch. Team of 2.",
            "market": "Addressable market ~$4B across African digital health "
                      "and customer-operations voice automation.",
            "motivation": "Clinics are asking for this now; the grant funds "
                          "the pilot's evaluation phase. Heard about the "
                          "program from a founder friend. Target start "
                          "2026-10-01.",
        }.items()
    ]
    await firestore.update_application(
        app_id, state="APPROVED", draft_sections=sections)

    session_id = f"s-e2e-{os.urandom(4).hex()}"
    db = DatabaseSessionService(db_url="sqlite+aiosqlite:///sessions.db")
    await db.create_session(
        app_name="co_founder", user_id=founder, session_id=session_id,
        state={
            "current_step": "APPROVED",
            "active_application_id": app_id,
            "active_opportunity_id": opp["id"],
            "active_program_requirements": opp.get("required_materials", []),
        })

    secrets_path = os.environ.get("PORTAL_SECRETS_FILE", ".portal_secrets.json")
    secrets = {}
    if os.path.exists(secrets_path):
        secrets = json.loads(open(secrets_path).read())
    secrets[PORTAL_HOST] = {"email": "demo-founder", "password": "demo-pass-2026"}
    with open(secrets_path, "w", encoding="utf-8") as fh:
        json.dump(secrets, fh)

    return app_id, session_id, opp["id"]


async def main() -> int:
    async with httpx.AsyncClient() as client:
        # ---- Act 0: services up, demo seeded -------------------------------
        print("Act 0 — services and seed data", flush=True)
        check("app serving UI",
              (await client.get(f"{APP}/")).status_code == 200)
        check("mock portal up",
              (await client.get(f"{PORTAL}/healthz")).status_code == 200)

        # ---- Act 1: browse a public page (docs/18) -------------------------
        print("\nAct 1 — Alex browses a public page (docs/18)", flush=True)
        sid = (await client.post(f"{APP}/session/new")).json()["session_id"]
        reply = await wake(client, sid, "Open https://example.org and tell me "
                                        "in one sentence what this page is for.")
        refused = any(w in reply.lower() for w in REFUSAL_WORDS)
        check("open_page + read_page answered",
              not refused and "example" in reply.lower(),
              "refused — is BROWSE_OPEN_WEB=true set for dev?" if refused else "")
        state = (await client.get(
            f"{APP}/api/browser/state", params={"session_id": sid})).json()
        browse = state.get("browse")
        check("Browser panel shows the run", bool(browse and browse.get("url")),
              f"url={browse and browse.get('url')}")
        check("screenshot artifact linked",
              bool(browse and browse.get("screenshot_url")))

        reply = await wake(client, sid, "Follow the 'More information' link on "
                                        "that page and tell me where you landed, "
                                        "then close the browser.")
        check("browser_action navigated to iana.org",
              "iana" in reply.lower(),
              reply[:120] if "iana" not in reply.lower() else "")

        reply = await wake(client, sid, "Open http://127.0.0.1:8091/apply/mp-grant "
                                        "and fill out the form for me.")
        check("SSRF guard refuses loopback browse",
              any(w in reply.lower() for w in REFUSAL_WORDS))

        # ---- Act 2: application page → recon → fill (docs/09) --------------
        print("\nAct 2 — application page: recon reads every question (docs/09)",
              flush=True)
        app_id, sid2, _opp_id = await setup_approved_application()
        print(f"  seeded APPROVED application {app_id} (session {sid2})",
              flush=True)
        await wake(client, sid2, "The application is APPROVED. Transfer to the "
                                 "form-filler now: call open_portal with "
                                 "application_url http://127.0.0.1:8091/apply/mp-grant "
                                 "(the URL from the opportunity record), then "
                                 "map_form_requirements to read every question "
                                 "the form asks, then fill_fields from my "
                                 "approved answers.")
        detail = (await client.get(f"{APP}/api/applications/{app_id}")).json()
        report = detail.get("form_fill_report") or {}
        if not report:  # debug aid: dump the transcript on failure
            transcript = (await client.get(f"{APP}/api/chat/{sid2}")).json()
            for message in transcript.get("messages", [])[-6:]:
                print(f"    [{message['role']}] {message['text'][:180]}",
                      flush=True)
        check("fill report written (recon + fill ran)", bool(report),
              f"filled {report.get('filled')}/{report.get('total')}"
              if report else "no report")
        check("partial success is first-class (needs_human listed)",
              bool(report.get("needs_human")),
              f"{len(report.get('needs_human', []))} field(s) for the founder"
              if report else "")
        fill_run = (await client.get(
            f"{APP}/api/browser/state", params={"session_id": sid2})).json()
        check("Browser panel surfaced the fill run",
              fill_run.get("fill") is not None
              or fill_run.get("browse") is not None)

        # ---- Act 3: document with responses (docs/15) ----------------------
        print("\nAct 3 — document answering every question (docs/15)",
              flush=True)
        await wake(client, sid2, "Create a docx document titled 'Meridian "
                                 "Application Responses' that answers every "
                                 "question on the form, using my approved "
                                 "answers.")
        docs = (await client.get(f"{APP}/api/documents")).json()
        rows = docs.get("documents", docs if isinstance(docs, list) else [])
        match = [d for d in rows if "Meridian" in str(d)]
        check("responses document produced", bool(match),
              match[0].get("artifact_name", "") if match else "none found")
        if match and match[0].get("artifact_name"):
            dl = await client.get(
                f"{APP}/api/artifacts/{match[0]['artifact_name']}/download")
            check("document downloadable",
                  dl.status_code == 200 and len(dl.content) > 2000,
                  f"{len(dl.content)} bytes")

            # A document that exists is not a document that is correct. These
            # two checks are the actual requirement: it must answer every
            # question the form asks, and every word of it must trace back to
            # approved sections or profile facts.
            await check_document_is_grounded(dl.content, app_id)

    return _finish()


async def check_document_is_grounded(blob: bytes, app_id: str) -> None:
    """Coverage + grounding of the produced document.

    produce_document takes a model-authored `spec` and renders it; grounding is
    asked for in the tool docstring, i.e. in a prompt. docs/README.md principle
    7 says guards live in code, not prompts — so until that tool grows a real
    guard, this is where the invariant is enforced.
    """
    import io

    from docx import Document

    from mock_portal.main import _fields
    from services import firestore, profile_service

    doc = Document(io.BytesIO(blob))
    body = "\n".join(p.text for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            body += "\n" + " | ".join(c.text for c in row.cells)
    low = body.lower()

    # --- coverage: every question on the form is addressed ------------------
    # A good document paraphrases: "What problem are you solving?" becomes a
    # "Problem" heading. Accept either the field's own name tokens (problem,
    # market_size -> market + size) or the label's distinctive words, so the
    # check flags real gaps instead of good writing.
    missing = []
    for field in _fields("v1"):
        label = field["label"]
        name_tokens = [t for t in field["name"].split("_") if len(t) > 2]
        label_words = [w for w in re.sub(r"[^a-z ]", "", label.lower()).split() if len(w) > 3]
        covered = (
            label.lower() in low
            or (name_tokens and all(t in low for t in name_tokens))
            or (label_words and all(w in low for w in label_words))
        )
        if not covered:
            missing.append(label)
    total = len(_fields("v1"))
    check(f"document answers every question on the form ({total - len(missing)}/{total})",
          not missing, ", ".join(missing[:4]) + ("…" if len(missing) > 4 else ""))

    # --- grounding: the applicant is the founder's company, not the programme
    profile = await profile_service.get_profile(os.environ.get("FOUNDER_ID", "founder"))
    facts = (profile or {}).get("facts", {}) or {}
    company = str(facts.get("company_name", "")).strip()
    check("document names the founder's company", bool(company) and company.split(",")[0].lower() in low,
          f"expected {company!r}")

    app = await firestore.get_application(app_id)
    opp = await firestore.get_opportunity(app.get("opportunity_id", "")) if app else None
    programme = (opp or {}).get("name", "")
    # The programme name belongs in the title, never as the applicant.
    first_word = programme.split()[0].lower() if programme else ""
    as_applicant = bool(first_word) and (
        f"company name\n{first_word}" in low or f"{first_word}.com" in low
        or f"@{first_word}." in low)
    check("programme name is not used as the applicant company",
          not as_applicant, f"{programme!r} appears as the company")

    # --- grounding: prose traces to approved sections or profile facts ------
    approved = " ".join(str(sec.get("content", ""))
                        for sec in (app or {}).get("draft_sections", [])
                        if sec.get("status") == "APPROVED")
    source = (approved + " " + json.dumps(facts)).lower()
    # Numbers are the cheapest fabrication to detect and the most damaging to
    # publish: a grant reviewer can check them.
    doc_numbers = set(re.findall(r"\b\d[\d,]{1,}\b", body))
    invented = sorted(n for n in doc_numbers if n.replace(",", "") not in source.replace(",", ""))
    check("no invented figures", not invented,
          "not in approved answers or profile: " + ", ".join(invented[:6]) if invented else "")


def _finish() -> int:
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"E2E browser story: {len(FAILURES)} FAILURE(S)")
        for failure in FAILURES:
            print(f"  ✗ {failure}")
        return 1
    print("E2E browser story: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

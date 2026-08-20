"""FastAPI app (docs/07).

Surfaces over ONE shared session store:
  1 — founder chat (get_fast_api_app internal Runner)
  2 — webhooks/tasks (dedicated webhook Runner, resume via state_delta)
  3 — distiller (tiny second App; inline, synchronous, from the feedback path)

Fast-ack rule: task routes return 202 immediately and work in BackgroundTasks.
someone_is_there(): system sessions never ask questions.
"""

import os
import uuid

# oauthlib raises on any scope difference between flow and token response —
# but per-connector incremental consent legitimately returns a different set
# (Google merges prior grants). Relax to a logged warning.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from google.adk.apps import App
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.genai import types
from pydantic import BaseModel

from agents.co_founder.agent import app as agent_app
from agents.co_founder.config import PERSONA_NAME
from agents.co_founder.state_schema import ApplicationStep as Step
from agents.co_founder.sub_agents import distiller as distiller_subagent
from app import browser_routes
from app.app_utils.telemetry import setup_telemetry
from app.resume_handler import ResumeHandler
from services import (approval_service, distill_service, discovery_service,
                      browser_service, feedback_service, firestore,
                      pipeline_service, storage, voice_service)

setup_telemetry()

SESSION_SERVICE_URI = os.environ.get("SESSION_SERVICE_URI", "sqlite+aiosqlite:///sessions.db")


def _abs_file_uri(uri: str) -> str:
    """ADK 2.7 file:// artifact URIs must be absolute — normalize defensively."""
    if uri.startswith("file://") and not uri.startswith("file:///"):
        return f"file://{os.path.abspath(uri.removeprefix('file://'))}"
    return uri


ARTIFACT_SERVICE_URI = _abs_file_uri(
    os.environ.get("ARTIFACT_SERVICE_URI", f"file://{os.path.abspath('artifacts')}")
)

# Surface 1: founder chat
app: FastAPI = get_fast_api_app(
    agents_dir="agents",
    web=False,  # adk web is dev-only, never in prod
    session_service_uri=SESSION_SERVICE_URI,
    artifact_service_uri=ARTIFACT_SERVICE_URI,
)
app.title = "co-founder"

# Surface 2: webhooks/tasks
db_session_service = DatabaseSessionService(db_url=SESSION_SERVICE_URI)
webhook_runner = Runner(app=agent_app, session_service=db_session_service)
resume_handler = ResumeHandler(runner=webhook_runner)

# Surface 3: the distiller (inline, synchronous)
distill_app = App(name="co_founder_distill", root_agent=distiller_subagent.agent)
distill_runner = Runner(app=distill_app, session_service=db_session_service)
distill_service.attach(distill_runner, db_session_service)

FOUNDER_ID = os.environ.get("FOUNDER_ID", "founder")

# Surface 4: real-time voice (Gemini Live bidi) — same orchestrator, same sessions
from app.live import register_live  # noqa: E402

register_live(app, db_session_service, FOUNDER_ID)

# Production Gemini backends (search, extraction, doc understanding, vision,
# voice). Without ADC this raises and the services keep their offline error
# paths — the server still boots for UI/dev work.
import logging

try:
    from services import gemini_backends

    gemini_backends.wire_all()
    logging.getLogger(__name__).info("Gemini backends wired")
except Exception as _gemini_exc:
    logging.getLogger(__name__).warning("Gemini backends not wired: %s", _gemini_exc)


def _verify_portal_token(request: Request) -> bool:
    expected = os.environ.get("PORTAL_WEBHOOK_TOKEN", "dev-portal-token")
    return request.headers.get("X-Portal-Token") == expected


async def _verify_oidc(request: Request) -> bool:
    """Pub/Sub push auth (docs/13 cost control): in Cloud Run, task endpoints
    require the scheduler service-account OIDC token. Locally (no K_SERVICE),
    open for dev. Bad/missing token → 401, never a 500."""
    if not os.environ.get("K_SERVICE"):
        return True
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    try:
        from google.oauth2 import id_token as _id_token
        from google.auth.transport import requests as _auth_requests

        base = f"https://{request.url.hostname}"
        _id_token.verify_oauth2_token(
            auth.removeprefix("Bearer "), _auth_requests.Request(), audience=base)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------

class WakePayload(BaseModel):
    message: str
    session_id: str | None = None


_founder_session_id: str | None = None  # latest chat session — proactive reports land here


async def _notify_founder(notice: str) -> None:
    """Wake the founder's chat session with a system notice so the AGENT
    reports outcomes (sweep results, deadline alerts) instead of the board
    changing silently. No-op until the founder has opened a chat."""
    if not _founder_session_id:
        return
    await resume_handler.wake(
        user_id=FOUNDER_ID, session_id=_founder_session_id,
        notice=notice, state_delta={})


@app.post("/wake")
async def wake(payload: WakePayload) -> dict:
    global _founder_session_id
    session_id = payload.session_id or f"s-{uuid.uuid4().hex[:8]}"
    _founder_session_id = session_id
    existing = await db_session_service.get_session(
        app_name=agent_app.name, user_id=FOUNDER_ID, session_id=session_id)
    if existing is None:
        await db_session_service.create_session(
            app_name=agent_app.name, user_id=FOUNDER_ID, session_id=session_id)
    replies: list[str] = []
    async for event in webhook_runner.run_async(
            user_id=FOUNDER_ID, session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=payload.message)])):
        if event.content and event.content.parts:
            replies.extend(p.text for p in event.content.parts if p.text)
    return {"session_id": session_id, "replies": replies}


@app.post("/session/new")
async def new_session() -> dict:
    global _founder_session_id
    session_id = f"s-{uuid.uuid4().hex[:8]}"
    _founder_session_id = session_id
    await db_session_service.create_session(
        app_name=agent_app.name, user_id=FOUNDER_ID, session_id=session_id)
    return {"session_id": session_id}


@app.get("/api/chat/{session_id}")
async def chat_history(session_id: str) -> dict:
    """Full chat transcript so agent-initiated messages (proactive reports)
    render without the founder sending anything. System wake notices are
    hidden — only the agent's replies to them surface."""
    session = await db_session_service.get_session(
        app_name=agent_app.name, user_id=FOUNDER_ID, session_id=session_id)
    if session is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    messages = []
    for event in session.events or []:
        content = getattr(event, "content", None)
        if not content or not content.parts:
            continue
        text = "".join(p.text for p in content.parts if getattr(p, "text", None)).strip()
        if not text or text.startswith(("System:", "Resume:")):
            continue
        messages.append({"role": "you" if event.author == "user" else "agent",
                         "text": text})
    return {"status": "success", "messages": messages}


# ---------------------------------------------------------------------------
# webhooks (external events wake parked sessions — docs/03 §dormancy)
# ---------------------------------------------------------------------------

class PortalEvent(BaseModel):
    user_id: str = "founder"
    session_id: str = ""
    application_id: str = ""
    kind: str
    confirmation_id: str = ""
    detail: str = ""


@app.post("/webhooks/portal_event")
async def portal_event(event: PortalEvent, request: Request):
    if not _verify_portal_token(request):
        return JSONResponse({"error": "bad portal token"}, status_code=401)
    if event.kind == "ping":
        return {"status": "ok", "ping": True}
    delta = {"pending_signals": []}
    if event.kind == "submission_confirmed":
        delta["current_step"] = Step.FOLLOW_UP
    elif event.kind == "result_posted":
        delta["current_step"] = Step.CLOSED
    if event.session_id:
        await resume_handler.wake(
            user_id=event.user_id or FOUNDER_ID,
            session_id=event.session_id,
            notice=f"Resume: portal event — {event.kind} {event.confirmation_id}".strip(),
            state_delta=delta,
        )
    return {"status": "ok"}


@app.post("/webhooks/deadline")
async def deadline_webhook(request: Request, background: BackgroundTasks):
    if not await _verify_oidc(request):
        return JSONResponse({"error": "bad oidc token"}, status_code=401)
    background.add_task(_deadline_scan_and_nudge)
    return JSONResponse({"status": "accepted"}, status_code=202)


async def _deadline_scan_and_nudge() -> None:
    """Deadline sentinel + proactive nudge (docs/08): anything newly CRITICAL
    is reported to the founder's chat, not just re-badged on the board."""
    result = await discovery_service.deadline_scan()
    critical = result.get("newly_critical") or []
    if not critical:
        return
    names = []
    for oid in critical:
        opp = await firestore.get_opportunity(oid)
        if opp:
            names.append(opp.get("name", oid))
    await _notify_founder(
        "System: deadline scan — newly CRITICAL: "
        + ", ".join(names)
        + ". Tell the founder, with days left and what starting now requires.")


# ---------------------------------------------------------------------------
# tasks (Scheduler → Pub/Sub; fast-ack, idempotent, no human present)
# ---------------------------------------------------------------------------

@app.post("/tasks/discover")
async def tasks_discover(request: Request, background: BackgroundTasks):
    if not await _verify_oidc(request):
        return JSONResponse({"error": "bad oidc token"}, status_code=401)
    background.add_task(_discover_and_score)
    return JSONResponse({"status": "accepted"}, status_code=202)


async def _discover_and_score() -> None:
    """Autonomous pipeline (docs/08): sweep, then wake the agent on a headless
    system session to score the new finds — discover → matchmake with no human
    in the loop. someone_is_there(): system sessions never ask questions."""
    from agents.co_founder.workflow import get_workflow

    summary = await discovery_service.run_sweep(get_workflow(), FOUNDER_ID)
    unscored = await firestore.list_unscored_opportunities()
    if not unscored:
        return
    session_id = "system-discovery"
    existing = await db_session_service.get_session(
        app_name=agent_app.name, user_id="system", session_id=session_id)
    if existing is None:
        await db_session_service.create_session(
            app_name=agent_app.name, user_id="system", session_id=session_id,
            state={"current_step": "IDLE", "active_application_id": "",
                   "checklist_status": [], "pending_signals": [],
                   "user:profile_id": FOUNDER_ID})
    await resume_handler.wake(
        user_id="system", session_id=session_id,
        notice=(f"System: discovery sweep complete ({summary.get('new', 0)} new, "
                f"{len(unscored)} unscored). Score every unscored opportunity against "
                "the Founder Profile via matchmaker_agent: shortlist strong fits with "
                "rationale, archive weak fits with a specific reason. Act and stop — "
                "nobody is here to answer questions."),
        state_delta={})
    # Proactive report: the agent comes back to the founder with the outcome.
    board = await pipeline_service.board(FOUNDER_ID)
    shortlisted = board.get("opportunities", {}).get("SHORTLISTED", [])
    names = ", ".join(o.get("name", "?") for o in shortlisted[:5])
    await _notify_founder(
        f"System: discovery sweep finished — {summary.get('new', 0)} new programs found; "
        f"{len(shortlisted)} currently shortlisted ({names}). Report what you found, "
        "urgent first, and recommend one concrete next move.")


@app.post("/tasks/deadline_scan")
async def tasks_deadline_scan(background: BackgroundTasks):
    background.add_task(discovery_service.deadline_scan)
    return JSONResponse({"status": "accepted"}, status_code=202)


class DistillRequest(BaseModel):
    feedback_id: str


@app.post("/tasks/distill")
async def tasks_distill(payload: DistillRequest):
    """Admin/retry route — the interactive path distills inline (docs/07)."""
    return await distill_service.run_distillation(payload.feedback_id)


# ---------------------------------------------------------------------------
# api (UI)
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def api_config():
    """Founder-facing config for the UI (docs/adr/001): persona name, workflow."""
    return {"persona_name": PERSONA_NAME, "workflow_id": os.environ.get("WORKFLOW_FILE", "")}


@app.get("/api/pipeline")
async def api_pipeline():
    return await pipeline_service.board(FOUNDER_ID)


@app.get("/api/audit")
async def api_audit():
    return {"status": "success", "audit": await firestore.list_audit()}


async def _founder_session_exists(session_id: str) -> bool:
    """Resolve identity server-side exactly once; callers never supply user_id."""
    if not session_id:
        return False
    session = await db_session_service.get_session(
        app_name=agent_app.name, user_id=FOUNDER_ID, session_id=session_id)
    return session is not None


# Browser panel routes live in an import-light module (docs/07, 18).
browser_routes.configure(
    app_name=agent_app.name,
    founder_id=FOUNDER_ID,
    session_exists=_founder_session_exists,
)
app.include_router(browser_routes.router)


@app.get("/api/applications/{application_id}")
async def api_application(application_id: str):
    app_doc = await firestore.get_application(application_id)
    if not app_doc:
        return JSONResponse({"error": "not found"}, status_code=404)
    pending = await firestore.find_pending_approval(application_id)
    app_doc["pending_approval_id"] = pending["id"] if pending else None
    return app_doc


class FeedbackRequest(BaseModel):
    session_id: str = ""
    application_id: str
    section_id: str
    type: str
    reason: str = ""
    edited_text: str = ""


@app.post("/api/feedback")
async def api_feedback(payload: FeedbackRequest):
    """Review controls — the distiller runs inline (synchronous, docs/07)."""
    result = await feedback_service.record_feedback(
        founder_id=FOUNDER_ID, application_id=payload.application_id,
        section_id=payload.section_id, feedback_type=payload.type,
        reason=payload.reason, edited_text=payload.edited_text)
    if payload.session_id:
        await resume_handler.wake(
            user_id=FOUNDER_ID, session_id=payload.session_id,
            notice="Resume: founder reviewed a section.",
            state_delta={"pending_signals": []})
    return result


class ApprovalResolve(BaseModel):
    decision: str  # grant | deny


@app.get("/api/approvals/pending")
async def api_approvals_pending():
    """The global approval inbox — every gate (submit, send_email) lands here."""
    return {"status": "success",
            "pending": await firestore.list_pending_approvals()}


@app.post("/api/approvals/{approval_id}/resolve")
async def api_resolve_approval(approval_id: str, payload: ApprovalResolve):
    return await approval_service.resolve(approval_id, payload.decision, FOUNDER_ID)


@app.post("/api/voice-note")
async def api_voice_note(file: UploadFile = File(...), context: str = Form("")):
    """Voice-note intake (Day 11): store audio artifact, transcribe, forward
    the extracted intent through the normal resume path."""
    audio = await file.read()
    name = f"voicenote_{FOUNDER_ID}_{uuid.uuid4().hex[:8]}.webm"
    storage.save_bytes(name, audio)
    result = await voice_service.transcribe(storage.artifact_path(name), context)
    return {"status": result.get("status"), "artifact": name,
            "transcript": result.get("transcript", ""),
            "extracted": result.get("extracted", {}),
            "message": result.get("message", "")}


@app.post("/api/ingest")
async def api_ingest(file: UploadFile = File(...)):
    """Company-document upload (docs/06 §bootstrap)."""
    from services import profile_service

    data = await file.read()
    name = f"companydoc_{FOUNDER_ID}_{uuid.uuid4().hex[:8]}_{file.filename}"
    storage.save_bytes(name, data)
    return await profile_service.ingest_document(FOUNDER_ID, "upload", file.filename or name, name)


# ---------------------------------------------------------------------------
# integrations (Drive + Gmail + Calendar — read-only OAuth, docs/12, adr/002)
# ---------------------------------------------------------------------------

def _oauth_flow(scopes: list[str] | None = None):
    """Loopback/web flow for the Connectors panel (docs/12).

    Client type comes from GOOGLE_OAUTH_CLIENT_TYPE: "installed" (Desktop app
    client — loopback redirects, local dev) or "web" (Web application client —
    works for both local loopback AND the Cloud Run https URL, as long as each
    redirect URI is registered on the client). Consent always re-prompted so
    scope changes mint a fresh refresh token. `scopes` defaults to the full
    grant; per-connector Connect buttons pass just their own scopes
    (incremental authorization — Google merges grants)."""
    from google_auth_oauthlib.flow import Flow

    from services import google_oauth

    base = os.environ.get("AGENT_BASE_URL", "http://127.0.0.1:8090")
    client_type = os.environ.get("GOOGLE_OAUTH_CLIENT_TYPE", "installed")
    return Flow.from_client_config(
        {client_type: {
            "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"]}},
        scopes=scopes or google_oauth.SCOPES,
        redirect_uri=f"{base}/api/integrations/google/callback",
    )


_pending_code_verifier: str | None = None  # single-founder app: one consent in flight


@app.get("/api/integrations/google/connect")
async def api_google_connect(connector: str = ""):
    """Connect button target: redirect the browser to Google consent.
    ?connector=drive|gmail|calendar requests only that connector's scopes on
    the founder account; ?connector=alex_mail consents AS alex@ruhu.ai (sign
    in as that account on the consent screen) — adr/001."""
    from fastapi.responses import RedirectResponse

    from services import google_oauth

    global _pending_code_verifier
    account = google_oauth.CONNECTOR_ACCOUNT.get(connector, "founder")
    flow = _oauth_flow(google_oauth.SCOPE_MAP.get(connector))
    url, _ = flow.authorization_url(
        prompt="consent", access_type="offline", include_granted_scopes="true",
        state=account)  # NB: string "true" — Google rejects the Python bool's "True"
    _pending_code_verifier = flow.code_verifier  # PKCE: the callback's exchange
    # must present the SAME verifier a fresh Flow would not have
    return RedirectResponse(url)


@app.get("/api/integrations/google/callback")
async def api_google_callback(code: str = "", state: str = "founder"):
    """Consent redirect: exchange the code, persist the refresh token for the
    account named in `state` (no restart needed), land back on the app."""
    from fastapi.responses import RedirectResponse

    from services import google_oauth

    if not code:
        return JSONResponse({"status": "error", "error": True,
                             "message": "no code in callback"}, status_code=400)
    global _pending_code_verifier
    flow = _oauth_flow(google_oauth.ALL_SCOPES)
    if _pending_code_verifier:
        flow.code_verifier = _pending_code_verifier
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("oauth token exchange failed: %s", exc)
        return JSONResponse({"status": "error", "error": True,
                             "message": f"token exchange failed: {exc}"}, status_code=400)
    finally:
        _pending_code_verifier = None
    token = flow.credentials.refresh_token
    if not token:
        return JSONResponse({"status": "error", "error": True,
                             "message": "no refresh token returned — consent again"}, status_code=400)
    account = state if state in google_oauth.ACCOUNT_ENV else "founder"
    google_oauth.save_refresh_token(token, account=account)
    return RedirectResponse(f"/?connected={account}")


@app.get("/api/connectors")
async def api_connectors():
    """Connector catalog (registry-as-data, services/connectors.py) — the
    Connections panel renders this and never hardcodes a connector."""
    import asyncio

    from services import connectors

    return {"status": "success",
            "connectors": await asyncio.to_thread(connectors.catalog)}


class GithubTokenRequest(BaseModel):
    token: str


@app.post("/api/connectors/github/token")
async def api_github_connect(payload: GithubTokenRequest):
    from services import connectors

    return await connectors.github_connect(payload.token)


@app.delete("/api/connectors/github/token")
async def api_github_disconnect():
    from services import connectors

    return await connectors.github_disconnect()


@app.get("/api/integrations")
async def api_integrations():
    """Connection panel state: per-connector status (scope-level), account,
    selections, and last seen."""
    import asyncio

    from services import google_oauth

    def _status() -> dict:
        """Blocking Google calls (tokeninfo, profile) — off the event loop."""
        any_configured = google_oauth.configured()
        return {
            "any": any_configured,
            "account_email": google_oauth.account_email() if any_configured else "",
            "connectors": {k: google_oauth.configured(k) for k in google_oauth.SCOPE_MAP},
        }

    status = await asyncio.to_thread(_status)
    integ = await firestore.get_integrations(FOUNDER_ID)
    return {"status": "success", "oauth_configured": status["any"],
            "account_email": status["account_email"],
            "connectors": status["connectors"],
            "drive": {"files": integ.get("drive_files", [])},
            "gmail": {"label": integ.get("gmail_label", "grants"),
                      "last_scan": await firestore.get_last_gmail_scan()},
            "alex_mail": {"last_scan": await firestore.get_last_alex_scan()}}


class DriveFileRequest(BaseModel):
    file_id: str


@app.get("/api/integrations/calendar/upcoming")
async def api_calendar_upcoming():
    """Calendar pane preview: upcoming events on the founder's primary calendar."""
    from services import calendar_adapter

    return await calendar_adapter.list_upcoming(days_ahead=7, max_results=5)


@app.post("/api/integrations/alex_mail/watch")
async def api_alex_mail_watch():
    """Register Gmail push notifications for Alex's inbox (adr/001 v2)."""
    from services import alex_mailbox

    topic = os.environ.get("ALEX_MAIL_PUBSUB_TOPIC", "")
    if not topic:
        return {"status": "error", "error": True,
                "message": "ALEX_MAIL_PUBSUB_TOPIC not set (projects/<p>/topics/<t>)"}
    return await alex_mailbox.start_watch(topic)
    name: str = ""
    action: str = "add"  # add | remove


@app.post("/api/integrations/drive/files")
async def api_drive_files(payload: DriveFileRequest):
    """Founder selects exactly which Drive files the agent may read (docs/12)."""
    integ = await firestore.get_integrations(FOUNDER_ID)
    files = integ.get("drive_files", [])
    if payload.action == "add":
        if not any(f["id"] == payload.file_id for f in files):
            files.append({"id": payload.file_id, "name": payload.name or payload.file_id})
    else:
        files = [f for f in files if f["id"] != payload.file_id]
    await firestore.update_integrations(FOUNDER_ID, drive_files=files)
    return {"status": "success", "drive_files": files}


class GmailLabelRequest(BaseModel):
    label: str


@app.post("/api/integrations/gmail/label")
async def api_gmail_label(payload: GmailLabelRequest):
    """The ONE label the agent may read (docs/12). Everything else in the
    mailbox does not exist as far as the agent is concerned."""
    await firestore.update_integrations(FOUNDER_ID, gmail_label=payload.label.strip() or "grants")
    return {"status": "success", "gmail_label": payload.label}


class DriveIngestRequest(BaseModel):
    file_id: str


@app.post("/api/ingest/drive")
async def api_ingest_drive(payload: DriveIngestRequest):
    """Ingest one founder-selected Drive file through the standard pipeline."""
    from services import drive_adapter, profile_service

    fetched = drive_adapter.fetch_file(payload.file_id)
    if fetched.get("status") != "success":
        return fetched
    return await profile_service.ingest_document(
        FOUNDER_ID, "google_drive", fetched.get("name", payload.file_id), fetched["artifact"])


@app.post("/tasks/gmail_scan")
async def tasks_gmail_scan(background: BackgroundTasks):
    background.add_task(_gmail_scan_and_report)
    return JSONResponse({"status": "accepted"}, status_code=202)


async def _gmail_scan_and_report() -> None:
    """Label-scoped portal mail → follow-ups + a founder chat report."""
    from services import gmail_adapter

    integ = await firestore.get_integrations(FOUNDER_ID)
    result = await gmail_adapter.scan(label=integ.get("gmail_label", "grants"))
    if result.get("status") != "success":
        await firestore.set_last_gmail_scan({"error": result.get("message", ""), "events": []})
        return
    events = result.get("events", [])
    await firestore.set_last_gmail_scan({"events": events, "scanned": result.get("scanned", 0)})
    if not events:
        return
    # Attach each event as a follow-up on the best-matching inflight application.
    apps = await firestore.list_inflight_applications(FOUNDER_ID)
    for a in apps:
        opp = await firestore.get_opportunity(a.get("opportunity_id", ""))
        a["opportunity_name"] = opp.get("name", "") if opp else ""
    for event in events:
        haystack = f"{event.get('subject', '')} {event.get('excerpt', '')}".lower()
        match = next((a for a in apps
                      if a.get("opportunity_name")
                      and a["opportunity_name"].lower() in haystack), None)
        if match:
            followups = match.get("followups", [])
            followups.append({"kind": f"email_{event['kind']}", "due_at": "",
                              "status": "PENDING",
                              "note": f"{event['from']}: {event['subject']}"})
            await firestore.update_application(match["id"], followups=followups)
    lines = "; ".join(f"[{e['kind']}] {e['subject']} (from {e['from']})" for e in events[:5])
    await _notify_founder(
        f"System: gmail scan of label '{integ.get('gmail_label', 'grants')}' found "
        f"{len(events)} new message(s): {lines}. Report them to the founder, "
        "matched to applications where possible, with what each one needs next.")


# ---------------------------------------------------------------------------
# Alex's mailbox (adr/001 v2): Pub/Sub push + manual/scheduled scan
# ---------------------------------------------------------------------------

@app.post("/webhooks/alex_mail")
async def alex_mail_push(request: Request, background: BackgroundTasks):
    """Gmail push notification (via Pub/Sub) for alex@ruhu.ai. Fast-ack; the
    history fetch + founder report run in the background. Token-checked when
    ALEX_MAIL_WEBHOOK_TOKEN is set (same posture as the portal webhook)."""
    token = os.environ.get("ALEX_MAIL_WEBHOOK_TOKEN", "")
    if token and request.query_params.get("token") != token:
        return JSONResponse({"error": "bad webhook token"}, status_code=401)
    background.add_task(_alex_mail_process)
    return JSONResponse({"status": "accepted"}, status_code=202)


@app.post("/tasks/alex_mail_scan")
async def tasks_alex_mail_scan(background: BackgroundTasks):
    background.add_task(_alex_mail_process)
    return JSONResponse({"status": "accepted"}, status_code=202)


async def _alex_mail_process() -> None:
    """New mail in Alex's inbox → classified events → follow-ups on matching
    inflight applications → the agent reports to the founder (wake)."""
    from services import alex_mailbox

    result = await alex_mailbox.fetch_history_events()
    if result.get("status") != "success":
        await firestore.set_last_alex_scan({"error": result.get("message", ""), "events": []})
        return
    events = result.get("events", [])
    if not events:
        return
    apps = await firestore.list_inflight_applications(FOUNDER_ID)
    for a in apps:
        opp = await firestore.get_opportunity(a.get("opportunity_id", ""))
        a["opportunity_name"] = opp.get("name", "") if opp else ""
    for event in events:
        haystack = f"{event.get('subject', '')} {event.get('excerpt', '')}".lower()
        match = next((a for a in apps
                      if a.get("opportunity_name")
                      and a["opportunity_name"].lower() in haystack), None)
        if match:
            followups = match.get("followups", [])
            followups.append({"kind": f"email_{event['kind']}", "due_at": "",
                              "status": "PENDING",
                              "note": f"{event['from']}: {event['subject']}"})
            await firestore.update_application(match["id"], followups=followups)
    lines = "; ".join(f"[{e['kind']}] {e['subject']} (from {e['from']})" for e in events[:5])
    await _notify_founder(
        f"System: {len(events)} new message(s) arrived in Alex's mailbox "
        f"(alex@ruhu.ai): {lines}. Report them to the founder, matched to "
        "applications where possible, with what each one needs next.")


# ---------------------------------------------------------------------------
# documents (docs/15): registry, downloads, Drive sync
# ---------------------------------------------------------------------------

@app.get("/api/applications/{application_id}/recon")
async def api_recon(application_id: str):
    """Vision-recon evidence for one application: the form_map the agent built
    and the screenshots it took getting there (docs/09 Tier 1, docs/10 §Fill
    report). This is the 'show your working' surface — judges ask for it."""
    import json as _json

    artifact = f"form_map_{application_id}.json"
    form_map = None
    if storage.exists(artifact):
        try:
            form_map = _json.loads(storage.read_text(artifact))
        except (ValueError, OSError):
            form_map = None

    prefix = f"recon_{application_id}_"
    shots = sorted((a for a in storage.list_artifacts()
                    if a.startswith(prefix) and a.endswith(".png")),
                   key=lambda a: a[len(prefix):], reverse=True)
    return {"status": "success", "application_id": application_id,
            "form_map": form_map,
            "form_map_artifact": artifact if form_map else None,
            "screenshots": shots[:8], "screenshot_total": len(shots)}


@app.get("/api/documents")
async def api_documents(session_id: str = "", application_id: str = ""):
    docs = await firestore.list_documents(
        FOUNDER_ID, session_id=session_id or None,
        application_id=application_id or None)
    return {"status": "success", "documents": docs}


@app.get("/api/artifacts/{name}/download")
async def api_download_artifact(name: str):
    """Stream a produced artifact. Name allowlist: no path traversal."""
    import re as _re

    if not _re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return JSONResponse({"error": "bad artifact name"}, status_code=400)
    path = os.path.realpath(storage.download_if_missing(name))
    if not path.startswith(os.path.realpath(storage._root())) or not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    from fastapi.responses import FileResponse
    from services import document_service

    ext = name.rsplit(".", 1)[-1].lower()
    images = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
    if ext in images:
        # No filename= : FileResponse would set Content-Disposition: attachment,
        # and the recon filmstrip needs these to render in an <img>.
        return FileResponse(path, media_type=images[ext])
    mime = document_service.mime_for(ext) if ext in ("docx", "xlsx", "pptx", "pdf") \
        else "application/octet-stream"
    return FileResponse(path, media_type=mime, filename=name)


@app.post("/api/documents/{artifact_name}/sync_drive")
async def api_sync_drive(artifact_name: str):
    """Founder-clicked copy of a produced document to Drive — the click IS the
    approval (docs/15 §security)."""
    from services import document_service, drive_adapter

    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9_.-]+", artifact_name):
        return JSONResponse({"error": "bad artifact name"}, status_code=400)
    path = storage.artifact_path(artifact_name)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    ext = artifact_name.rsplit(".", 1)[-1]
    result = drive_adapter.upload_file(
        artifact_name, path,
        document_service.mime_for(ext) if ext in ("docx", "xlsx", "pptx", "pdf")
        else "application/octet-stream")
    if result.get("status") == "success":
        await firestore.audit(actor="founder", action="drive_sync",
                              target=f"artifacts/{artifact_name}", result="success",
                              detail=f"copied to Drive file {result.get('file_id')}")
    return result


@app.get("/api/artifacts/{name}/preview")
async def api_preview_artifact(name: str):
    """View-only preview: PDFs stream directly; docx/xlsx/pptx convert to PDF
    via headless LibreOffice, cached as {name}.preview.pdf (docs/15)."""
    import re as _re

    if not _re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return JSONResponse({"error": "bad artifact name"}, status_code=400)
    path = os.path.realpath(storage.download_if_missing(name))
    if not path.startswith(os.path.realpath(storage._root())) or not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        from fastapi.responses import FileResponse

        mime = ("image/png" if name.lower().endswith(".png") else
                "image/webp" if name.lower().endswith(".webp") else "image/jpeg")
        return FileResponse(path, media_type=mime)
    if name.endswith(".pdf"):
        from fastapi.responses import FileResponse
        return FileResponse(path, media_type="application/pdf")
    from services import document_service

    preview_name = f"{name}.preview.pdf"
    preview_path = storage.artifact_path(preview_name)
    if not os.path.exists(preview_path):
        converted = document_service.convert_to_pdf(path, preview_path)
        if converted["status"] != "success":
            return JSONResponse({"error": converted["message"]}, status_code=503)
    from fastapi.responses import FileResponse
    return FileResponse(preview_path, media_type="application/pdf")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "app": agent_app.name,
            "session_store": SESSION_SERVICE_URI.split(":///")[0]}


async def _browser_startup() -> None:
    try:
        await browser_service.reconcile_all_runs()
    except Exception as exc:
        logging.getLogger(__name__).warning("browser run reconciliation unavailable: %s", exc)


# FastAPI ≥0.118 removed app.add_event_handler — register on the router.
app.router.on_startup.append(_browser_startup)
app.router.on_shutdown.append(browser_service.shutdown)


if os.path.isdir("app/static"):
    app.mount("/", StaticFiles(directory="app/static", html=True), name="ui")

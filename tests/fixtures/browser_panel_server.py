"""Tiny manual/UI fixture for docs/18 Browser panel verification."""

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response

ROOT = Path(__file__).resolve().parents[2]
app = FastAPI()
stopped = False


@app.get("/")
async def index():
    return FileResponse(ROOT / "app/static/index.html")


@app.get("/api/config")
async def config():
    return {"persona_name": "Alex", "workflow_id": "browser-fixture"}


@app.post("/session/new")
async def session_new():
    return {"session_id": "s-ui-browser"}


@app.get("/api/pipeline")
async def pipeline():
    return {"status": "success", "opportunities": {}, "applications": []}


@app.get("/api/audit")
async def audit():
    return {"status": "success", "audit": []}


@app.get("/api/approvals/pending")
async def approvals():
    return {"status": "success", "pending": []}


@app.get("/api/documents")
async def documents():
    return {"status": "success", "documents": []}


@app.get("/api/chat/{session_id}")
async def chat(session_id: str):
    return {"status": "success", "messages": []}


@app.get("/api/browser/state")
async def browser_state(session_id: str):
    active = not stopped
    return {
        "status": "success",
        "browse": {
            "active": active,
            "run_id": "ui-run-1",
            "url": "https://program.example/faq",
            "title": "Eligibility FAQ",
            "goal": "checking the FAQ",
            "screenshot_url": "/fixture-shot.png",
            "last_action": {
                "seq": 3,
                "kind": "open_link",
                "target": "Eligibility",
                "at": "2026-08-20T12:00:00+00:00",
            },
            "status": "active" if active else "closed",
        },
        "fill": None,
    }


@app.post("/api/browser/stop")
async def browser_stop():
    global stopped
    already = stopped
    stopped = True
    return {"status": "success", "already_closed": already}


@app.get("/fixture-shot.png")
async def fixture_shot():
    # A deterministic SVG frame served at the screenshot URL; the UI treats it
    # as an opaque image just as it does a pageshot PNG.
    svg = """<svg xmlns='http://www.w3.org/2000/svg' width='960' height='600'>
      <rect width='100%' height='100%' fill='#f6f4ed'/>
      <rect x='70' y='60' width='820' height='70' rx='12' fill='#e7e2d4'/>
      <text x='100' y='105' font-family='sans-serif' font-size='28' fill='#25231f'>Eligibility FAQ</text>
      <text x='100' y='210' font-family='sans-serif' font-size='22' fill='#4d4941'>Founders retain their equity.</text>
    </svg>"""
    return Response(svg, media_type="image/svg+xml")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766, log_level="warning")

"""FastAPI backend for the PBC Tracker web app.

Runs the agent once (cached), assembles a UI payload (tracker items with checks + citations,
per-item agent traces, follow-ups, flags, and cost), and serves a single self-contained
single-page app. One command:  `python -m pbc_agent.cli web --bundle data/sample_bundle`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from pbc_agent.agent.loop import run_agent
from pbc_agent.util.env import load_local_env

load_local_env()   # so `uvicorn pbc_agent.webapp.server:app` also finds a local .env

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="PBC Tracker")


def _bundle() -> str:
    return os.environ.get("PBC_BUNDLE", "data/sample_bundle")


@lru_cache(maxsize=4)
def _payload(bundle: str) -> dict:
    prefer_mock = not os.environ.get("ANTHROPIC_API_KEY")
    state = run_agent(bundle, prefer_mock=prefer_mock)
    report = state.to_report()

    # attach per-item agent traces (plan + tool calls) for the "Audit detail" panel
    traces: dict[str, list] = {}
    for iid in state.items:
        for t in state.trace.for_item(iid):
            traces.setdefault(iid, []).append({
                "message": t.message_source, "subject": t.subject,
                "models": t.models_used, "plan": t.plan_notes,
                "tools": [{"name": c.name, "summary": c.result_summary, "ok": c.ok}
                          for c in t.tool_calls],
            })
    report["traces"] = traces
    report["provider"] = "live" if not prefer_mock else "mock"
    report["categories"] = {iid: state.items[iid].category for iid in state.items}
    report["descriptions"] = {iid: state.items[iid].description for iid in state.items}
    return report


@app.get("/api/report")
def report() -> JSONResponse:
    return JSONResponse(_payload(_bundle()))


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((_STATIC / "index.html").read_text())


def serve(bundle: str = "data/sample_bundle", host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn
    os.environ["PBC_BUNDLE"] = bundle
    _payload.cache_clear()
    print(f"PBC Tracker → http://{host}:{port}   (bundle: {bundle})")
    uvicorn.run(app, host=host, port=port, log_level="warning")

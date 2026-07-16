"""FastAPI backend for the PBC Tracker web app.

Runs the agent once (cached), assembles a UI payload (tracker items with checks + citations,
per-item agent traces, follow-ups, flags, and cost), and serves a single self-contained
single-page app. One command:  `python -m pbc_agent.cli web --bundle data/sample_bundle`.
"""

from __future__ import annotations

import atexit
import io
import os
import shutil
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from pbc_agent.agent.loop import _resolve, run_agent
from pbc_agent.util.env import load_local_env

load_local_env()   # so `uvicorn pbc_agent.webapp.server:app` also finds a local .env

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="PBC Tracker")

# The bundle currently being viewed. Starts from --bundle / PBC_BUNDLE if that path exists,
# and is replaced when a tester uploads their own audit. Localhost single-user, so a module
# global is sufficient.
_CURRENT: dict[str, str | None] = {"bundle": None}


def _current_bundle() -> str | None:
    if _CURRENT["bundle"]:
        return _CURRENT["bundle"]
    default = os.environ.get("PBC_BUNDLE", "data/sample_bundle")
    return default if Path(default).exists() else None


def _live() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# --- hosted mode + global spend cap (protects a server-held API budget) ---------------
_SPENT: dict[str, float] = {"usd": 0.0}
_MAX_UPLOAD_BYTES = 80 * 1024 * 1024   # reject uploads larger than 80 MB


def _hosted() -> bool:
    """Deployed/multi-user mode: the server holds the key; the in-browser override is off."""
    return os.environ.get("PBC_HOSTED", "").strip() in {"1", "true", "yes"}


def _spend_cap() -> float:
    try:
        return float(os.environ.get("PBC_MAX_SPEND_USD", "0") or 0)
    except ValueError:
        return 0.0


def _use_live() -> bool:
    """Live only if a key is present AND we're under the cumulative spend cap (if one is set)."""
    if not _live():
        return False
    cap = _spend_cap()
    return cap <= 0 or _SPENT["usd"] < cap


def _record_spend(usd: float) -> None:
    _SPENT["usd"] = round(_SPENT["usd"] + float(usd or 0), 4)


@lru_cache(maxsize=4)
def _payload(bundle: str) -> dict:
    prefer_mock = not _use_live()
    state = run_agent(bundle, prefer_mock=prefer_mock)
    _record_spend(state.budget.summary()["usd"])
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
    report["hosted"] = _hosted()
    report["spend"] = {"usd": round(_SPENT["usd"], 4), "cap": _spend_cap()}
    return report


@app.get("/api/report")
def report() -> JSONResponse:
    bundle = _current_bundle()
    if not bundle:
        return JSONResponse({"empty": True, "live": _use_live(), "hosted": _hosted()})
    try:
        return JSONResponse(_payload(bundle))
    except Exception as e:
        return JSONResponse({"error": f"Could not run this audit: {e}", "empty": True,
                             "hosted": _hosted()},
                            status_code=400)


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract a zip, refusing entries that escape the destination (zip-slip guard)."""
    root = dest.resolve()
    for member in zf.namelist():
        target = (dest / member).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"unsafe path in archive: {member}")
    zf.extractall(dest)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> JSONResponse:
    """Upload an audit to run: a ``.zip`` of the bundle, or a ``.mbox`` mailbox.

    The bundle must contain a PBC-list PDF, a client-profile PDF, and the mailbox (an ``emails/``
    folder of ``.eml`` files or a ``.mbox``). Files are held in a temporary directory on the
    tester's own machine only; nothing is uploaded anywhere else.
    """
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"error": f"That upload is {len(data) // (1024*1024)} MB; the limit is "
                      f"{_MAX_UPLOAD_BYTES // (1024*1024)} MB."},
            status_code=413)
    name = Path(file.filename or "upload.bin").name
    up = Path(tempfile.mkdtemp(prefix="pbc_upload_"))
    try:
        if name.lower().endswith(".zip"):
            _safe_extract(zipfile.ZipFile(io.BytesIO(data)), up)
        else:
            (up / name).write_bytes(data)
    except (zipfile.BadZipFile, ValueError) as e:
        return JSONResponse({"error": f"Could not read the upload: {e}"}, status_code=400)

    try:
        _resolve(up)   # confirms profile + PBC list + mailbox are present
    except FileNotFoundError:
        return JSONResponse(
            {"error": "That upload is missing pieces. Include a PBC-list PDF, a client-profile "
                      "PDF, and the mailbox (an emails/ folder of .eml files, or a .mbox)."},
            status_code=400)

    # Run the audit BEFORE repointing the app at it, so a bundle that passes _resolve but fails
    # to actually run (e.g. a corrupt PDF) doesn't drop the previously-loaded good audit.
    try:
        payload = _payload(str(up))
    except Exception as e:
        shutil.rmtree(up, ignore_errors=True)
        return JSONResponse({"error": f"Could not run this audit: {e}"}, status_code=400)
    _remember_upload(str(up))
    _CURRENT["bundle"] = str(up)
    return JSONResponse(payload)


# Temp upload dirs — keep only the current one; clean the rest (and all on shutdown).
_UPLOAD_DIRS: list[str] = []


def _remember_upload(path: str) -> None:
    _UPLOAD_DIRS.append(path)
    for old in _UPLOAD_DIRS[:-1]:
        shutil.rmtree(old, ignore_errors=True)
    _UPLOAD_DIRS[:] = _UPLOAD_DIRS[-1:]


@atexit.register
def _cleanup_uploads() -> None:
    for d in _UPLOAD_DIRS:
        shutil.rmtree(d, ignore_errors=True)


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Liveness probe for the container host (Render healthCheckPath)."""
    return JSONResponse({"ok": True})


@app.get("/api/status")
def status() -> JSONResponse:
    live = _use_live()
    return JSONResponse({"provider": "live" if live else "mock", "live": live,
                         "hosted": _hosted(),
                         "spend": {"usd": round(_SPENT["usd"], 4), "cap": _spend_cap()}})


class KeyIn(BaseModel):
    key: str = ""


@app.post("/api/key")
def set_key(payload: KeyIn) -> JSONResponse:
    """Hold a tester-supplied API key in process memory only (never written to disk).

    The bespoke web app binds 127.0.0.1, so the key stays on the tester's machine. Setting a key
    flips both the tracker and the live-test harness to real Claude; clearing it reverts to the
    offline mock. The tracker payload cache is invalidated so the next load re-runs live.

    Disabled in hosted mode: the deployed server holds its own key (with a spend cap), and a public
    visitor must not be able to replace or clear it.
    """
    if _hosted():
        return JSONResponse(
            {"error": "Key entry is disabled on the hosted demo; it runs on the server's key.",
             "hosted": True}, status_code=403)
    key = (payload.key or "").strip()
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    _payload.cache_clear()
    live = _live()
    return JSONResponse({"provider": "live" if live else "mock", "live": live})


class LiveTestIn(BaseModel):
    n: int = 12
    seed: int = 7
    force_mock: bool = False


@app.post("/api/livetest")
def livetest(payload: LiveTestIn) -> JSONResponse:
    """Generate fresh random adversarial traps and run each through the agent path.

    Uses real Claude when a key has been supplied (else the offline mock). The verdict for each
    trap is deterministic, so a correct live run catches every case — the "live == offline"
    guarantee, exercised on never-before-seen data.
    """
    from pbc_agent.eval.live_harness import run_live_traps
    n = max(1, min(60, int(payload.n)))
    prefer_mock = bool(payload.force_mock) or not _use_live()
    out = run_live_traps(n=n, seed=int(payload.seed), prefer_mock=prefer_mock)
    try:
        _record_spend((out.get("cost") or {}).get("usd") or out.get("usd") or 0)
    except AttributeError:
        pass
    return JSONResponse(out)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((_STATIC / "index.html").read_text())


def serve(bundle: str = "data/sample_bundle", host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn
    os.environ["PBC_BUNDLE"] = bundle
    _payload.cache_clear()
    print(f"PBC Tracker → http://{host}:{port}   (bundle: {bundle})")
    uvicorn.run(app, host=host, port=port, log_level="warning")

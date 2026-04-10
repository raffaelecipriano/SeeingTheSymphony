"""
Seeing the Symphony — FastAPI Server
Phase 1: MusicXML parser endpoint.

Run with:
    uvicorn backend.main:app --reload --port 8000

Interactive docs: http://localhost:8000/docs
"""
import asyncio
import multiprocessing
import os
import tempfile
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse


# ---------------------------------------------------------------------------
# Background job state
# ---------------------------------------------------------------------------
# ProcessPoolExecutor gives each parse job its own Python interpreter and GIL,
# so the event loop in the main process is never starved by CPU-bound music21 work.
_mp_manager = multiprocessing.Manager()

# _stage_map is a Manager dict so worker processes can write to it directly.
# Holds job_id -> current stage name while a job is in progress.
_stage_map: Any = _mp_manager.dict()

_executor = ProcessPoolExecutor(max_workers=2)

# Each entry: {"status": "processing"} | {"status": "complete", "data": <dict>}
#                                       | {"status": "error", "detail": str}
_jobs: Dict[str, Dict[str, Any]] = {}

_DASHBOARD_HTML = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")

app = FastAPI(
    title="Seeing the Symphony — Analysis API",
    description=(
        "Backend analysis pipeline for Seeing the Symphony. "
        "Parses MusicXML scores and returns a fully structured ScoreData object "
        "containing notes, instruments, sections, dynamic spans, and tempo events. "
        "Phase 1 implementation: parser + harmonic analyzer + auto-annotator."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS — allow all origins so the /docs UI and React dev server can make requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["Health"])
def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "Seeing the Symphony API", "version": "0.1.0"}



@app.get("/", response_class=HTMLResponse, tags=["Dashboard"], include_in_schema=False)
def dashboard():
    """Parse Pipeline Dashboard — drag-and-drop MusicXML upload with timing visualisation."""
    return HTMLResponse(content=_DASHBOARD_HTML)


def _parse_worker(stage_map: Any, job_id: str, tmp_path: str, file_bytes: bytes) -> dict:
    """
    Runs in a worker process (separate GIL). Writes the current stage name into
    stage_map (a Manager dict visible to the main process) before each pipeline
    step. Returns a plain dict so the result can be pickled back to the main process.
    """
    def _stage(name: str) -> None:
        try:
            stage_map[job_id] = name
        except Exception:
            pass

    try:
        from backend.models.score_data import ScoreData as _ScoreData  # absolute import required in subprocess
        score_data = _ScoreData.from_xml(tmp_path, file_bytes, stage_callback=_stage)
        return score_data.model_dump()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.post(
    "/parse",
    tags=["Analysis Pipeline"],
    summary="Submit a MusicXML file for async parsing — returns a job_id immediately",
    response_description="A job_id to poll via GET /parse/status/{job_id}.",
    status_code=202,
)
async def parse_score(
    file: UploadFile = File(
        ...,
        description=(
            "A MusicXML file (.xml or .mxl) to parse. "
            "The system ingests pitch, rhythm, dynamics, articulation, "
            "tempo markings, and instrument definitions. "
            "Returns a ScoreData object with all notes at concert pitch."
        ),
    ),
) -> dict:
    """
    **POST /parse** — Async core analysis endpoint.

    Accepts a MusicXML file, immediately returns `{job_id}`, and runs the full
    Phase 1 analysis pipeline in a background thread:

    1. **Score Parser** — Ingests MusicXML via music21. Creates InstrumentMeta and
       NoteObject for every note. Applies transposition to concert pitch. Computes
       `time_onset` in seconds via the tempo map.
    2. **Harmonic Analyzer** — Detects key area and creates ScoreSection with
       embedded HarmonicEvent objects. Marked `is_auto_analyzed=True`.
    3. **Tempo Extractor** — Extracts MetronomeMarks and creates TempoEvent objects.
    4. **Dynamic Extractor** — Extracts hairpin markings as DynamicSpan objects.
       Stamps `dynamic_span_id` and `dynamic_span_position` on affected NoteObjects.
    5. **Auto-Annotator** — Infers `attack`, `release`, and `technique` from
       articulation markings.
    6. **Shape Variant Pre-pass** — Assigns deterministic `variant_index` to every
       NoteObject (seeded by note position; same score always renders identically).

    Poll **GET /parse/status/{job_id}** to retrieve the result. All objects store
    **concert pitch** — transposition is applied during parsing and never appears in
    the data model.
    """
    # Validate file type
    filename = file.filename or "upload.xml"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".xml", ".mxl", ".musicxml"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Please upload a .xml or .mxl MusicXML file.",
        )

    # Read file bytes
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Write to temp file (music21 requires a file path); the worker cleans it up
    suffix = ext if ext in (".xml", ".mxl") else ".xml"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "processing"}

    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_executor, _parse_worker, _stage_map, job_id, tmp_path, file_bytes)

    def _done(fut: asyncio.Future) -> None:
        try:
            del _stage_map[job_id]
        except KeyError:
            pass
        exc = fut.exception()
        if exc is not None:
            _jobs[job_id] = {"status": "error", "detail": str(exc)}
        else:
            _jobs[job_id] = {"status": "complete", "data": fut.result()}

    future.add_done_callback(_done)

    return {"job_id": job_id}


@app.get(
    "/parse/status/{job_id}",
    tags=["Analysis Pipeline"],
    summary="Poll the status of an async parse job",
    response_description='{"status": "processing"} or {"status": "complete", "data": <ScoreData>}',
)
async def parse_status(job_id: str) -> dict:
    """
    **GET /parse/status/{job_id}** — Poll a background parse job.

    Returns one of:
    - `{"status": "processing"}` — parse is still running.
    - `{"status": "complete", "data": <ScoreData>}` — parse finished successfully.
    - `{"status": "error", "detail": <str>}` — parse failed; `detail` describes the error.
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    if job["status"] == "complete":
        return {"status": "complete", "data": job["data"]}  # already a dict from model_dump()
    if job["status"] == "processing":
        return {"status": "processing", "stage": _stage_map.get(job_id, "starting")}
    return job  # error

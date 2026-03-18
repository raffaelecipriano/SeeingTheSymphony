"""
Seeing the Symphony — FastAPI Server
Phase 1: MusicXML parser endpoint.

Run with:
    uvicorn backend.main:app --reload --port 8000

Interactive docs: http://localhost:8000/docs
"""
import os
import tempfile

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .models.score_data import ScoreData

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


@app.get("/", tags=["Health"])
def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "Seeing the Symphony API", "version": "0.1.0"}


@app.post(
    "/parse",
    response_model=ScoreData,
    tags=["Analysis Pipeline"],
    summary="Parse a MusicXML file and return ScoreData",
    response_description="The fully structured ScoreData object for the uploaded score.",
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
) -> ScoreData:
    """
    **POST /parse** — Core analysis endpoint.

    Accepts a MusicXML file and runs the full Phase 1 analysis pipeline:

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

    Returns a `ScoreData` object serialized as JSON. All objects store **concert pitch**
    — transposition is applied during parsing and never appears in the data model.
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

    # Write to temp file (music21 requires a file path)
    suffix = ext if ext in (".xml", ".mxl") else ".xml"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        score_data = ScoreData.from_xml(tmp_path, file_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to parse MusicXML: {exc}",
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return score_data

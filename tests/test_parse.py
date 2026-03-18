"""
test_parse.py — Phase 1 integration test for the /parse endpoint.

Usage:
    1. Start the server:  uvicorn backend.main:app --reload --port 8000
    2. Run this script:   python tests/test_parse.py

Prints a summary of the returned ScoreData and asserts correctness.
Exits with code 1 if any assertion fails.
"""

import json
import sys
import os
import pathlib

# Allow running from project root: python tests/test_parse.py
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed. Run: pip install requests")
    sys.exit(1)

FIXTURE_PATH = ROOT / "fixtures" / "bach_chorale.xml"
ENDPOINT = "http://localhost:8000/parse"


def assert_field(label: str, value, condition: bool, expected_desc: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    desc = f"  [{status}] {label}: {value!r}"
    if not condition:
        desc += f"  ← expected {expected_desc}"
    print(desc)
    if not condition:
        sys.exit(1)


def main() -> None:
    print("=" * 60)
    print("Seeing the Symphony — /parse endpoint test")
    print("=" * 60)
    print(f"Fixture : {FIXTURE_PATH}")
    print(f"Endpoint: {ENDPOINT}")
    print()

    # ----------------------------------------------------------------
    # Health check
    # ----------------------------------------------------------------
    try:
        health = requests.get("http://localhost:8000/", timeout=5)
        health.raise_for_status()
    except Exception as e:
        print(f"ERROR: Cannot reach server at localhost:8000 — {e}")
        print("Start the server with:  uvicorn backend.main:app --reload --port 8000")
        sys.exit(1)

    # ----------------------------------------------------------------
    # POST /parse
    # ----------------------------------------------------------------
    if not FIXTURE_PATH.exists():
        print(f"ERROR: Fixture not found at {FIXTURE_PATH}")
        sys.exit(1)

    with open(FIXTURE_PATH, "rb") as f:
        response = requests.post(
            ENDPOINT,
            files={"file": ("bach_chorale.xml", f, "application/xml")},
            timeout=60,
        )

    if response.status_code != 200:
        print(f"ERROR: HTTP {response.status_code}")
        print(response.text[:500])
        sys.exit(1)

    data = response.json()

    print("--- ScoreData Summary ---")
    print()

    # ----------------------------------------------------------------
    # Title
    # ----------------------------------------------------------------
    title = data.get("title", "")
    assert_field("title", title, isinstance(title, str) and len(title) > 0, "non-empty string")

    # ----------------------------------------------------------------
    # Composer
    # ----------------------------------------------------------------
    composer = data.get("composer", "")
    assert_field("composer", composer, isinstance(composer, str) and len(composer) > 0, "non-empty string")

    # ----------------------------------------------------------------
    # Instrument count — fixture has 4 parts (S, A, T, B)
    # ----------------------------------------------------------------
    instruments = data.get("instruments", [])
    inst_count = len(instruments)
    assert_field("instrument_count", inst_count, inst_count == 4, "== 4")

    print(f"  Instruments:")
    for inst in instruments:
        print(f"    [{inst['score_order']}] {inst['name']} ({inst['instrument_family']}) "
              f"pitch {inst['pitch_range_low']}–{inst['pitch_range_high']} midi")

    # ----------------------------------------------------------------
    # Note count — 4 parts × ~14 notes per part ≈ ≥ 56 notes
    # ----------------------------------------------------------------
    notes = data.get("notes", [])
    note_count = len(notes)
    assert_field("note_count", note_count, note_count >= 50, ">= 50")

    # ----------------------------------------------------------------
    # Section count — at least 1 section
    # ----------------------------------------------------------------
    sections = data.get("sections", [])
    section_count = len(sections)
    assert_field("section_count", section_count, section_count >= 1, ">= 1")

    if sections:
        s0 = sections[0]
        he = s0.get("harmonic_events", [])
        assert_field(
            "  section[0].harmonic_events[0].key_tonic",
            he[0].get("key_tonic") if he else None,
            bool(he),
            "non-empty",
        )

    # ----------------------------------------------------------------
    # Tempo event count — fixture has 1 explicit tempo mark
    # ----------------------------------------------------------------
    tempo_events = data.get("tempo_events", [])
    tempo_count = len(tempo_events)
    assert_field("tempo_event_count", tempo_count, tempo_count >= 1, ">= 1")
    if tempo_events:
        bpm = tempo_events[0].get("bpm", 0)
        assert_field("  tempo_events[0].bpm", bpm, 60 <= bpm <= 200, "between 60 and 200")

    # ----------------------------------------------------------------
    # Duration in seconds — 4 measures at 72 BPM = 4 × (4 × 60/72) ≈ 13.3 s
    # ----------------------------------------------------------------
    duration = data.get("duration_seconds", 0.0)
    assert_field("duration_seconds", round(duration, 2), duration > 5.0, "> 5.0")

    # ----------------------------------------------------------------
    # Collision clusters — must be empty at parse time (per data model)
    # ----------------------------------------------------------------
    clusters = data.get("collision_clusters", [])
    assert_field("collision_clusters (empty at parse time)", len(clusters), len(clusters) == 0, "== 0")

    # ----------------------------------------------------------------
    # musicxml_hash — MD5 string
    # ----------------------------------------------------------------
    mx_hash = data.get("musicxml_hash", "")
    assert_field("musicxml_hash", mx_hash, len(mx_hash) == 32, "32-char MD5 hex string")

    # ----------------------------------------------------------------
    # Spot-check a NoteObject
    # ----------------------------------------------------------------
    n0 = notes[0]
    assert_field("notes[0].note_id", n0.get("note_id"), bool(n0.get("note_id")), "non-empty string")
    assert_field("notes[0].pitch_midi", n0.get("pitch_midi"), 0 <= n0.get("pitch_midi", -1) <= 127, "0–127")
    assert_field("notes[0].pitch_name", n0.get("pitch_name"), bool(n0.get("pitch_name")), "non-empty string")
    assert_field("notes[0].time_onset", n0.get("time_onset"), n0.get("time_onset", -1) >= 0.0, ">= 0.0")
    assert_field("notes[0].time_duration", n0.get("time_duration"), n0.get("time_duration", 0) > 0.0, "> 0.0")
    assert_field("notes[0].variant_index", n0.get("variant_index"), n0.get("variant_index") is not None, "not null")

    print()
    print("=" * 60)
    print("ALL ASSERTIONS PASSED")
    print("=" * 60)
    print()
    print(f"  Title            : {title}")
    print(f"  Composer         : {composer}")
    print(f"  Instruments      : {inst_count}")
    print(f"  Notes            : {note_count}")
    print(f"  Sections         : {section_count}")
    print(f"  Tempo events     : {tempo_count}")
    print(f"  Duration         : {duration:.2f} s")
    print(f"  MusicXML hash    : {mx_hash}")


if __name__ == "__main__":
    main()

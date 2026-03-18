"""
Score Parser — Steps 1, 7, 9, 11 of the parsing pipeline.
Orchestrates the full parse via ScoreData.from_xml().

Step 1:  Ingest MusicXML, create InstrumentMeta and NoteObject.
         Apply transposition to concert pitch.
         Compute time_onset from the tempo map.
Step 7:  Hash check (MD5 of source bytes).
Step 9:  Shape Variant pre-pass (assign variant_index).
Step 11: Lightweight validation → ValidationResult.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Optional

import music21
from music21 import (
    articulations as m21_articulations,
    dynamics as m21_dynamics,
    expressions as m21_expressions,
    note as m21_note,
    chord as m21_chord,
)

from ..models.score_data import (
    ScoreData, InstrumentMeta, NoteObject, ValidationResult, ValidationMessage,
)
from ..models.enums import (
    InstrumentFamilyEnum, DynamicMarkingEnum, SforzandoEnum,
    ArticulationEnum, TechniqueEnum, AttackEnum, ReleaseEnum,
    ValidationLevelEnum,
)
from .tempo_extractor import extract_tempo_events, ql_to_seconds
from .harmonic_analyzer import analyze_harmony
from .dynamic_extractor import extract_dynamic_spans, stamp_dynamic_spans
from .auto_annotator import auto_annotate_notes

# ---------------------------------------------------------------------------
# Instrument family detection
# ---------------------------------------------------------------------------
_FAMILY_KEYWORDS = {
    InstrumentFamilyEnum.STRINGS: [
        "violin", "viola", "cello", "violoncello", "contrabass", "double bass",
        "bass viol", "viol", "string",
    ],
    InstrumentFamilyEnum.WOODWINDS: [
        "flute", "oboe", "clarinet", "bassoon", "saxophone", "piccolo",
        "cor anglais", "english horn", "contrabassoon",
    ],
    InstrumentFamilyEnum.BRASS: [
        "trumpet", "trombone", "horn", "tuba", "cornet", "flugelhorn",
        "euphonium", "bugle",
    ],
    InstrumentFamilyEnum.PERCUSSION: [
        "timpani", "drum", "cymbal", "xylophone", "marimba", "vibraphone",
        "glockenspiel", "percussion", "snare", "bass drum", "triangle",
        "tambourine", "castanets",
    ],
    InstrumentFamilyEnum.KEYBOARD: [
        "piano", "organ", "harpsichord", "celesta", "keyboard",
        "clavinet", "accordion",
    ],
    InstrumentFamilyEnum.VOICE: [
        "soprano", "alto", "tenor", "bass", "baritone", "mezzo", "voice",
        "choir", "chorus", "vocal", "singer",
    ],
}


def _detect_family(name: str) -> InstrumentFamilyEnum:
    lower = name.lower()
    for family, keywords in _FAMILY_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return family
    return InstrumentFamilyEnum.STRINGS  # default


# ---------------------------------------------------------------------------
# Dynamic marking map
# ---------------------------------------------------------------------------
M21_DYNAMIC_MAP = {
    "ppp": DynamicMarkingEnum.PPP,
    "pp":  DynamicMarkingEnum.PP,
    "p":   DynamicMarkingEnum.P,
    "mp":  DynamicMarkingEnum.MP,
    "mf":  DynamicMarkingEnum.MF,
    "f":   DynamicMarkingEnum.F,
    "ff":  DynamicMarkingEnum.FF,
    "fff": DynamicMarkingEnum.FFF,
    "fp":  DynamicMarkingEnum.FP,
}

M21_SFORZANDO_MAP = {
    "sf":   SforzandoEnum.SF,
    "fz":   SforzandoEnum.FZ,
    "sfz":  SforzandoEnum.SFZ,
    "fzfz": SforzandoEnum.FZFZ,
    "sffz": SforzandoEnum.SFFZ,
}

# ---------------------------------------------------------------------------
# Articulation + technique mapping
# ---------------------------------------------------------------------------
def _map_articulations(note_obj) -> list[ArticulationEnum]:
    arts = []
    for a in note_obj.articulations:
        cls_name = type(a).__name__.lower()
        if "staccato" in cls_name:
            arts.append(ArticulationEnum.STACCATO)
        elif "tenuto" in cls_name:
            arts.append(ArticulationEnum.TENUTO)
        elif "strongaccent" in cls_name or "marcato" in cls_name:
            arts.append(ArticulationEnum.MARCATO)
        elif "accent" in cls_name:
            arts.append(ArticulationEnum.ACCENT)
        elif "tremolo" in cls_name:
            arts.append(ArticulationEnum.TREMOLO)
        elif "portato" in cls_name:
            arts.append(ArticulationEnum.PORTATO)
    return arts if arts else [ArticulationEnum.NORMAL]


def _map_technique(note_obj) -> list[TechniqueEnum]:
    for expr in getattr(note_obj, "expressions", []):
        cls_name = type(expr).__name__.lower()
        text = getattr(expr, "content", "").lower()
        combined = cls_name + " " + text
        if "pizzicato" in combined or "pizz" in combined:
            return [TechniqueEnum.PIZZICATO]
        if "col legno" in combined or "col_legno" in combined:
            return [TechniqueEnum.COL_LEGNO]
        if "sul ponticello" in combined or "ponticello" in combined:
            return [TechniqueEnum.SUL_PONTICELLO]
        if "harmonics" in combined or "harmonic" in combined:
            return [TechniqueEnum.HARMONICS]
        if "sul tasto" in combined or "tasto" in combined:
            return [TechniqueEnum.SUL_TASTO]
        if "flautando" in combined:
            return [TechniqueEnum.FLAUTANDO]
        if "snap pizzicato" in combined or "bartok" in combined:
            return [TechniqueEnum.SNAP_PIZZICATO]
    return [TechniqueEnum.NORMAL]


def _map_attack_release(articulations: list[ArticulationEnum]) -> tuple[AttackEnum, ReleaseEnum]:
    if ArticulationEnum.STACCATO in articulations:
        return AttackEnum.SHARP, ReleaseEnum.CLIPPED
    if ArticulationEnum.MARCATO in articulations:
        return AttackEnum.SHARP, ReleaseEnum.NORMAL
    if ArticulationEnum.ACCENT in articulations:
        return AttackEnum.SHARP, ReleaseEnum.NORMAL
    if ArticulationEnum.TENUTO in articulations:
        return AttackEnum.SOFT, ReleaseEnum.TAPERED
    if ArticulationEnum.LEGATO in articulations:
        return AttackEnum.SOFT, ReleaseEnum.TAPERED
    if ArticulationEnum.PORTATO in articulations:
        return AttackEnum.NORMAL, ReleaseEnum.CLIPPED
    return AttackEnum.NORMAL, ReleaseEnum.NORMAL


# ---------------------------------------------------------------------------
# InstrumentMeta extraction
# ---------------------------------------------------------------------------
def instrument_meta_from_part(part: music21.stream.Part, score_order: int) -> InstrumentMeta:
    """Construct InstrumentMeta from a music21 Part."""
    # Get instrument object
    m21_inst = part.getInstrument(returnDefault=True)
    name = m21_inst.instrumentName or part.partName or f"Part {score_order + 1}"
    abbr = m21_inst.instrumentAbbreviation or name[:4]
    family = _detect_family(name)

    # Transposition: semitones written→concert
    transposition = 0
    if m21_inst.transposition is not None:
        try:
            transposition = int(m21_inst.transposition.semitones)
        except Exception:
            transposition = 0

    # Pitch range from notes in the part
    flat = part.flatten()
    pitches = []
    for n in flat.getElementsByClass(m21_note.Note):
        pitches.append(n.pitch.midi)
    for c in flat.getElementsByClass(m21_chord.Chord):
        for p in c.pitches:
            pitches.append(p.midi)

    pitch_low = min(pitches) if pitches else 21
    pitch_high = max(pitches) if pitches else 108

    # Sanitise instrument_id: use part.id, fallback to slugified name
    inst_id = part.id or re.sub(r"[^a-zA-Z0-9_]", "_", name)

    return InstrumentMeta(
        instrument_id=inst_id,
        name=name,
        abbreviation=abbr,
        instrument_family=family,
        score_order=score_order,
        transposition=transposition,
        player_count=1,
        pitch_range_low=pitch_low,
        pitch_range_high=pitch_high,
    )


# ---------------------------------------------------------------------------
# NoteObject extraction
# ---------------------------------------------------------------------------
def _get_active_dynamic(flat_part: music21.stream.Stream, offset: float) -> Optional[DynamicMarkingEnum]:
    """Find the most recent Dynamic marking at or before offset."""
    result = None
    for d in flat_part.getElementsByClass(m21_dynamics.Dynamic):
        if float(d.offset) <= offset:
            result = M21_DYNAMIC_MAP.get(d.value)
        else:
            break
    return result


def note_object_from_m21(note_obj, instrument_meta: InstrumentMeta,
                          bar: int, beat: int, subdivision: int,
                          time_onset: float, time_duration: float,
                          note_id: str, voice: int,
                          active_dynamic: Optional[DynamicMarkingEnum]) -> NoteObject:
    """Construct a NoteObject from a music21 Note (pitch already at concert pitch)."""
    pitch_midi = note_obj.pitch.midi
    pitch_name = note_obj.pitch.nameWithOctave  # e.g. 'G4'

    # Dynamic
    dyn_marking = active_dynamic
    dyn_value = instrument_meta.get_dynamic_value(dyn_marking) if dyn_marking else 0.55

    # Sforzando check
    sfz = None
    for a in note_obj.articulations:
        cls_name = type(a).__name__.lower()
        for k, v in M21_SFORZANDO_MAP.items():
            if k in cls_name:
                sfz = v
                break

    arts = _map_articulations(note_obj)
    tech = _map_technique(note_obj)
    attack, release = _map_attack_release(arts)

    return NoteObject(
        note_id=note_id,
        bar=bar,
        beat=beat,
        subdivision=subdivision,
        time_onset=time_onset,
        time_duration=time_duration,
        instrument_id=instrument_meta.instrument_id,
        voice=voice,
        pitch_midi=pitch_midi,
        pitch_name=pitch_name,
        dynamic_marking=dyn_marking,
        dynamic_value=dyn_value,
        sforzando=sfz,
        articulation=arts,
        technique=tech,
        attack=attack,
        release=release,
    )


# ---------------------------------------------------------------------------
# Shape Variant pre-pass (Step 9)
# ---------------------------------------------------------------------------
def assign_variant_indices(notes: list[NoteObject], seed: int = 42) -> None:
    """
    Deterministic round-robin variant assignment seeded by note position.
    Variant counts per articulation type (from architecture doc):
      staccato=5, legato=5, marcato=4, tenuto=4, accent=4, tremolo=5, normal=1
    """
    VARIANT_COUNTS = {
        ArticulationEnum.STACCATO: 5,
        ArticulationEnum.LEGATO:   5,
        ArticulationEnum.MARCATO:  4,
        ArticulationEnum.TENUTO:   4,
        ArticulationEnum.ACCENT:   4,
        ArticulationEnum.TREMOLO:  5,
        ArticulationEnum.NORMAL:   1,
        ArticulationEnum.PORTATO:  4,
    }
    counters: dict[ArticulationEnum, int] = {k: 0 for k in VARIANT_COUNTS}

    for note in notes:
        primary_art = note.articulation[0] if note.articulation else ArticulationEnum.NORMAL
        count = VARIANT_COUNTS.get(primary_art, 1)
        note.variant_index = (seed + counters[primary_art]) % count
        counters[primary_art] += 1


# ---------------------------------------------------------------------------
# Validation (Step 11)
# ---------------------------------------------------------------------------
def validate_score_data(score: ScoreData) -> ValidationResult:
    errors = []
    warnings = []
    suggestions = []

    # ERROR checks
    if not score.instruments:
        errors.append(ValidationMessage(
            level=ValidationLevelEnum.ERROR,
            code="NO_INSTRUMENTS",
            message="No instruments found in the score.",
            object_type="ScoreData",
        ))

    if not score.notes:
        errors.append(ValidationMessage(
            level=ValidationLevelEnum.ERROR,
            code="NO_NOTES",
            message="No notes found in the score.",
            object_type="ScoreData",
        ))

    if score.duration_seconds <= 0:
        errors.append(ValidationMessage(
            level=ValidationLevelEnum.ERROR,
            code="ZERO_DURATION",
            message="Score duration is zero or negative.",
            object_type="ScoreData",
        ))

    # WARNING checks
    auto_sections = [s for s in score.sections if s.is_auto_analyzed]
    if auto_sections:
        warnings.append(ValidationMessage(
            level=ValidationLevelEnum.WARNING,
            code="AUTO_ANALYZED_SECTIONS",
            message=f"{len(auto_sections)} section(s) are auto-analyzed and need conductor review.",
            object_type="ScoreSection",
        ))

    auto_spans = [s for s in score.dynamic_spans if s.is_auto_analyzed]
    if auto_spans:
        warnings.append(ValidationMessage(
            level=ValidationLevelEnum.WARNING,
            code="AUTO_ANALYZED_DYNAMIC_SPANS",
            message=f"{len(auto_spans)} dynamic span(s) have inferred boundaries.",
            object_type="DynamicSpan",
        ))

    # SUGGESTION checks
    notes_with_dynamics = [n for n in score.notes if n.dynamic_marking is not None]
    if len(score.notes) > 0 and len(notes_with_dynamics) == 0:
        suggestions.append(ValidationMessage(
            level=ValidationLevelEnum.SUGGESTION,
            code="NO_DYNAMICS",
            message="No dynamic markings found. Consider adding dynamics to the score.",
            object_type="NoteObject",
            suggestion="Check if the MusicXML source contains dynamic markings.",
        ))

    if len(score.tempo_events) == 0:
        suggestions.append(ValidationMessage(
            level=ValidationLevelEnum.SUGGESTION,
            code="NO_TEMPO_EVENTS",
            message="No tempo markings detected; using default 120 BPM.",
            object_type="TempoEvent",
            suggestion="Add explicit tempo markings to the score for accurate time rendering.",
        ))

    return ValidationResult(errors=errors, warnings=warnings, suggestions=suggestions)


# ---------------------------------------------------------------------------
# Main pipeline orchestrator
# ---------------------------------------------------------------------------
def parse_musicxml(file_path: str, file_bytes: Optional[bytes] = None) -> ScoreData:
    """
    Orchestrates the full parse pipeline. Called by ScoreData.from_xml().
    """
    # Step 7: Hash
    if file_bytes:
        musicxml_hash = hashlib.md5(file_bytes).hexdigest()
    else:
        with open(file_path, "rb") as fh:
            musicxml_hash = hashlib.md5(fh.read()).hexdigest()

    # Parse with music21, convert to concert pitch
    score = music21.converter.parse(file_path)
    score = score.toSoundingPitch()

    # Extract score-level metadata
    title = "Untitled"
    composer = "Unknown"
    opus = None
    movement = None
    year = None

    try:
        md = score.metadata
        if md:
            title = md.title or title
            composer = (md.composer or
                        (md.contributors[0].name if md.contributors else composer))
            opus = getattr(md, "opusNumber", None)
            movement = getattr(md, "movementNumber", None)
    except Exception:
        pass

    # Step 4: Build tempo map
    flat = score.flatten()
    boundaries = flat.metronomeMarkBoundaries()
    tempo_events = extract_tempo_events(score, boundaries)

    # Step 1: Extract instruments and notes
    instruments = []
    all_notes: list[NoteObject] = []
    note_counter = 0

    for part_idx, part in enumerate(score.parts):
        inst_meta = instrument_meta_from_part(part, part_idx)
        instruments.append(inst_meta)

        flat_part = part.flatten()

        # Build a sorted list of (offset, Dynamic) for efficient lookup
        dynamics_sorted = sorted(
            flat_part.getElementsByClass(m21_dynamics.Dynamic),
            key=lambda d: float(d.offset)
        )

        # Track slur membership for LEGATO articulation
        slurred_offsets: set[float] = set()
        try:
            from music21 import spanner as m21_spanner
            for sp in part.spannerBundle.getByClass(m21_spanner.Slur):
                for el in sp.getSpannedElements():
                    slurred_offsets.add(float(el.offset))
        except Exception:
            pass

        for measure in part.getElementsByClass(music21.stream.Measure):
            bar_num = measure.number or (part_idx + 1)
            ts = measure.timeSignature
            beat_ql = float(ts.beatDuration.quarterLength) if ts else 1.0

            for voice_obj in measure.voices if measure.voices else [measure]:
                voice_num = int(voice_obj.id) if hasattr(voice_obj, "id") and str(voice_obj.id).isdigit() else 1

                for element in voice_obj.getElementsByClass([m21_note.Note, m21_chord.Chord]):
                    offset_in_measure = float(element.offset)
                    global_offset = float(measure.offset) + offset_in_measure

                    # Bar/beat/subdivision
                    beat_idx = int(offset_in_measure / beat_ql)
                    beat_num = beat_idx + 1
                    frac_in_beat = (offset_in_measure - beat_idx * beat_ql) / beat_ql if beat_ql > 0 else 0.0
                    subdivision = min(int(frac_in_beat * 60), 60)

                    # Time in seconds
                    time_onset = ql_to_seconds(global_offset, boundaries)
                    time_dur = ql_to_seconds(global_offset + float(element.quarterLength), boundaries) - time_onset
                    time_dur = max(time_dur, 0.001)

                    # Active dynamic at this offset
                    active_dyn = None
                    for d in dynamics_sorted:
                        if float(d.offset) <= global_offset:
                            active_dyn = M21_DYNAMIC_MAP.get(d.value)
                        else:
                            break

                    # Handle slur (add LEGATO to articulation)
                    is_slurred = global_offset in slurred_offsets

                    notes_to_process = []
                    if isinstance(element, m21_chord.Chord):
                        for p in element.notes:
                            notes_to_process.append(p)
                    else:
                        notes_to_process.append(element)

                    for n in notes_to_process:
                        note_id = f"note_{note_counter:06d}"
                        note_counter += 1

                        n_obj = note_object_from_m21(
                            n, inst_meta,
                            bar_num, beat_num, subdivision,
                            time_onset, time_dur,
                            note_id, voice_num,
                            active_dyn,
                        )

                        # Overlay slur-based LEGATO
                        if is_slurred and ArticulationEnum.NORMAL in n_obj.articulation:
                            n_obj.articulation = [ArticulationEnum.LEGATO]
                            n_obj.attack = AttackEnum.SOFT
                            n_obj.release = ReleaseEnum.TAPERED

                        all_notes.append(n_obj)

    # Sort notes by time_onset, then instrument score_order
    inst_order = {inst.instrument_id: inst.score_order for inst in instruments}
    all_notes.sort(key=lambda n: (n.time_onset, inst_order.get(n.instrument_id, 999)))

    # Compute total duration
    duration_seconds = 0.0
    if all_notes:
        duration_seconds = max(n.time_onset + n.time_duration for n in all_notes)

    # Step 5: Dynamic spans
    dynamic_spans = extract_dynamic_spans(score, instruments, boundaries)
    stamp_dynamic_spans(all_notes, dynamic_spans)

    # Step 2+3: Harmonic analysis + phrase segmentation
    sections = analyze_harmony(score, boundaries, duration_seconds)

    # Step 6: Auto-annotator sweep
    auto_annotate_notes(all_notes)

    # Step 9: Shape variant pre-pass
    assign_variant_indices(all_notes)

    # Check for unresolved events
    has_unresolved = any(s.is_auto_analyzed for s in sections)

    # Step 11: Validate
    score_data = ScoreData(
        title=title,
        composer=composer,
        opus=str(opus) if opus else None,
        movement=str(movement) if movement else None,
        year=year,
        musicxml_filename=os.path.basename(file_path),
        musicxml_hash=musicxml_hash,
        duration_seconds=duration_seconds,
        has_unresolved_events=has_unresolved,
        instruments=instruments,
        notes=all_notes,
        sections=sections,
        dynamic_spans=dynamic_spans,
        tempo_events=tempo_events,
        collision_clusters=[],
    )

    return score_data

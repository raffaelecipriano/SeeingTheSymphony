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
import time
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
# Precomputed MIDI → pitch-name lookup (sharps; avoids music21 per-note calls)
# MIDI 0 = C-1, MIDI 60 = C4, MIDI 127 = G9
# ---------------------------------------------------------------------------
_PITCH_CLASSES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
_MIDI_TO_NAME: tuple[str, ...] = tuple(
    f"{_PITCH_CLASSES[m % 12]}{m // 12 - 1}" for m in range(128)
)


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
def _get_instrument_fast(part: music21.stream.Part, flat_part):
    """
    Return the music21 Instrument object for a part.
    Uses flat_part.getElementsByClass('Instrument') which is a direct class
    scan (no context walk), falling back to getInstrument() if nothing found.
    flat_part must already be the flattened version of part.
    """
    try:
        inst_list = flat_part.getElementsByClass('Instrument')
        if inst_list:
            return inst_list[0]
    except Exception:
        pass
    return part.getInstrument(returnDefault=True)


def instrument_meta_from_part(part: music21.stream.Part, score_order: int) -> InstrumentMeta:
    """
    Construct InstrumentMeta from a music21 Part.
    Called only from InstrumentMeta.from_xml() classmethod; the main parse
    pipeline inlines this work to share the pre-flattened stream.
    """
    flat = part.flatten()
    m21_inst = _get_instrument_fast(part, flat)
    name = m21_inst.instrumentName or part.partName or f"Part {score_order + 1}"
    abbr = m21_inst.instrumentAbbreviation or name[:4]
    family = _detect_family(name)

    transposition = 0
    if m21_inst.transposition is not None:
        try:
            transposition = int(m21_inst.transposition.semitones)
        except Exception:
            transposition = 0

    pitches = [n.pitch.midi for n in flat.getElementsByClass(m21_note.Note)]
    for c in flat.getElementsByClass(m21_chord.Chord):
        pitches.extend(p.midi for p in c.pitches)

    inst_id = part.id or re.sub(r"[^a-zA-Z0-9_]", "_", name)
    return InstrumentMeta(
        instrument_id=inst_id,
        name=name,
        abbreviation=abbr,
        instrument_family=family,
        score_order=score_order,
        transposition=transposition,
        player_count=1,
        pitch_range_low=min(pitches) if pitches else 21,
        pitch_range_high=max(pitches) if pitches else 108,
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
                          active_dynamic: Optional[DynamicMarkingEnum],
                          concert_midi: int, pitch_name: str) -> NoteObject:
    """
    Construct a NoteObject from a music21 Note.
    concert_midi and pitch_name are pre-computed by the caller using integer
    arithmetic on the cached transposition — no music21 pitch machinery here.
    """
    dyn_value = instrument_meta.get_dynamic_value(active_dynamic) if active_dynamic else 0.55

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
        pitch_midi=concert_midi,
        pitch_name=pitch_name,
        dynamic_marking=active_dynamic,
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

    if score.non_uniform_beat_bars:
        bar_list = ", ".join(str(b) for b in score.non_uniform_beat_bars)
        suggestions.append(ValidationMessage(
            level=ValidationLevelEnum.SUGGESTION,
            code="NON_UNIFORM_BEAT_UNIT",
            message=(
                f"{len(score.non_uniform_beat_bars)} measure(s) have non-uniform beat units "
                f"(bars: {bar_list}). Beat unit defaulted to QUARTER; affected TempoEvents "
                f"are marked is_auto_analyzed=True."
            ),
            object_type="TempoEvent",
            suggestion=(
                "Review the listed bars for mixed or asymmetric meters (e.g. 5/4, 7/8). "
                "Confirm that the defaulted beat unit produces correct time_onset values."
            ),
        ))

    return ValidationResult(errors=errors, warnings=warnings, suggestions=suggestions)


# ---------------------------------------------------------------------------
# Helper: beat quarter-length with non-uniform fallback
# ---------------------------------------------------------------------------
def _safe_beat_ql(ts, bar_num: int, non_uniform_bars: list[int]) -> float:
    """
    Return the quarter-length of one beat for the given TimeSignature.
    When music21 raises TimeSignatureException('non-uniform beat unit'),
    fall back to 4/denominator (e.g. 5/8 → 0.5, 7/4 → 1.0) and record
    the bar number so it can surface as a validation SUGGESTION.
    """
    if ts is None:
        return 1.0
    try:
        return float(ts.beatDuration.quarterLength)
    except Exception:
        if bar_num not in non_uniform_bars:
            non_uniform_bars.append(bar_num)
        # Sensible fallback: denominator-based beat unit (4 / denominator)
        try:
            return 4.0 / float(ts.denominator)
        except Exception:
            return 1.0


# ---------------------------------------------------------------------------
# Main pipeline orchestrator
# ---------------------------------------------------------------------------
def parse_musicxml(
    file_path: str,
    file_bytes: Optional[bytes] = None,
    stage_callback=None,
) -> ScoreData:
    """
    Orchestrates the full parse pipeline. Called by ScoreData.from_xml().
    stage_callback(name: str) is called before each pipeline stage so callers
    can track progress (e.g. write to a shared Manager dict for the status API).
    """
    def _stage(name: str) -> None:
        if stage_callback is not None:
            try:
                stage_callback(name)
            except Exception:
                pass

    _t_pipeline_start = time.perf_counter()

    # Step 7: Hash
    if file_bytes:
        musicxml_hash = hashlib.md5(file_bytes).hexdigest()
    else:
        with open(file_path, "rb") as fh:
            musicxml_hash = hashlib.md5(fh.read()).hexdigest()

    _timing: dict[str, float] = {}

    # Parse with music21.
    # forceSource=True skips any stale on-disk cache for this path.
    # We do NOT call score.toSoundingPitch() — that traverses every note in
    # all parts and mutates pitch objects, taking several seconds on large scores.
    # Instead we cache the transposition interval per-part as a plain int and
    # apply it via integer arithmetic when reading each note's MIDI number.
    _stage("music21_parse")
    _t0 = time.perf_counter()
    score = music21.converter.parse(file_path, forceSource=True)
    _timing["music21_parse_ms"] = round((time.perf_counter() - _t0) * 1000, 1)
    print(f"[pipeline] music21 parse: {_timing['music21_parse_ms']:.1f} ms")

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

    # Step 4: Build tempo map + extract tempo events
    _stage("tempo_extractor")
    _t0 = time.perf_counter()
    flat = score.flatten()
    boundaries = flat.metronomeMarkBoundaries()
    tempo_events = extract_tempo_events(score, boundaries)
    _timing["tempo_extractor_ms"] = round((time.perf_counter() - _t0) * 1000, 1)
    print(f"[pipeline] Tempo Extractor: {_timing['tempo_extractor_ms']:.1f} ms")

    # Step 1: Extract instruments and notes
    _stage("score_parser")
    # Key optimisations vs the naive approach:
    #   • Each part is flattened exactly once; the result is shared for instrument
    #     metadata, dynamics, and note iteration (was: 2–3 flatten() calls per part).
    #   • Transposition is cached as a plain int per part; concert MIDI is computed
    #     as raw_midi + semitones — no toSoundingPitch() traversal of the whole score.
    #   • Pitch name comes from _MIDI_TO_NAME (module-level tuple lookup, O(1)).
    #   • Dynamics pre-converted to (float_offset, DynamicMarkingEnum) tuples.
    #   • Time-signature beat_ql is cached across consecutive identical measures.
    _t0 = time.perf_counter()
    instruments = []
    all_notes: list[NoteObject] = []
    note_counter = 0
    non_uniform_beat_bars: list[int] = []

    from music21 import spanner as m21_spanner  # import once outside the loop

    for part_idx, part in enumerate(score.parts):
        # ── Flatten once ──────────────────────────────────────────────────────
        flat_part = part.flatten()

        # ── Instrument & transposition ────────────────────────────────────────
        m21_inst = _get_instrument_fast(part, flat_part)
        name = m21_inst.instrumentName or part.partName or f"Part {part_idx + 1}"
        abbr = m21_inst.instrumentAbbreviation or name[:4]
        family = _detect_family(name)
        inst_id = part.id or re.sub(r"[^a-zA-Z0-9_]", "_", name)

        # Semitone offset for written→concert conversion (integer, cached per part)
        transposition_semitones: int = 0
        if m21_inst.transposition is not None:
            try:
                transposition_semitones = int(m21_inst.transposition.semitones)
            except Exception:
                transposition_semitones = 0

        # Build InstrumentMeta with placeholder pitch range;
        # we fill it from the actual notes below.
        inst_meta = InstrumentMeta(
            instrument_id=inst_id,
            name=name,
            abbreviation=abbr,
            instrument_family=family,
            score_order=part_idx,
            transposition=transposition_semitones,
            player_count=1,
            pitch_range_low=127,
            pitch_range_high=0,
        )
        instruments.append(inst_meta)

        # ── Dynamics: pre-convert to (offset_float, enum) tuples ─────────────
        dynamics_list: list[tuple[float, DynamicMarkingEnum]] = []
        for d in flat_part.getElementsByClass(m21_dynamics.Dynamic):
            mapped = M21_DYNAMIC_MAP.get(d.value)
            if mapped is not None:
                dynamics_list.append((float(d.offset), mapped))
        dynamics_list.sort(key=lambda x: x[0])

        # ── Slurs ─────────────────────────────────────────────────────────────
        slurred_offsets: set[float] = set()
        try:
            for sp in part.spannerBundle.getByClass(m21_spanner.Slur):
                for el in sp.getSpannedElements():
                    slurred_offsets.add(float(el.offset))
        except Exception:
            pass

        # ── Pitch-range accumulators (updated per note, set on InstrumentMeta) ─
        part_pitch_low = 127
        part_pitch_high = 0

        # ── Beat-ql cache: skip recomputing when TS hasn't changed ────────────
        _cached_ts_id: int | None = None
        _cached_beat_ql: float = 1.0

        # ── Note extraction ───────────────────────────────────────────────────
        for measure in part.getElementsByClass(music21.stream.Measure):
            bar_num = measure.number or (part_idx + 1)
            ts = measure.timeSignature
            ts_id = id(ts) if ts is not None else -1
            if ts_id != _cached_ts_id:
                _cached_beat_ql = _safe_beat_ql(ts, bar_num, non_uniform_beat_bars)
                _cached_ts_id = ts_id
            beat_ql = _cached_beat_ql
            measure_offset = float(measure.offset)

            for voice_obj in (measure.voices if measure.voices else [measure]):
                voice_num = (
                    int(voice_obj.id)
                    if hasattr(voice_obj, "id") and str(voice_obj.id).isdigit()
                    else 1
                )

                for element in voice_obj.getElementsByClass([m21_note.Note, m21_chord.Chord]):
                    offset_in_measure = float(element.offset)
                    global_offset = measure_offset + offset_in_measure

                    # Bar / beat / subdivision
                    beat_idx = int(offset_in_measure / beat_ql)
                    beat_num = beat_idx + 1
                    frac = (offset_in_measure - beat_idx * beat_ql) / beat_ql if beat_ql > 0 else 0.0
                    subdivision = min(int(frac * 60), 60)

                    # Time in seconds (two boundary walks per element)
                    el_ql = float(element.quarterLength)
                    time_onset = ql_to_seconds(global_offset, boundaries)
                    time_dur = max(
                        ql_to_seconds(global_offset + el_ql, boundaries) - time_onset,
                        0.001,
                    )

                    # Active dynamic: last entry whose offset ≤ global_offset
                    active_dyn: Optional[DynamicMarkingEnum] = None
                    for dyn_off, dyn_val in dynamics_list:
                        if dyn_off <= global_offset:
                            active_dyn = dyn_val
                        else:
                            break

                    is_slurred = global_offset in slurred_offsets
                    notes_to_process = (
                        list(element.notes) if isinstance(element, m21_chord.Chord)
                        else [element]
                    )

                    for n in notes_to_process:
                        note_id = f"note_{note_counter:06d}"
                        note_counter += 1

                        # Concert pitch via integer arithmetic — no music21 pitch
                        # transposition machinery, no toSoundingPitch() needed.
                        raw_midi: int = n.pitch.midi
                        concert_midi: int = max(0, min(127, raw_midi + transposition_semitones))
                        pitch_name: str = _MIDI_TO_NAME[concert_midi]

                        # Update per-part pitch range
                        if concert_midi < part_pitch_low:
                            part_pitch_low = concert_midi
                        if concert_midi > part_pitch_high:
                            part_pitch_high = concert_midi

                        n_obj = note_object_from_m21(
                            n, inst_meta,
                            bar_num, beat_num, subdivision,
                            time_onset, time_dur,
                            note_id, voice_num,
                            active_dyn,
                            concert_midi, pitch_name,
                        )

                        if is_slurred and ArticulationEnum.NORMAL in n_obj.articulation:
                            n_obj.articulation = [ArticulationEnum.LEGATO]
                            n_obj.attack = AttackEnum.SOFT
                            n_obj.release = ReleaseEnum.TAPERED

                        all_notes.append(n_obj)

        # Commit pitch range (guard against parts with zero notes)
        if part_pitch_low <= part_pitch_high:
            inst_meta.pitch_range_low = part_pitch_low
            inst_meta.pitch_range_high = part_pitch_high

    # Sort notes by time_onset, then instrument score_order
    inst_order = {inst.instrument_id: inst.score_order for inst in instruments}
    all_notes.sort(key=lambda n: (n.time_onset, inst_order.get(n.instrument_id, 999)))

    # Compute total duration
    duration_seconds = 0.0
    if all_notes:
        duration_seconds = max(n.time_onset + n.time_duration for n in all_notes)
    _timing["score_parser_ms"] = round((time.perf_counter() - _t0) * 1000, 1)
    print(f"[pipeline] Score Parser (instruments + notes, {len(all_notes)} notes): {_timing['score_parser_ms']:.1f} ms")

    # Step 5: Dynamic spans
    _stage("dynamic_extractor")
    _t0 = time.perf_counter()
    dynamic_spans = extract_dynamic_spans(score, instruments, boundaries)
    stamp_dynamic_spans(all_notes, dynamic_spans)
    _timing["dynamic_extractor_ms"] = round((time.perf_counter() - _t0) * 1000, 1)
    print(f"[pipeline] Dynamic Extractor ({len(dynamic_spans)} spans): {_timing['dynamic_extractor_ms']:.1f} ms")

    # Step 2+3: Harmonic analysis + phrase segmentation
    _stage("harmonic_analyzer")
    _t0 = time.perf_counter()
    sections = analyze_harmony(score, boundaries, duration_seconds)
    _timing["harmonic_analyzer_ms"] = round((time.perf_counter() - _t0) * 1000, 1)
    print(f"[pipeline] Harmonic Analyzer ({len(sections)} sections): {_timing['harmonic_analyzer_ms']:.1f} ms")

    # Step 6: Auto-annotator sweep
    _stage("auto_annotator")
    _t0 = time.perf_counter()
    auto_annotate_notes(all_notes)
    _timing["auto_annotator_ms"] = round((time.perf_counter() - _t0) * 1000, 1)
    print(f"[pipeline] Auto-Annotator: {_timing['auto_annotator_ms']:.1f} ms")

    # Step 9: Shape variant pre-pass
    _stage("shape_variant")
    _t0 = time.perf_counter()
    assign_variant_indices(all_notes)
    _timing["shape_variant_ms"] = round((time.perf_counter() - _t0) * 1000, 1)
    print(f"[pipeline] Shape Variant Pre-pass: {_timing['shape_variant_ms']:.1f} ms")

    # Check for unresolved events
    has_unresolved = any(s.is_auto_analyzed for s in sections)

    # Step 11: Validate
    _timing["total_ms"] = round((time.perf_counter() - _t_pipeline_start) * 1000, 1)
    print(f"[pipeline] TOTAL parse_musicxml: {_timing['total_ms']:.1f} ms")

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
        timing_ms=_timing,
        non_uniform_beat_bars=sorted(set(non_uniform_beat_bars)),
    )

    return score_data

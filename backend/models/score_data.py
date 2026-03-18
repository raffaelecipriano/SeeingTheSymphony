"""
Seeing the Symphony — Data Model
All Pydantic models follow the Data Model Reference Document v0.1 exactly.
from_xml() classmethods delegate to the pipeline modules.
"""
from __future__ import annotations

from typing import Optional, Dict, List
from pydantic import BaseModel, Field, model_validator

from .enums import (
    DynamicMarkingEnum, SforzandoEnum, ArticulationEnum, TechniqueEnum,
    AttackEnum, ReleaseEnum, InstrumentFamilyEnum, KeyModeEnum,
    ShapeEnum, SpanTypeEnum, StrategyEnum, BeatUnitEnum, TransitionTypeEnum,
    ValidationLevelEnum,
)

# ---------------------------------------------------------------------------
# Default dynamic intensity table (concert dynamics → normalised float 0–1)
# ---------------------------------------------------------------------------
DEFAULT_DYNAMIC_TABLE: Dict[DynamicMarkingEnum, float] = {
    DynamicMarkingEnum.PPP: 0.05,
    DynamicMarkingEnum.PP:  0.15,
    DynamicMarkingEnum.P:   0.25,
    DynamicMarkingEnum.MP:  0.40,
    DynamicMarkingEnum.MF:  0.55,
    DynamicMarkingEnum.F:   0.70,
    DynamicMarkingEnum.FF:  0.85,
    DynamicMarkingEnum.FFF: 1.00,
    DynamicMarkingEnum.FP:  0.70,
}


# ---------------------------------------------------------------------------
# HarmonicEvent — embedded in ScoreSection
# ---------------------------------------------------------------------------
class HarmonicEvent(BaseModel):
    """
    Describes the harmonic state at a moment within a ScoreSection.
    One per stable key area; multiple per modulating section.
    circle_of_fifths_pos: C=0, G=1, D=2, A=3, E=4, B=5, F#=6,
                          Db=7, Ab=8, Eb=9, Bb=10, F=11
    """
    time_onset: float = Field(description="Start time in seconds")
    time_duration: float = Field(description="Duration in seconds")
    key_tonic: str = Field(description="Tonic pitch class, e.g. 'G', 'Bb', 'F#'")
    key_mode: KeyModeEnum = Field(description="MAJOR or MINOR")
    circle_of_fifths_pos: int = Field(ge=0, le=11, description="Position on circle of fifths (C=0..F=11)")
    is_transitional: bool = Field(default=False, description="True during modulation; drives gradient rendering")
    is_auto_analyzed: bool = Field(default=True, description="False once conductor confirms")

    @classmethod
    def from_xml(cls, key_obj, time_onset: float, time_duration: float) -> "HarmonicEvent":
        """Construct from a music21 key.Key object."""
        from ..pipeline.harmonic_analyzer import harmonic_event_from_key
        return harmonic_event_from_key(key_obj, time_onset, time_duration)


# ---------------------------------------------------------------------------
# ScoreSection
# ---------------------------------------------------------------------------
class ScoreSection(BaseModel):
    """
    Background layer — large-scale formal division of the score.
    Contains one or more HarmonicEvents describing its harmonic journey.
    """
    section_id: str = Field(description="Unique identifier")
    label: str = Field(
        description="Free text. UI presets: Exposition|Development|Recapitulation|"
                    "Intro|Coda|Codetta|Bridge|Theme A|Theme B|Other"
    )
    is_auto_analyzed: bool = Field(default=True, description="False once conductor confirms")
    start_bar: int
    start_beat: int
    end_bar: int
    end_beat: int
    time_onset: float = Field(description="Start time in seconds")
    time_duration: float = Field(description="Duration in seconds")
    harmonic_events: List[HarmonicEvent] = Field(
        default_factory=list,
        description="Ordered by time_onset. One item = stable key; multiple = modulating section."
    )


# ---------------------------------------------------------------------------
# InstrumentMeta
# ---------------------------------------------------------------------------
class InstrumentMeta(BaseModel):
    """
    Describes one instrument or part. Used by the renderer for lane sizing,
    color assignment, and dynamic normalization. Does not store color —
    the UI derives color from instrument_family and score_order.
    """
    instrument_id: str = Field(description="Unique identifier; PK. Matches NoteObject.instrument_id")
    name: str = Field(description="Full name, e.g. 'Violin I', 'Oboe', 'Horn in F'")
    abbreviation: str = Field(description="Short name, e.g. 'Vl.I', 'Ob.', 'Hn.'")
    instrument_family: InstrumentFamilyEnum = Field(
        description="STRINGS|WOODWINDS|BRASS|PERCUSSION|KEYBOARD|VOICE"
    )
    score_order: int = Field(description="0 = top of score, ascending downward")
    transposition: int = Field(default=0, description="Semitones from written to concert pitch. 0=C instrument, -2=Bb clarinet, -5=Horn in F")
    player_count: int = Field(default=1, description="Number of players on this part (for lane height)")
    pitch_range_low: int = Field(default=21, description="Lowest expected MIDI pitch")
    pitch_range_high: int = Field(default=108, description="Highest expected MIDI pitch")
    dynamic_table: Dict[DynamicMarkingEnum, float] = Field(
        default_factory=lambda: dict(DEFAULT_DYNAMIC_TABLE),
        description="Maps dynamic marking to normalised intensity 0.0–1.0. Conductor-adjustable."
    )

    def get_dynamic_value(self, marking: DynamicMarkingEnum) -> float:
        return self.dynamic_table.get(marking, 0.55)

    @classmethod
    def from_xml(cls, part, score_order: int) -> "InstrumentMeta":
        """Construct from a music21 Part object."""
        from ..pipeline.score_parser import instrument_meta_from_part
        return instrument_meta_from_part(part, score_order)


# ---------------------------------------------------------------------------
# NoteObject
# ---------------------------------------------------------------------------
class NoteObject(BaseModel):
    """
    Fundamental unit of the foreground layer. One NoteObject per pitch per note
    event (chords are split). Always stores concert pitch.
    """
    note_id: str = Field(description="Unique identifier used by CollisionCluster.note_ids")
    bar: int
    beat: int
    subdivision: int = Field(ge=0, le=60, description="0=beat start, 60=next beat; triplet≈20, sixteenth=15")
    time_onset: float = Field(description="Primary rendering coordinate in seconds")
    time_duration: float = Field(description="Duration in seconds")
    instrument_id: str = Field(description="FK → InstrumentMeta")
    voice: int = Field(default=1)
    pitch_midi: int = Field(ge=0, le=127, description="Concert pitch MIDI number")
    pitch_name: str = Field(description="Concert pitch name, e.g. 'G4', 'Bb3'")
    dynamic_marking: Optional[DynamicMarkingEnum] = Field(default=None, description="PPP|PP|P|MP|MF|F|FF|FFF|FP")
    dynamic_value: float = Field(default=0.55, ge=0.0, le=1.0, description="Computed via InstrumentMeta.dynamic_table")
    sforzando: Optional[SforzandoEnum] = Field(default=None, description="SF|FZ|SFZ|FZFZ|SFFZ")
    dynamic_span_id: Optional[str] = Field(default=None, description="FK → DynamicSpan")
    dynamic_span_position: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Pre-computed position within hairpin 0.0–1.0")
    articulation: List[ArticulationEnum] = Field(
        default_factory=lambda: [ArticulationEnum.NORMAL],
        description="NORMAL|LEGATO|STACCATO|MARCATO|TENUTO|ACCENT|TREMOLO|PORTATO"
    )
    technique: List[TechniqueEnum] = Field(
        default_factory=lambda: [TechniqueEnum.NORMAL],
        description="NORMAL|PIZZICATO|COL_LEGNO|SUL_PONTICELLO|HARMONICS|SUL_TASTO|TREMOLO_BOW|SNAP_PIZZICATO|FLAUTANDO"
    )
    attack: AttackEnum = Field(default=AttackEnum.NORMAL, description="NORMAL|SHARP|SOFT")
    release: ReleaseEnum = Field(default=ReleaseEnum.NORMAL, description="NORMAL|CLIPPED|TAPERED")
    variant_index: Optional[int] = Field(default=None, description="Assigned by Shape Variant pre-pass")
    collision_cluster_id: Optional[str] = Field(default=None, description="FK → CollisionCluster; assigned by Collision Resolver")

    @classmethod
    def from_xml(cls, note, part, instrument_meta: "InstrumentMeta",
                 bar: int, beat: int, subdivision: int,
                 time_onset: float, time_duration: float,
                 note_id: str, voice: int = 1) -> "NoteObject":
        """Construct from a music21 Note object. Transposition already applied upstream."""
        from ..pipeline.score_parser import note_object_from_m21
        return note_object_from_m21(note, part, instrument_meta, bar, beat, subdivision,
                                    time_onset, time_duration, note_id, voice)


# ---------------------------------------------------------------------------
# DynamicSpan
# ---------------------------------------------------------------------------
class DynamicSpan(BaseModel):
    """
    Crescendo or diminuendo hairpin attached to a specific instrument.
    NoteObject.dynamic_span_position is pre-computed at parse time so the
    renderer never needs to consult this object directly.
    """
    span_id: str = Field(description="Unique identifier PK")
    span_type: SpanTypeEnum = Field(description="CRESCENDO|DIMINUENDO")
    instrument_id: str = Field(description="FK → InstrumentMeta; one span per instrument per hairpin")
    start_bar: int
    start_beat: int
    end_bar: int
    end_beat: int
    time_onset: float = Field(description="Start time in seconds")
    time_duration: float = Field(description="Duration in seconds")
    dynamic_start: DynamicMarkingEnum = Field(description="Never null; inferred if not explicit")
    dynamic_end: DynamicMarkingEnum = Field(description="Never null; defaults to ±1 level if not explicit")
    shape: ShapeEnum = Field(default=ShapeEnum.LINEAR, description="LINEAR|CONVEX|CONCAVE")
    is_auto_analyzed: bool = Field(default=False, description="True if dynamic_start or dynamic_end were inferred")

    def get_value_at(self, position: float) -> float:
        """Interpolate dynamic value at position 0.0–1.0 using shape curve."""
        from ..models.enums import ShapeEnum
        start_val = DEFAULT_DYNAMIC_TABLE.get(self.dynamic_start, 0.4)
        end_val = DEFAULT_DYNAMIC_TABLE.get(self.dynamic_end, 0.7)
        if self.shape == ShapeEnum.LINEAR:
            return start_val + (end_val - start_val) * position
        elif self.shape == ShapeEnum.CONVEX:
            return start_val + (end_val - start_val) * (position ** 0.5)
        else:  # CONCAVE
            return start_val + (end_val - start_val) * (position ** 2)

    @classmethod
    def from_xml(cls, hairpin, part, instrument_id: str, span_id: str,
                 boundaries) -> "DynamicSpan":
        """Construct from a music21 DynamicWedge spanner."""
        from ..pipeline.dynamic_extractor import dynamic_span_from_hairpin
        return dynamic_span_from_hairpin(hairpin, part, instrument_id, span_id, boundaries)


# ---------------------------------------------------------------------------
# TempoEvent
# ---------------------------------------------------------------------------
class TempoEvent(BaseModel):
    """
    Encodes a tempo marking or change. Used to convert bar/beat to seconds
    for all other objects.
    """
    tempo_event_id: str = Field(description="Unique identifier PK")
    bar: int
    beat: int
    time_onset: float = Field(description="Time in seconds")
    bpm: float = Field(description="Beats per minute")
    beat_unit: BeatUnitEnum = Field(default=BeatUnitEnum.QUARTER, description="QUARTER|HALF|EIGHTH|DOTTED_QUARTER|DOTTED_HALF")
    transition_type: TransitionTypeEnum = Field(default=TransitionTypeEnum.IMMEDIATE, description="IMMEDIATE|GRADUAL")
    transition_end_bar: Optional[int] = None
    transition_end_beat: Optional[int] = None
    transition_end_bpm: Optional[float] = Field(default=None, description="Target BPM for gradual tempo change")
    transition_curve: Optional[ShapeEnum] = None
    notation_label: Optional[str] = Field(default=None, description="Text as written: 'Allegro vivace', 'Tempo I'")
    is_auto_analyzed: bool = Field(default=False, description="True if BPM inferred from text label")
    is_gradual_unresolved: bool = Field(default=False, description="True if GRADUAL and transition_end_bpm is null")

    def seconds_per_beat(self) -> float:
        return 60.0 / self.bpm

    @classmethod
    def from_xml(cls, tempo_mark, idx: int, time_onset: float, bar: int, beat: int) -> "TempoEvent":
        """Construct from a music21 MetronomeMark."""
        from ..pipeline.tempo_extractor import tempo_event_from_m21
        return tempo_event_from_m21(tempo_mark, idx, time_onset, bar, beat)


# ---------------------------------------------------------------------------
# CollisionCluster — empty at parse time; populated by Collision Resolver
# ---------------------------------------------------------------------------
class CollisionCluster(BaseModel):
    """
    Group of notes from different instruments occupying the same visual space.
    Populated by the Collision Resolver pre-pass (Phase 2+); empty list at parse time.
    """
    cluster_id: str
    note_ids: List[str] = Field(description="FK list → NoteObject")
    instrument_ids: List[str] = Field(description="For quick family-pair lookups")
    time_onset: float
    time_duration: float
    pitch_center: int = Field(description="Average MIDI pitch of all notes")
    pitch_spread: int = Field(description="Semitones spread. 0=pure unison, 1-2=tight, 3+=open voicing")
    is_unison: bool = Field(description="True if all notes share identical pitch_midi")
    family_pairs: List[str] = Field(description="All combinations present, normalised alphabetically e.g. 'STRINGS-BRASS'")
    strategy: StrategyEnum = Field(default=StrategyEnum.BLEND, description="EXPAND|ALTERNATE|BLEND")
    contour_pct: float = Field(default=0.35, ge=0.0, le=1.0, description="Blend contour percentage")
    shape_override: bool = Field(default=False, description="True if conductor manually adjusted this cluster")
    family_pair_rules: Optional[Dict[str, float]] = Field(default=None, description="Per-pair contour overrides")
    is_auto_analyzed: bool = True


# ---------------------------------------------------------------------------
# ValidationMessage + ValidationResult
# ---------------------------------------------------------------------------
class ValidationMessage(BaseModel):
    level: ValidationLevelEnum
    code: str = Field(description="Machine-readable code e.g. 'MISSING_INSTRUMENT_ID'")
    message: str = Field(description="Human-readable description")
    object_type: str = Field(description="Which object type triggered it")
    object_ref: Optional[str] = Field(default=None, description="ID of the specific object")
    bar: Optional[int] = Field(default=None, description="Metric location if applicable")
    suggestion: Optional[str] = Field(default=None, description="What the conductor might consider (SUGGESTION level)")


class ValidationResult(BaseModel):
    errors: List[ValidationMessage] = Field(default_factory=list, description="Must fix before proceeding")
    warnings: List[ValidationMessage] = Field(default_factory=list, description="Flag for review; proceed with caution")
    suggestions: List[ValidationMessage] = Field(default_factory=list, description="Draw attention; no action required")
    is_valid: bool = Field(default=True, description="True if errors is empty")
    is_clean: bool = Field(default=True, description="True if warnings is empty")
    is_pristine: bool = Field(default=True, description="True if suggestions is empty")

    @model_validator(mode="after")
    def compute_flags(self) -> "ValidationResult":
        self.is_valid = len(self.errors) == 0
        self.is_clean = len(self.warnings) == 0
        self.is_pristine = len(self.suggestions) == 0
        return self


# ---------------------------------------------------------------------------
# ScoreData — top-level container
# ---------------------------------------------------------------------------
class ScoreData(BaseModel):
    """
    Top-level container. Singleton — one instance per loaded score.
    Holds all musical data. Serialized to JSON by FastAPI for the React frontend.
    Contains only musical content — no rendering parameters.
    """
    title: str = Field(description="Score title")
    composer: str = Field(description="Composer name")
    opus: Optional[str] = Field(default=None, description="Opus number or catalogue reference")
    movement: Optional[str] = Field(default=None, description="Movement designation")
    year: Optional[int] = Field(default=None, description="Year of composition")
    engraver_edition: Optional[str] = Field(default=None, description="Edition, source, or preparator")
    musicxml_filename: str = Field(description="Original filename of the MusicXML source")
    musicxml_hash: str = Field(description="MD5 hash of source file for CIL sync detection")
    cil_filename: Optional[str] = Field(default=None, description="Associated CIL file if loaded")
    duration_seconds: float = Field(description="Total score duration in seconds")
    has_unresolved_events: bool = Field(default=False, description="True if any object has is_auto_analyzed=True")
    instruments: List[InstrumentMeta] = Field(default_factory=list, description="Ordered by score_order")
    notes: List[NoteObject] = Field(default_factory=list, description="Flat list, sorted by time_onset")
    sections: List[ScoreSection] = Field(default_factory=list, description="Sorted by time_onset")
    dynamic_spans: List[DynamicSpan] = Field(default_factory=list, description="Sorted by time_onset")
    tempo_events: List[TempoEvent] = Field(default_factory=list, description="Sorted by time_onset")
    collision_clusters: List[CollisionCluster] = Field(default_factory=list, description="Empty at parse time; populated by Collision Resolver")

    def get_instrument(self, instrument_id: str) -> Optional[InstrumentMeta]:
        for inst in self.instruments:
            if inst.instrument_id == instrument_id:
                return inst
        return None

    def get_notes_for(self, instrument_id: str, bar_start: int, bar_end: int) -> List[NoteObject]:
        return [n for n in self.notes if n.instrument_id == instrument_id and bar_start <= n.bar <= bar_end]

    def get_notes_at(self, time: float) -> List[NoteObject]:
        return [n for n in self.notes if n.time_onset <= time < n.time_onset + n.time_duration]

    def get_section_at(self, time: float) -> Optional[ScoreSection]:
        for s in self.sections:
            if s.time_onset <= time < s.time_onset + s.time_duration:
                return s
        return None

    def get_tempo_at(self, time: float) -> float:
        bpm = 120.0
        for e in self.tempo_events:
            if e.time_onset <= time:
                bpm = e.bpm
            else:
                break
        return bpm

    def get_unresolved(self) -> list:
        unresolved = []
        for n in self.notes:
            if n.attack != AttackEnum.NORMAL or n.technique != [TechniqueEnum.NORMAL]:
                pass  # auto-annotation always runs; flag via has_unresolved_events
        for s in self.sections:
            if s.is_auto_analyzed:
                unresolved.append(s)
        for sp in self.dynamic_spans:
            if sp.is_auto_analyzed:
                unresolved.append(sp)
        return unresolved

    def validate(self) -> ValidationResult:
        from ..pipeline.score_parser import validate_score_data
        return validate_score_data(self)

    @classmethod
    def from_xml(cls, path: str, file_bytes: Optional[bytes] = None) -> "ScoreData":
        """
        Classmethod. Orchestrates the full parse pipeline:
        Score Parser → Harmonic Analyzer → Phrase Segmenter →
        Tempo Extractor → Dynamic Extractor → Auto-Annotator →
        Hash Check → Shape Variant Pre-pass → Validate
        """
        from ..pipeline.score_parser import parse_musicxml
        return parse_musicxml(path, file_bytes)

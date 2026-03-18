"""
Harmonic Analyzer — Step 2 of the parsing pipeline.
Detects key areas and creates ScoreSection + HarmonicEvent objects.
All results are marked is_auto_analyzed=True.
"""
from __future__ import annotations

import music21
from music21 import key as m21_key

from ..models.score_data import ScoreSection, HarmonicEvent
from ..models.enums import KeyModeEnum
from .tempo_extractor import ql_to_seconds

# Circle of fifths position: C=0, G=1, D=2, A=3, E=4, B=5,
#                            F#=6, Db=7, Ab=8, Eb=9, Bb=10, F=11
CIRCLE_OF_FIFTHS = {
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5,
    "F#": 6, "C#": 6,   # enharmonic
    "D-": 7, "Db": 7, "C-": 7,
    "A-": 8, "Ab": 8, "G#": 8,
    "E-": 9, "Eb": 9, "D#": 9,
    "B-": 10, "Bb": 10, "A#": 10,
    "F": 11,
}


def _tonic_name(key_obj) -> str:
    """Normalise music21 tonic to a clean name like 'G', 'Bb', 'F#'."""
    name = key_obj.tonic.name  # e.g. 'G', 'B-', 'F#'
    # music21 uses '-' for flat; convert to 'b' for display
    return name.replace("-", "b")


def _circle_pos(tonic_name: str) -> int:
    # Try as-is, then with flat/sharp variants
    pos = CIRCLE_OF_FIFTHS.get(tonic_name)
    if pos is not None:
        return pos
    # Normalise music21 dash-flat notation
    normalised = tonic_name.replace("b", "-")
    return CIRCLE_OF_FIFTHS.get(normalised, 0)


def harmonic_event_from_key(key_obj, time_onset: float, time_duration: float) -> HarmonicEvent:
    tonic = _tonic_name(key_obj)
    mode = KeyModeEnum.MAJOR if key_obj.mode == "major" else KeyModeEnum.MINOR
    return HarmonicEvent(
        time_onset=time_onset,
        time_duration=time_duration,
        key_tonic=tonic,
        key_mode=mode,
        circle_of_fifths_pos=_circle_pos(tonic),
        is_transitional=False,
        is_auto_analyzed=True,
    )


def analyze_harmony(score: music21.stream.Score, boundaries: list,
                    duration_seconds: float) -> list[ScoreSection]:
    """
    Perform global key analysis and produce a single ScoreSection covering the
    full score. Phrase segmentation (Step 3) will later subdivide this.
    All results are marked is_auto_analyzed=True.
    """
    sections: list[ScoreSection] = []

    try:
        key_obj = score.analyze("key")
    except Exception:
        # Fallback to C major if analysis fails
        key_obj = m21_key.Key("C")

    tonic = _tonic_name(key_obj)
    mode = KeyModeEnum.MAJOR if key_obj.mode == "major" else KeyModeEnum.MINOR
    cof_pos = _circle_pos(tonic)

    harmonic_event = HarmonicEvent(
        time_onset=0.0,
        time_duration=duration_seconds,
        key_tonic=tonic,
        key_mode=mode,
        circle_of_fifths_pos=cof_pos,
        is_transitional=False,
        is_auto_analyzed=True,
    )

    # Determine score measure range
    start_bar, end_bar = 1, 1
    start_beat, end_beat = 1, 1
    try:
        if score.parts:
            measures = list(score.parts[0].getElementsByClass(music21.stream.Measure))
            if measures:
                start_bar = measures[0].number or 1
                end_bar = measures[-1].number or len(measures)
                last_m = measures[-1]
                ts = last_m.timeSignature
                end_beat = ts.numerator if ts else 4
    except Exception:
        pass

    section = ScoreSection(
        section_id="section_0",
        label="Full Score",
        is_auto_analyzed=True,
        start_bar=start_bar,
        start_beat=1,
        end_bar=end_bar,
        end_beat=end_beat,
        time_onset=0.0,
        time_duration=duration_seconds,
        harmonic_events=[harmonic_event],
    )
    sections.append(section)
    return sections

"""
Tempo Extractor — Step 4 of the parsing pipeline.
Extracts TempoEvent objects from a music21 score.
Builds the quarter-length → seconds conversion map used by all other extractors.
"""
from __future__ import annotations

import music21
from music21 import tempo as m21_tempo

from ..models.score_data import TempoEvent
from ..models.enums import BeatUnitEnum, TransitionTypeEnum, ShapeEnum

# Common Italian tempo terms → approximate BPM
TEMPO_LABEL_MAP = {
    "larghissimo": 24, "grave": 35, "largo": 50, "lento": 55,
    "larghetto": 60, "adagio": 70, "adagietto": 74, "andante": 90,
    "andantino": 94, "moderato": 112, "allegretto": 116,
    "allegro": 132, "vivace": 140, "vivacissimo": 152,
    "presto": 168, "prestissimo": 200,
}


def _bpm_from_label(label: str) -> float:
    if not label:
        return 120.0
    lower = label.lower().strip()
    for key, bpm in TEMPO_LABEL_MAP.items():
        if key in lower:
            return float(bpm)
    return 120.0


def _beat_unit_from_referent(referent) -> BeatUnitEnum:
    if referent is None:
        return BeatUnitEnum.QUARTER
    ql = float(referent.quarterLength)
    if ql == 1.0:
        return BeatUnitEnum.QUARTER
    elif ql == 2.0:
        return BeatUnitEnum.HALF
    elif ql == 0.5:
        return BeatUnitEnum.EIGHTH
    elif abs(ql - 1.5) < 0.01:
        return BeatUnitEnum.DOTTED_QUARTER
    elif abs(ql - 3.0) < 0.01:
        return BeatUnitEnum.DOTTED_HALF
    return BeatUnitEnum.QUARTER


def ql_to_seconds(ql_offset: float, boundaries: list) -> float:
    """
    Convert a quarter-length offset to wall-clock seconds using
    metronomeMarkBoundaries() output: [(start_ql, end_ql, MetronomeMark), ...].
    """
    t = 0.0
    for (start, end, mm) in boundaries:
        start_f = float(start)
        end_f = float(end) if end != float("inf") else ql_offset
        if ql_offset <= start_f:
            break
        try:
            bpm = float(mm.getQuarterBPM()) if mm.number is not None else 120.0
        except Exception:
            bpm = 120.0
        if bpm <= 0:
            bpm = 120.0
        spq = 60.0 / bpm
        seg = min(ql_offset, end_f) - start_f
        t += seg * spq
        if ql_offset <= end_f:
            break
    return t


def _offset_to_bar_beat(score: music21.stream.Score, offset: float):
    """Return (bar_number, beat_number) for a given quarter-length offset."""
    try:
        if score.parts:
            part = score.parts[0]
            for m in part.getElementsByClass(music21.stream.Measure):
                m_start = float(m.offset)
                m_end = m_start + float(m.barDuration.quarterLength)
                if m_start <= offset < m_end:
                    ts = m.timeSignature
                    if ts is None:
                        ts = part.flatten().getTimeSignatureForBeat(offset)
                    beat_ql = 1.0 if ts is None else float(ts.beatDuration.quarterLength)
                    beat = int((offset - m_start) / beat_ql) + 1
                    return m.number or 1, beat
    except Exception:
        pass
    return 1, 1


def extract_tempo_events(score: music21.stream.Score, boundaries: list) -> list[TempoEvent]:
    """
    Extract all TempoEvent objects from the score.
    Returns a list sorted by time_onset.
    """
    flat = score.flatten()
    marks = list(flat.getElementsByClass(m21_tempo.MetronomeMark))

    if not marks:
        # No explicit tempo — default to 120 BPM
        return [TempoEvent(
            tempo_event_id="tempo_0",
            bar=1, beat=1,
            time_onset=0.0,
            bpm=120.0,
            beat_unit=BeatUnitEnum.QUARTER,
            transition_type=TransitionTypeEnum.IMMEDIATE,
            notation_label="Allegro (default)",
            is_auto_analyzed=True,
        )]

    events = []
    for idx, mm in enumerate(marks):
        offset = float(mm.offset)
        time_onset = ql_to_seconds(offset, boundaries)
        bar, beat = _offset_to_bar_beat(score, offset)

        # Resolve BPM
        if mm.number is not None and mm.number > 0:
            bpm = float(mm.getQuarterBPM())
            is_auto = False
        else:
            bpm = _bpm_from_label(mm.text or "")
            is_auto = True

        events.append(TempoEvent(
            tempo_event_id=f"tempo_{idx}",
            bar=bar,
            beat=beat,
            time_onset=time_onset,
            bpm=bpm,
            beat_unit=_beat_unit_from_referent(mm.referent),
            transition_type=TransitionTypeEnum.IMMEDIATE,
            notation_label=mm.text,
            is_auto_analyzed=is_auto,
        ))

    return sorted(events, key=lambda e: e.time_onset)


def tempo_event_from_m21(mm, idx: int, time_onset: float, bar: int, beat: int) -> TempoEvent:
    """Construct a TempoEvent from a music21 MetronomeMark (used by the classmethod)."""
    if mm.number is not None and mm.number > 0:
        bpm = float(mm.getQuarterBPM())
        is_auto = False
    else:
        bpm = _bpm_from_label(mm.text or "")
        is_auto = True
    return TempoEvent(
        tempo_event_id=f"tempo_{idx}",
        bar=bar, beat=beat,
        time_onset=time_onset,
        bpm=bpm,
        beat_unit=_beat_unit_from_referent(mm.referent),
        transition_type=TransitionTypeEnum.IMMEDIATE,
        notation_label=mm.text,
        is_auto_analyzed=is_auto,
    )

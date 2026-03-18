"""
Dynamic Extractor — Step 5 of the parsing pipeline.
Extracts DynamicSpan objects from hairpin markings.
Stamps dynamic_span_id and dynamic_span_position on affected NoteObjects.
"""
from __future__ import annotations

import music21
from music21 import dynamics as m21_dynamics

from ..models.score_data import DynamicSpan, NoteObject
from ..models.enums import SpanTypeEnum, DynamicMarkingEnum, ShapeEnum
from .tempo_extractor import ql_to_seconds

# Map music21 dynamic string to DynamicMarkingEnum
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

# Dynamic level ordering (for inferring ±1 step)
DYNAMIC_LEVELS = [
    DynamicMarkingEnum.PPP, DynamicMarkingEnum.PP, DynamicMarkingEnum.P,
    DynamicMarkingEnum.MP, DynamicMarkingEnum.MF,
    DynamicMarkingEnum.F, DynamicMarkingEnum.FF, DynamicMarkingEnum.FFF,
]


def _step_up(d: DynamicMarkingEnum) -> DynamicMarkingEnum:
    try:
        idx = DYNAMIC_LEVELS.index(d)
        return DYNAMIC_LEVELS[min(idx + 1, len(DYNAMIC_LEVELS) - 1)]
    except ValueError:
        return DynamicMarkingEnum.F


def _step_down(d: DynamicMarkingEnum) -> DynamicMarkingEnum:
    try:
        idx = DYNAMIC_LEVELS.index(d)
        return DYNAMIC_LEVELS[max(idx - 1, 0)]
    except ValueError:
        return DynamicMarkingEnum.P


def _find_nearest_dynamic(score: music21.stream.Score, part_id: str,
                          offset: float, direction: str = "before") -> DynamicMarkingEnum:
    """Find the nearest explicit dynamic marking before/after a given offset."""
    try:
        for part in score.parts:
            if part.id != part_id:
                continue
            flat = part.flatten()
            dyns = list(flat.getElementsByClass(m21_dynamics.Dynamic))
            if direction == "before":
                candidates = [d for d in dyns if float(d.offset) <= offset]
                if candidates:
                    nearest = max(candidates, key=lambda d: float(d.offset))
                    return M21_DYNAMIC_MAP.get(nearest.value, DynamicMarkingEnum.MF)
            else:
                candidates = [d for d in dyns if float(d.offset) >= offset]
                if candidates:
                    nearest = min(candidates, key=lambda d: float(d.offset))
                    return M21_DYNAMIC_MAP.get(nearest.value, DynamicMarkingEnum.MF)
    except Exception:
        pass
    return DynamicMarkingEnum.MF


def _offset_to_bar_beat(score: music21.stream.Score, part_id: str, offset: float):
    try:
        for part in score.parts:
            if part.id != part_id:
                continue
            for m in part.getElementsByClass(music21.stream.Measure):
                m_start = float(m.offset)
                m_end = m_start + float(m.barDuration.quarterLength)
                if m_start <= offset < m_end:
                    ts = m.timeSignature
                    beat_ql = float(ts.beatDuration.quarterLength) if ts else 1.0
                    beat = int((offset - m_start) / beat_ql) + 1
                    return m.number or 1, beat
    except Exception:
        pass
    return 1, 1


def extract_dynamic_spans(score: music21.stream.Score, instruments,
                          boundaries: list) -> list[DynamicSpan]:
    """Extract all hairpin DynamicSpan objects from the score."""
    spans = []
    span_counter = 0

    for part in score.parts:
        part_id = part.id
        # Match to our instrument_id
        instrument_id = part_id  # instrument_id == part.id (set during instrument extraction)

        # Collect crescendo/diminuendo spanners from this part
        try:
            sb = part.spannerBundle
            hairpins = list(sb.getByClass(m21_dynamics.Crescendo)) + \
                       list(sb.getByClass(m21_dynamics.Diminuendo))
        except Exception:
            continue

        for hairpin in hairpins:
            try:
                spanned = hairpin.getSpannedElements()
                if not spanned:
                    continue

                first = spanned[0]
                last = spanned[-1]

                start_offset = float(first.offset)
                end_offset = float(last.offset) + float(last.quarterLength)

                start_time = ql_to_seconds(start_offset, boundaries)
                end_time = ql_to_seconds(end_offset, boundaries)
                duration = max(end_time - start_time, 0.001)

                span_type = (SpanTypeEnum.CRESCENDO
                             if isinstance(hairpin, m21_dynamics.Crescendo)
                             else SpanTypeEnum.DIMINUENDO)

                # Infer dynamic_start and dynamic_end
                dyn_start = _find_nearest_dynamic(score, part_id, start_offset, "before")
                if span_type == SpanTypeEnum.CRESCENDO:
                    dyn_end_default = _step_up(dyn_start)
                else:
                    dyn_end_default = _step_down(dyn_start)
                dyn_end = _find_nearest_dynamic(score, part_id, end_offset, "after")
                if dyn_end == DynamicMarkingEnum.MF and dyn_end == dyn_start:
                    dyn_end = dyn_end_default
                    is_auto = True
                else:
                    is_auto = False

                start_bar, start_beat = _offset_to_bar_beat(score, part_id, start_offset)
                end_bar, end_beat = _offset_to_bar_beat(score, part_id, end_offset)

                span = DynamicSpan(
                    span_id=f"span_{span_counter}",
                    span_type=span_type,
                    instrument_id=instrument_id,
                    start_bar=start_bar,
                    start_beat=start_beat,
                    end_bar=end_bar,
                    end_beat=end_beat,
                    time_onset=start_time,
                    time_duration=duration,
                    dynamic_start=dyn_start,
                    dynamic_end=dyn_end,
                    shape=ShapeEnum.LINEAR,
                    is_auto_analyzed=is_auto,
                )
                spans.append(span)
                span_counter += 1
            except Exception:
                continue

    return sorted(spans, key=lambda s: s.time_onset)


def stamp_dynamic_spans(notes: list[NoteObject], spans: list[DynamicSpan]) -> None:
    """
    Pre-compute dynamic_span_id and dynamic_span_position on each NoteObject
    that falls within a DynamicSpan. Modifies notes in-place.
    """
    for span in spans:
        span_end = span.time_onset + span.time_duration
        for note in notes:
            if note.instrument_id != span.instrument_id:
                continue
            if span.time_onset <= note.time_onset < span_end and span.time_duration > 0:
                note.dynamic_span_id = span.span_id
                note.dynamic_span_position = (note.time_onset - span.time_onset) / span.time_duration


def dynamic_span_from_hairpin(hairpin, part, instrument_id: str,
                               span_id: str, boundaries: list) -> DynamicSpan:
    """Used by DynamicSpan.from_xml() classmethod."""
    spanned = hairpin.getSpannedElements()
    first = spanned[0] if spanned else None
    last = spanned[-1] if spanned else None
    start_offset = float(first.offset) if first else 0.0
    end_offset = float(last.offset) + float(last.quarterLength) if last else 1.0

    start_time = ql_to_seconds(start_offset, boundaries)
    end_time = ql_to_seconds(end_offset, boundaries)

    span_type = (SpanTypeEnum.CRESCENDO
                 if isinstance(hairpin, m21_dynamics.Crescendo)
                 else SpanTypeEnum.DIMINUENDO)

    return DynamicSpan(
        span_id=span_id,
        span_type=span_type,
        instrument_id=instrument_id,
        start_bar=1, start_beat=1, end_bar=1, end_beat=4,
        time_onset=start_time,
        time_duration=max(end_time - start_time, 0.001),
        dynamic_start=DynamicMarkingEnum.P,
        dynamic_end=DynamicMarkingEnum.F,
        shape=ShapeEnum.LINEAR,
        is_auto_analyzed=True,
    )

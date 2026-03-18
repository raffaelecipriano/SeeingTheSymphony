"""
Auto-Annotator — Step 6 of the parsing pipeline.
Infers attack, release, and technique from articulation markings and score context.
All inferences are already embedded in NoteObjects during note extraction;
this pass can make a second sweep for context-dependent inferences.
"""
from __future__ import annotations

from ..models.score_data import NoteObject
from ..models.enums import AttackEnum, ReleaseEnum, ArticulationEnum, TechniqueEnum


def auto_annotate_notes(notes: list[NoteObject]) -> None:
    """
    Context-dependent annotation sweep.
    Derives attack/release from articulation if not already set.
    Modifies notes in-place.
    """
    for note in notes:
        arts = note.articulation

        # If already non-default (set during per-note extraction), skip
        if note.attack != AttackEnum.NORMAL or note.release != ReleaseEnum.NORMAL:
            continue

        if ArticulationEnum.STACCATO in arts:
            note.attack = AttackEnum.SHARP
            note.release = ReleaseEnum.CLIPPED
        elif ArticulationEnum.MARCATO in arts:
            note.attack = AttackEnum.SHARP
            note.release = ReleaseEnum.NORMAL
        elif ArticulationEnum.ACCENT in arts:
            note.attack = AttackEnum.SHARP
            note.release = ReleaseEnum.NORMAL
        elif ArticulationEnum.TENUTO in arts:
            note.attack = AttackEnum.SOFT
            note.release = ReleaseEnum.TAPERED
        elif ArticulationEnum.LEGATO in arts:
            note.attack = AttackEnum.SOFT
            note.release = ReleaseEnum.TAPERED
        elif ArticulationEnum.PORTATO in arts:
            note.attack = AttackEnum.NORMAL
            note.release = ReleaseEnum.CLIPPED

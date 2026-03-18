from enum import Enum


class DynamicMarkingEnum(str, Enum):
    PPP = "PPP"
    PP = "PP"
    P = "P"
    MP = "MP"
    MF = "MF"
    F = "F"
    FF = "FF"
    FFF = "FFF"
    FP = "FP"


class SforzandoEnum(str, Enum):
    SF = "SF"
    FZ = "FZ"
    SFZ = "SFZ"
    FZFZ = "FZFZ"
    SFFZ = "SFFZ"


class ArticulationEnum(str, Enum):
    NORMAL = "NORMAL"
    LEGATO = "LEGATO"
    STACCATO = "STACCATO"
    MARCATO = "MARCATO"
    TENUTO = "TENUTO"
    ACCENT = "ACCENT"
    TREMOLO = "TREMOLO"
    PORTATO = "PORTATO"


class TechniqueEnum(str, Enum):
    NORMAL = "NORMAL"
    PIZZICATO = "PIZZICATO"
    COL_LEGNO = "COL_LEGNO"
    SUL_PONTICELLO = "SUL_PONTICELLO"
    HARMONICS = "HARMONICS"
    SUL_TASTO = "SUL_TASTO"
    TREMOLO_BOW = "TREMOLO_BOW"
    SNAP_PIZZICATO = "SNAP_PIZZICATO"
    FLAUTANDO = "FLAUTANDO"


class AttackEnum(str, Enum):
    NORMAL = "NORMAL"
    SHARP = "SHARP"
    SOFT = "SOFT"


class ReleaseEnum(str, Enum):
    NORMAL = "NORMAL"
    CLIPPED = "CLIPPED"
    TAPERED = "TAPERED"


class InstrumentFamilyEnum(str, Enum):
    STRINGS = "STRINGS"
    WOODWINDS = "WOODWINDS"
    BRASS = "BRASS"
    PERCUSSION = "PERCUSSION"
    KEYBOARD = "KEYBOARD"
    VOICE = "VOICE"


class KeyModeEnum(str, Enum):
    MAJOR = "MAJOR"
    MINOR = "MINOR"


class ShapeEnum(str, Enum):
    LINEAR = "LINEAR"
    CONVEX = "CONVEX"
    CONCAVE = "CONCAVE"


class SpanTypeEnum(str, Enum):
    CRESCENDO = "CRESCENDO"
    DIMINUENDO = "DIMINUENDO"


class StrategyEnum(str, Enum):
    EXPAND = "EXPAND"
    ALTERNATE = "ALTERNATE"
    BLEND = "BLEND"


class BeatUnitEnum(str, Enum):
    QUARTER = "QUARTER"
    HALF = "HALF"
    EIGHTH = "EIGHTH"
    DOTTED_QUARTER = "DOTTED_QUARTER"
    DOTTED_HALF = "DOTTED_HALF"


class TransitionTypeEnum(str, Enum):
    IMMEDIATE = "IMMEDIATE"
    GRADUAL = "GRADUAL"


class VerticalModeEnum(str, Enum):
    PITCH_BASED = "PITCH_BASED"
    SCORE_BASED = "SCORE_BASED"


class TemplateEnum(str, Enum):
    PIANO_ROLL = "PIANO_ROLL"
    SPIRAL = "SPIRAL"


class ValidationLevelEnum(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    SUGGESTION = "SUGGESTION"

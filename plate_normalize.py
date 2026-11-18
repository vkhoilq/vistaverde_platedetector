"""License-plate string normalization and confusion-aware fuzzy matching.

Everything in this module works on the *canonical* form of a plate:
upper-case ASCII letters and digits only (``51F-123.45`` -> ``51F12345``).
"""

from __future__ import annotations

__all__ = [
    "canonical",
    "format_vn",
    "similarity",
    "is_close",
    "distance",
]

_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")

# Characters OCR engines routinely confuse when reading a plate.  Each pair is
# unordered; substituting one for the other costs half a regular edit.
_CONFUSION_PAIRS = (
    "0O",
    "1I",
    "1L",
    "2Z",
    "5S",
    "6G",
    "8B",
    "D0",
    "QO",
    "UV",
    "4A",
)
_CONFUSIONS = frozenset(frozenset(pair) for pair in _CONFUSION_PAIRS)

CONFUSION_COST = 0.5
EDIT_COST = 1.0


def canonical(plate: str) -> str:
    """Uppercase, Đ/đ -> D, keep only A-Z and 0-9. Returns '' for empty input."""
    if not plate:
        return ""
    text = str(plate).upper().replace("Đ", "D")
    return "".join(ch for ch in text if ch in _ALPHABET)


def format_vn(plate: str) -> str:
    """Human-readable Vietnamese-style display form of ``plate``."""
    p = canonical(plate)
    if len(p) == 8:
        return f"{p[:3]}-{p[3:6]}.{p[6:8]}"
    if len(p) == 9:
        return f"{p[:4]}-{p[4:7]}.{p[7:9]}"
    return p


def is_confusable(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` are a known visually-confusable character pair."""
    if a == b:
        return False
    return frozenset((a, b)) in _CONFUSIONS


def _distance_raw(a: str, b: str) -> float:
    """Weighted Levenshtein distance between two already-canonical strings."""
    if a == b:
        return 0.0
    la, lb = len(a), len(b)
    if la == 0:
        return float(lb)
    if lb == 0:
        return float(la)

    previous = [float(j) for j in range(lb + 1)]
    for i in range(1, la + 1):
        current = [float(i)] + [0.0] * lb
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cb = b[j - 1]
            if ca == cb:
                substitution = previous[j - 1]
            elif is_confusable(ca, cb):
                substitution = previous[j - 1] + CONFUSION_COST
            else:
                substitution = previous[j - 1] + EDIT_COST
            current[j] = min(
                substitution,
                previous[j] + EDIT_COST,  # deletion
                current[j - 1] + EDIT_COST,  # insertion
            )
        previous = current
    return previous[lb]


def distance(a: str, b: str) -> float:
    """Confusion-aware edit distance between two plates (inputs are canonicalized)."""
    return _distance_raw(canonical(a), canonical(b))


def similarity(a: str, b: str) -> float:
    """0.0 - 1.0 confusion-aware similarity. 1.0 means identical canonical forms."""
    ca, cb = canonical(a), canonical(b)
    if not ca and not cb:
        return 1.0
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    score = 1.0 - _distance_raw(ca, cb) / max(len(ca), len(cb))
    return max(0.0, min(1.0, score))


def is_close(a: str, b: str, max_distance: int = 1) -> bool:
    """True when the confusion-aware weighted distance is <= ``max_distance``."""
    return distance(a, b) <= max_distance

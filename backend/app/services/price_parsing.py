"""Shared helpers for turning noisy spoken-price strings into numbers.

Vendor replies on calls arrive as messy strings like "52,000 है।",
"₹52000 rupaye", or with Devanagari numerals like "१२३४५". A single
normalizer keeps every consumer (slot extraction, outcome classification,
persistence) consistent instead of each re-implementing a regex.
"""
from __future__ import annotations

import re
from typing import Any

# Devanagari → ASCII digit map.
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def parse_price(value: Any) -> float | None:
    """Best-effort numeric price from a raw value.

    Returns a positive float, or None when no usable number is present.
    Accepts ints/floats directly and pulls the first number out of strings
    after normalizing Devanagari digits and stripping thousands separators.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if float(value) > 0 else None
    if not isinstance(value, str):
        return None

    cleaned = value.translate(_DEVANAGARI_DIGITS).replace(",", "")
    match = _NUMBER_RE.search(cleaned)
    if not match:
        return None
    try:
        parsed = float(match.group())
    except ValueError:
        return None
    return parsed if parsed > 0 else None

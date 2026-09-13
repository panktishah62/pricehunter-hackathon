"""Variant specs from intake conversations and numeric-aware title matching.

Phase 1: enrich StructuredQuery.search_specs from Priya conversation.
Phase 2: score Algolia/Mongo results so e.g. 10000 mAh power banks rank above
1000 mAh when the buyer asked for 10000.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SPLIT_RE = re.compile(r"[^a-z0-9]+")

# Number glued or spaced with a unit (10000mAh, 10,000 mAh, 256GB).
_NUM_UNIT_RE = re.compile(
    r"(?i)(\d[\d,]*\.?\d*)\s*"
    r"(mah|ma|ah|gb|tb|mb|kb|kg|g|gm|gram|grams|mg|ml|l|litre|liter|"
    r"w|watt|watts|kw|v|volt|volts|mm|cm|m|inch|inches|in|rpm|kva|hp|ton|tons)?"
)

# Standalone numbers the buyer likely meant as a spec (capacity, storage, model).
_STANDALONE_NUM_RE = re.compile(r"(?<!\d)(\d[\d,]{2,})(?!\d)")

_UNIT_ALIASES = {
    "ma": "mah",
    "mah": "mah",
    "ah": "ah",
    "gb": "gb",
    "tb": "tb",
    "mb": "mb",
    "kb": "kb",
    "kg": "kg",
    "g": "g",
    "gm": "gm",
    "gram": "g",
    "grams": "g",
    "mg": "mg",
    "ml": "ml",
    "l": "l",
    "litre": "l",
    "liter": "l",
    "w": "w",
    "watt": "w",
    "watts": "w",
    "kw": "kw",
    "v": "v",
    "volt": "v",
    "volts": "v",
    "mm": "mm",
    "cm": "cm",
    "m": "m",
    "inch": "in",
    "inches": "in",
    "in": "in",
    "rpm": "rpm",
    "kva": "kva",
    "hp": "hp",
    "ton": "ton",
    "tons": "ton",
}

# Words that signal a bare number is a product spec, not quantity/noise.
_SPEC_ASK = re.compile(
    r"\b(capacity|mah|storage|memory|gb|model|brand|variant|size|watt|voltage|"
    r"color|which|what kind|what type)\b",
    re.I,
)

_STOP_SPEC_TOKENS = {
    "the", "a", "an", "in", "at", "for", "and", "or", "need", "want", "looking",
    "please", "yes", "ok", "okay", "hi", "hello", "thanks", "delhi", "mumbai",
    "bangalore", "bengaluru", "ahmedabad", "chennai", "kolkata", "hyderabad",
    "pune", "jaipur", "surat", "india",
}


@dataclass(frozen=True)
class NumericSpec:
    value: float
    unit: str | None
    raw: str


def _normalize_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    return _UNIT_ALIASES.get(unit.lower().strip(), unit.lower().strip())


def _parse_float(num_text: str) -> float | None:
    cleaned = (num_text or "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_numeric_specs(phrases: list[str] | tuple[str, ...] | None) -> list[NumericSpec]:
    """Pull numeric+unit specs from phrases like '10000 mAh' or '256GB'."""
    specs: list[NumericSpec] = []
    seen: set[tuple[float, str | None]] = set()
    for phrase in phrases or []:
        text = str(phrase or "").strip()
        if not text:
            continue
        for match in _NUM_UNIT_RE.finditer(text):
            value = _parse_float(match.group(1))
            if value is None:
                continue
            unit = _normalize_unit(match.group(2))
            key = (value, unit)
            if key in seen:
                continue
            seen.add(key)
            specs.append(NumericSpec(value=value, unit=unit, raw=match.group(0).strip()))
        # Bare number when no unit match consumed the whole phrase.
        if not specs and text.replace(",", "").isdigit():
            value = _parse_float(text)
            if value is not None:
                key = (value, None)
                if key not in seen:
                    seen.add(key)
                    specs.append(NumericSpec(value=value, unit=None, raw=text))
    return specs


def extract_specs_from_text(text: str) -> list[str]:
    """Deterministic spec harvest from free text (user messages)."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _NUM_UNIT_RE.finditer(text or ""):
        snippet = match.group(0).strip()
        key = snippet.lower()
        if key not in seen:
            seen.add(key)
            found.append(snippet)
    if _SPEC_ASK.search(text or ""):
        for match in _STANDALONE_NUM_RE.finditer(text or ""):
            snippet = match.group(1).strip()
            key = snippet.lower()
            if key not in seen:
                seen.add(key)
                found.append(snippet)
    return found


def _user_messages(history: list[dict[str, str]] | None) -> list[str]:
    return [
        str(item.get("content") or "").strip()
        for item in (history or [])
        if str(item.get("role") or "").lower() == "user" and str(item.get("content") or "").strip()
    ]


def enrich_search_specs(
    specs: list[str],
    *,
    conversation_history: list[dict[str, str]] | None = None,
    product_attributes: dict[str, str] | None = None,
    quantity: str | None = None,
) -> list[str]:
    """Merge LLM-extracted specs with conversation-derived and image attributes."""
    merged: list[str] = []
    seen: set[str] = set()

    def _add(item: str) -> None:
        text = str(item or "").strip()
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        merged.append(text)

    for item in specs or []:
        _add(item)

    qty_norm = (quantity or "").strip().lower()
    history = conversation_history or []
    for index, item in enumerate(history):
        role = str(item.get("role") or "").lower()
        if role != "user":
            continue
        message = str(item.get("content") or "").strip()
        if not message:
            continue
        prev = history[index - 1] if index > 0 else None
        prev_assistant = (
            str((prev or {}).get("role") or "").lower() == "assistant"
            and bool(_SPEC_ASK.search(str((prev or {}).get("content") or "")))
        )
        for snippet in extract_specs_from_text(message):
            if qty_norm and snippet.lower() in qty_norm:
                continue
            _add(snippet)
        if prev_assistant:
            for match in _STANDALONE_NUM_RE.finditer(message):
                snippet = match.group(1).strip()
                if qty_norm and snippet.lower() in qty_norm:
                    continue
                _add(snippet)
            # Short replies often mix specs + location ("1L, abad") — only keep
            # clean numeric/unit snippets, never the whole comma-joined line.
            if (
                len(message.split()) <= 4
                and not message.replace(",", "").isdigit()
                and "," not in message
                and not any(
                    token in {
                        "abad",
                        "ahmedabad",
                        "mumbai",
                        "delhi",
                        "rajkot",
                        "bangalore",
                        "bengaluru",
                        "pune",
                        "surat",
                    }
                    for token in message.lower().replace(",", " ").split()
                )
            ):
                _add(message)

    for key, value in (product_attributes or {}).items():
        if key and value:
            _add(f"{key}: {value}")
            _add(str(value))

    return merged


def _tokens(text: str) -> set[str]:
    return {token for token in _SPLIT_RE.split((text or "").lower()) if len(token) >= 2}


def _text_spec_penalty(
    search_specs: list[str] | tuple[str, ...] | None,
    descriptors: list[str] | tuple[str, ...] | None,
    title: str,
) -> float:
    """0 = strong token overlap for non-numeric specs; 1 = no overlap."""
    phrases = [*(search_specs or []), *(descriptors or [])]
    spec_tokens: set[str] = set()
    for phrase in phrases:
        for token in _tokens(phrase):
            if token.isdigit() or token in _STOP_SPEC_TOKENS:
                continue
            spec_tokens.add(token)
    if not spec_tokens:
        return 0.0
    title_tokens = _tokens(title)
    if not title_tokens:
        return 1.0
    overlap = len(spec_tokens & title_tokens) / len(spec_tokens)
    return 1.0 - overlap


def numeric_spec_penalty(
    search_specs: list[str] | tuple[str, ...] | None,
    title: str,
) -> float:
    """0 = best numeric match; 1 = poor/no match. Lower is better for sorting."""
    query_specs = parse_numeric_specs(search_specs or [])
    if not query_specs:
        return 0.0

    title_specs = parse_numeric_specs([title])
    if not title_specs:
        # Also scan glued forms like 10000mAh inside the raw title.
        title_specs = [
            NumericSpec(value=item.value, unit=item.unit, raw=item.raw)
            for item in parse_numeric_specs(extract_specs_from_text(title))
        ]
    if not title_specs:
        return 0.55

    penalties: list[float] = []
    for query in query_specs:
        best_ratio = 0.0
        for candidate in title_specs:
            if query.unit and candidate.unit and query.unit != candidate.unit:
                continue
            if query.value <= 0 or candidate.value <= 0:
                continue
            ratio = min(query.value, candidate.value) / max(query.value, candidate.value)
            best_ratio = max(best_ratio, ratio)
        if best_ratio >= 0.98:
            penalties.append(0.0)
        elif best_ratio >= 0.85:
            penalties.append(0.15)
        elif best_ratio >= 0.5:
            penalties.append(0.45)
        elif best_ratio > 0:
            penalties.append(0.85)
        else:
            penalties.append(0.65)
    return sum(penalties) / len(penalties)


def rank_key(
    query_product: str,
    product_title: str,
    *,
    descriptors: list[str] | tuple[str, ...] | None = None,
    search_specs: list[str] | tuple[str, ...] | None = None,
    match_tier_fn,
) -> tuple[int, float, float]:
    """Sort key: (relevance tier, numeric penalty, text penalty)."""
    tier = match_tier_fn(
        query_product,
        product_title,
        descriptors=[*(descriptors or []), *(search_specs or [])] or None,
    )
    num_penalty = numeric_spec_penalty(search_specs, product_title)
    text_penalty = _text_spec_penalty(search_specs, descriptors, product_title)
    return (tier, num_penalty, text_penalty)


def optional_search_tokens(
    descriptors: list[str] | None,
    search_specs: list[str] | None,
) -> tuple[str, ...]:
    """Flatten descriptors + specs into Algolia optionalWords tokens."""
    tokens: list[str] = []
    seen: set[str] = set()
    for phrase in [*(descriptors or []), *(search_specs or [])]:
        for token in _SPLIT_RE.split((phrase or "").lower()):
            if len(token) < 2 or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
        # Keep multi-word specs intact as optional phrases when short.
        compact = (phrase or "").strip().lower()
        if compact and " " in compact and compact not in seen:
            seen.add(compact)
            tokens.append(compact)
    return tuple(tokens)

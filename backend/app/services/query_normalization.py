"""Search-text normalization for Algolia queries.

Two catalog-vocabulary gaps this papers over:

1. Plurals. `ignorePlurals: ["en"]` is set on the index but demonstrably does
   not merge forms for this catalog ("cartons" -> ~40 hits, "carton" ->
   ~1,000+), so query words are singularized in code before searching.
2. Compounds. Buyers type "monocartons" as one word while the catalog indexes
   "mono cartons" as two. `split_compounds` produces a spaced variant that
   callers use as a fallback when the original query returns (near-)zero hits.
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[A-Za-z]+")

# Words that end in "s" (or "ies") but are not plurals of catalog nouns.
_NOT_PLURAL = {
    "series", "species", "news", "lens", "gas", "brass", "glass", "ss",
    "stainless", "wireless", "cordless", "plus", "ups", "gps", "sos", "abs",
}

# One-word compound prefixes buyers glue onto product nouns.
_COMPOUND_PREFIXES = (
    "mono", "multi", "micro", "mini", "poly", "semi", "ultra", "anti",
)


def singularize_word(word: str) -> str:
    lower = word.lower()
    if len(lower) <= 3 or lower in _NOT_PLURAL or lower.endswith(("ss", "us", "is")):
        return word
    if lower.endswith("ies") and len(lower) > 4:
        return word[:-3] + "y"
    if lower.endswith(("sses", "shes", "ches", "xes", "zes")):
        return word[:-2]
    if lower.endswith("s"):
        return word[:-1]
    return word


def singularize_phrase(text: str) -> str:
    """Singularize every alphabetic word in a query phrase, preserving the
    rest of the text (numbers, hyphens, spacing) untouched."""
    return _WORD_RE.sub(lambda match: singularize_word(match.group(0)), text or "")


def _split_compound_word(word: str) -> str | None:
    lower = word.lower()
    for prefix in _COMPOUND_PREFIXES:
        remainder = lower[len(prefix):]
        if lower.startswith(prefix) and len(remainder) >= 4:
            return f"{word[:len(prefix)]} {word[len(prefix):]}"
    return None


def split_compounds(text: str) -> str | None:
    """Return a variant with glued compounds split ("monocarton" ->
    "mono carton"), or ``None`` when no word changes. Meant as a fallback
    query, not a default: only try it when the original text found nothing."""
    changed = False

    def _replace(match: re.Match[str]) -> str:
        nonlocal changed
        split = _split_compound_word(match.group(0))
        if split is None:
            return match.group(0)
        changed = True
        return split

    result = _WORD_RE.sub(_replace, text or "")
    return result if changed else None


def compound_root(token: str) -> str:
    """Strip a known compound prefix from a single token ("monocarton" ->
    "carton") for loose head-noun comparison."""
    split = _split_compound_word(token)
    return split.split(" ", 1)[1] if split else token
